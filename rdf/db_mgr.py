"""Data manager — loads TTL files, runs RDFS inference, SKOS rules, and policy.

Load sequence:
  1. Parse TTL files into discrete stores (instances, tags, vocab, schema)
  2. Merge into combined data store
  3. RDFS inference via owlrl
  4. SKOS rules (inverse broader, transitive broader, symmetric related)
  5. Policy SPARQL UPDATEs
  6. SHACL validation (log only)
"""

import os
import shutil
import time
from pathlib import Path

import owlrl
from rdflib import Graph, Literal, XSD
from rdflib.namespace import SKOS

from server import Server
from rdf import policy as Policy
from rdf.prefixes import bind_standard_prefixes
from util import gcp

LOCK_FILE = ".loadLock"


# ---------------------------------------------------------------------------
# Load lock — prevents concurrent reloads
# ---------------------------------------------------------------------------

def _lock_path(cfg: dict) -> Path:
    return Path(cfg.get("dir", ".")) / LOCK_FILE


def _acquire_lock(cfg: dict):
    lp = _lock_path(cfg)
    while lp.exists():
        Server.log_out("Waiting for load lock...")
        time.sleep(1)
    lp.write_text("locked")


def _release_lock(cfg: dict):
    _lock_path(cfg).unlink(missing_ok=True)


def _clear_gcp_folders(cfg: dict):
    """Delete local TTL files in the GCP-synced folders before sync.

    Ensures local state mirrors the bucket exactly — deletions and
    renames in the bucket propagate to the local store on the next load
    or refresh.

    Always clears data/ and tags/ — the bucket is the sole source of
    truth for those. Also clears model/ and vocab/ when referenceModel
    is null, meaning the bucket is the source of truth for those too.
    When referenceModel is set (CE bootstrap), model/ and vocab/ are
    left alone — they are populated by the reference fetch (run earlier
    in _load), not the bucket, so clearing them here would destroy them
    permanently.

    Call this only when cloud config is set and a bucket sync is
    guaranteed to follow — never for local-only deployments, where
    clearing would destroy the user's data.
    """
    folders = ["data", "tags"]
    if not cfg.get("referenceModel"):
        folders += ["model", "vocab"]

    for key in folders:
        folder = Path(cfg[key])
        if folder.exists():
            shutil.rmtree(folder)
            folder.mkdir(parents=True)
            Server.log_out(f"[sync] cleared {folder}")


# ---------------------------------------------------------------------------
# TTL file loader
# ---------------------------------------------------------------------------

def load_files(directory: str) -> Graph:
    """Load all TTL files from directory (recursively) into a single Graph."""
    g = Graph()
    path = Path(directory)
    if not path.exists():
        Server.log_out(f"WARNING: directory not found: {directory}")
        return g
    files = sorted(path.rglob("*.ttl"))
    for ttl_file in files:
        try:
            g.parse(str(ttl_file), format="turtle")
        except Exception as e:
            try:
                content = ttl_file.read_bytes().decode("latin-1")
                g.parse(data=content, format="turtle")
                Server.log_out(f"  [latin-1 fallback] {ttl_file.name}")
            except Exception as e2:
                Server.log_err(f"ERROR loading {ttl_file}: {e2}")
    return g


# ---------------------------------------------------------------------------
# SKOS inference rules
# ---------------------------------------------------------------------------

def apply_skos_rules(g: Graph):
    """Apply SKOS inference as explicit triple additions.

    Rules implemented (from skos.rules):
      1. inverseRule:    (?A broader ?B) -> (?B narrower ?A)
      2. transitiveRule: (?A broader ?B), (?B broader ?C) -> (?A broader ?C)
      3. symmetricRule:  (?Y related ?X) -> (?X related ?Y)

    The skos:related transitivity rule is intentionally omitted.
    """
    BROADER  = SKOS.broader
    NARROWER = SKOS.narrower
    RELATED  = SKOS.related

    # Pass 1: inverse — broader → narrower
    for a, b in list(g.subject_objects(BROADER)):
        g.add((b, NARROWER, a))

    # Pass 2: transitive broader — iterate to fixpoint
    changed = True
    while changed:
        changed = False
        new = []
        for a, b in g.subject_objects(BROADER):
            for c in g.objects(b, BROADER):
                if (a, BROADER, c) not in g:
                    new.append((a, BROADER, c))
                    changed = True
        for triple in new:
            g.add(triple)

    # Pass 3: symmetric related
    for y, x in list(g.subject_objects(RELATED)):
        g.add((x, RELATED, y))


# ---------------------------------------------------------------------------
# Boolean datatype cleanup (Jena-compatibility)
# ---------------------------------------------------------------------------

def _clean_boolean_expansion(g: Graph):
    """Remove spurious XSD numeric shadows of boolean literals.

    owlrl's RDFS datatype reasoning materializes the value 1/0 typed as
    BOTH xsd:integer and xsd:int for every xsd:boolean true/false, because
    xsd:boolean's lexical space admits "1"/"0". The behaviour is triggered
    by the `rdfs:range xsd:int` declarations in the data (strutCount,
    memberCount, cableCount, vertexCount) and then applies graph-wide.
    Jena did not do this, so the Groovy server emitted no such duplicates.

    Anchored on the boolean: a numeric literal is removed only when it sits
    on the same subject/predicate as a matching boolean (true→1, false→0).
    Genuine integer values (the:value, the:position, the:strutCount, ...)
    have no boolean sibling and are never touched.
    """
    to_remove = []
    for s, p, o in g.triples((None, None, None)):
        if isinstance(o, Literal) and o.datatype == XSD.boolean:
            num = 1 if o.value else 0
            for dt in (XSD.integer, XSD.int):
                shadow = Literal(num, datatype=dt)
                if (s, p, shadow) in g:
                    to_remove.append((s, p, shadow))
    for triple in to_remove:
        g.remove(triple)
    if to_remove:
        Server.log_out(
            f"  [cleanup] removed {len(to_remove)} spurious XSD boolean expansions"
        )


# ---------------------------------------------------------------------------
# SHACL validation (optional — log only)
# ---------------------------------------------------------------------------

def _validate(g: Graph, cfg_dir: str):
    """Run SHACL validation against any *.shacl files in res/. Never aborts."""
    shacl_files = list(Path(cfg_dir).glob("res/*.shacl"))
    if not shacl_files:
        return
    try:
        import pyshacl
    except ImportError:
        Server.log_out("pyshacl not installed — skipping SHACL validation")
        return

    all_pass = True
    for f in shacl_files:
        shapes = Graph()
        try:
            shapes.parse(str(f), format="turtle")
        except Exception as e:
            Server.log_out(f"SHACL syntax error in {f.name}: {e}")
            continue
        try:
            conforms, _, report = pyshacl.validate(g, shacl_graph=shapes)
            if not conforms:
                all_pass = False
                Server.log_out(f"SHACL violations in {f.name}:\n{report}")
        except Exception as e:
            Server.log_out(f"SHACL validation error in {f.name}: {e}")

    if all_pass:
        Server.log_out("SHACL validation passed.")


# ---------------------------------------------------------------------------
# DBMgr
# ---------------------------------------------------------------------------

class DBMgr:
    """Loads and owns all RDF stores. Instantiated once at server startup."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.instances: Graph = None
        self.tags:      Graph = None
        self.vocab:     Graph = None
        self.schema:    Graph = None
        self.data:      Graph = None   # instances + tags + vocab
        self.rdfs:      Graph = None   # inferred store — primary query target
        self._load()

    def print_stats(self):
        Server.log_out(
            f"DBMgr stores — instances:{len(self.instances)} tags:{len(self.tags)} "
            f"vocab:{len(self.vocab)} schema:{len(self.schema)} "
            f"data:{len(self.data)} rdfs:{len(self.rdfs)}"
        )

    def _load(self):
        cfg = self.cfg
        cfg_dir = cfg.get("dir", ".")
        srv = Server.get_instance()

        _acquire_lock(cfg)
        try:
            t0 = time.time()

            if cfg.get("referenceModel"):
                from util import reference
                reference.fetch_model(
                    cfg["referenceModel"],
                    cfg["model"],
                    cfg.get("vocab"),
                )

            if cfg.get("cloud") and os.environ.get("GCP_BUCKET"):
                _clear_gcp_folders(cfg)
                Server.log_out("Syncing TTL files from GCP bucket...")
                path_map = {
                    "model": cfg["model"],
                    "vocab": cfg["vocab"],
                    "data":  cfg["data"],
                    "tags":  cfg["tags"],
                }
                gcp.gcp_cp_dir_recurse(
                    cfg["cloud"]["src"],
                    path_map,
                    clobber=cfg.get("clobber", False),
                    multithreaded=cfg.get("multithreaded", False),
                )

            srv.verbose_log("Loading instance TTL files...")
            self.instances = load_files(cfg["data"])
            srv.verbose_log(f"  instances: {len(self.instances)} triples")

            srv.verbose_log("Loading tag TTL files...")
            self.tags = load_files(cfg["tags"])
            srv.verbose_log(f"  tags: {len(self.tags)} triples")

            srv.verbose_log("Loading vocab TTL files...")
            self.vocab = load_files(cfg["vocab"])
            srv.verbose_log(f"  vocab: {len(self.vocab)} triples")

            srv.verbose_log("Loading schema/model TTL files...")
            self.schema = load_files(cfg["model"])
            srv.verbose_log(f"  schema: {len(self.schema)} triples")

            srv.verbose_log("Merging data stores...")
            self.data = Graph()
            self.data += self.instances
            self.data += self.tags
            self.data += self.vocab
            srv.verbose_log(f"  data store: {len(self.data)} triples")

            srv.verbose_log("Running RDFS inference (owlrl)...")
            combined = Graph()
            combined += self.schema
            combined += self.data
            owlrl.DeductiveClosure(owlrl.RDFS_Semantics).expand(combined)
            _clean_boolean_expansion(combined)
            self.rdfs = combined
            srv.verbose_log(f"  rdfs store: {len(self.rdfs)} triples after inference")

            srv.verbose_log("Applying SKOS rules...")
            apply_skos_rules(self.rdfs)
            srv.verbose_log(f"  rdfs store: {len(self.rdfs)} triples after SKOS rules")

            srv.verbose_log("Executing policy updates...")
            Policy.exec(self.rdfs, cfg_dir)
            srv.verbose_log(f"  rdfs store: {len(self.rdfs)} triples after policy")

            _validate(self.rdfs, cfg_dir)

            # Bind canonical prefixes so any graph derived from these
            # stores serializes with vad:/work:/the:/... not ns1:/ns2:.
            for store in (self.instances, self.tags, self.vocab,
                          self.schema, self.data, self.rdfs):
                bind_standard_prefixes(store)

            elapsed = time.time() - t0
            Server.log_out(
                f"Data load complete in {elapsed:.1f}s — "
                f"{len(self.rdfs)} triples in rdfs store"
            )

        finally:
            _release_lock(cfg)
