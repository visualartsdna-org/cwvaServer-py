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
import time
from pathlib import Path

import owlrl
from rdflib import Graph
from rdflib.namespace import SKOS

from server import Server
from rdf import policy as Policy
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
                Server.log_out("Syncing TTL files from GCP bucket...")
                gcp.gcp_cp_dir_recurse(
                    cfg["cloud"]["src"],
                    cfg["cloud"]["tgt"],
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
            self.rdfs = combined
            srv.verbose_log(f"  rdfs store: {len(self.rdfs)} triples after inference")

            srv.verbose_log("Applying SKOS rules...")
            apply_skos_rules(self.rdfs)
            srv.verbose_log(f"  rdfs store: {len(self.rdfs)} triples after SKOS rules")

            srv.verbose_log("Executing policy updates...")
            Policy.exec(self.rdfs, cfg_dir)
            srv.verbose_log(f"  rdfs store: {len(self.rdfs)} triples after policy")

            _validate(self.rdfs, cfg_dir)

            elapsed = time.time() - t0
            Server.log_out(
                f"Data load complete in {elapsed:.1f}s — "
                f"{len(self.rdfs)} triples in rdfs store"
            )

        finally:
            _release_lock(cfg)
