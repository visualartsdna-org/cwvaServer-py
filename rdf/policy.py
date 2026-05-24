"""SPARQL UPDATE policy execution — port of Policy.groovy.

Parses Policy.upd (statements delimited by '# update delimiter' lines),
strips comment lines, and executes each UPDATE against the rdfs graph.

RDFLib limitation: SPARQL UPDATE cannot reliably INSERT triples whose subject
URI is constructed via BIND(IRI(...) AS ?var).  The ontology concept scheme
statements in Policy.upd use this pattern and are silent no-ops in RDFLib
(they work correctly in Jena and serve as documentation for that path).
_build_ontology_schemes() replicates their effect in Python, driven by the
FILTER(?t IN (...)) list parsed directly from Policy.upd so that file remains
the single source of truth for which classes get concept schemes.
"""

import re
from pathlib import Path

from rdflib import Graph, URIRef, Literal
from rdflib.namespace import RDF, RDFS, SKOS

from server import Server
from rdf.prefixes import FOR_QUERY, THE


# ---------------------------------------------------------------------------
# Prefix resolver (used to expand prefixed names from Policy.upd)
# ---------------------------------------------------------------------------

def _build_prefix_map() -> dict:
    m = {}
    for line in FOR_QUERY.strip().splitlines():
        hit = re.match(r'prefix\s+(\w+):\s+<([^>]+)>', line, re.IGNORECASE)
        if hit:
            m[hit.group(1)] = hit.group(2)
    return m

_PREFIX_MAP = _build_prefix_map()

_FILTER_IN_RE = re.compile(
    r'filter\s*\(\s*\?t\s+in\s*\((.*?)\)\s*\)',
    re.IGNORECASE | re.DOTALL,
)


def _resolve(prefixed: str) -> str | None:
    """Expand 'pfx:Local' to a full URI string using FOR_QUERY prefixes."""
    prefixed = prefixed.strip()
    if ':' not in prefixed:
        return None
    pfx, local = prefixed.split(':', 1)
    ns = _PREFIX_MAP.get(pfx.strip())
    return (ns + local.strip()) if ns else None


def _parse_ont_class_uris(statements: list) -> list:
    """Find the forOntology INSERT statement and extract its FILTER(?t IN (...)) URIs."""
    for stmt in statements:
        if 'forOntology' in stmt:
            m = _FILTER_IN_RE.search(stmt)
            if m:
                uris = [_resolve(item) for item in m.group(1).split(',')]
                return [u for u in uris if u]
    return []


# ---------------------------------------------------------------------------
# Ontology scheme builder (Python workaround for RDFLib SPARQL UPDATE gap)
# ---------------------------------------------------------------------------

def _build_ontology_schemes(g: Graph, class_uris: list):
    """Create skos:ConceptScheme instances and skos:inScheme memberships.

    Replicates the two Policy.upd INSERTs that RDFLib cannot execute due to
    its incomplete support for BIND(IRI(...)) as an INSERT subject.  The
    class_uris list is parsed from Policy.upd at load time, so that file
    remains the single source of truth — edit the FILTER list there to change
    which classes get concept schemes.
    """
    added = 0
    for cls_uri_str in class_uris:
        cls_uri = URIRef(cls_uri_str)
        labels = list(g.objects(cls_uri, RDFS.label))
        if not labels:
            continue
        label = str(labels[0])
        sch = URIRef(cls_uri_str + "Scheme")

        g.add((sch, RDF.type, SKOS.ConceptScheme))
        g.add((sch, RDFS.label, Literal(label + " scheme")))
        g.add((sch, THE.tag, THE.forOntology))

        members = g.query(
            FOR_QUERY
            + f"SELECT ?tn WHERE {{ ?tn rdfs:subClassOf* <{cls_uri_str}> . FILTER(!isBlank(?tn)) }}"
        )
        for row in members:
            g.add((URIRef(str(row[0])), SKOS.inScheme, sch))

        added += 1

    Server.log_out(f"  Built {added} ontology concept schemes from Policy.upd filter list")


# ---------------------------------------------------------------------------
# Policy file loader
# ---------------------------------------------------------------------------

def load_policy(cfg_dir: str) -> list:
    """Parse Policy.upd into a list of SPARQL UPDATE strings."""
    path = Path(cfg_dir) / "res" / "Policy.upd"
    if not path.exists():
        Server.log_out(f"WARNING: Policy.upd not found at {path}")
        return []

    statements, current = [], []
    with open(path) as f:
        for line in f:
            if line.startswith("# update delimiter"):
                if current:
                    statements.append("\n".join(current))
                current = []
            elif not line.startswith("#"):
                current.append(line.rstrip())

    if current:
        statements.append("\n".join(current))

    return statements


# ---------------------------------------------------------------------------
# Policy executor
# ---------------------------------------------------------------------------

def exec(graph: Graph, cfg_dir: str):
    """Execute all SPARQL UPDATE statements from Policy.upd against graph,
    then build ontology concept schemes in Python (RDFLib SPARQL UPDATE gap)."""
    statements = load_policy(cfg_dir)

    # Parse the ontology class list from Policy.upd before executing statements
    ont_class_uris = _parse_ont_class_uris(statements)
    if not ont_class_uris:
        Server.log_out("WARNING: no ontology class URIs found in Policy.upd forOntology statement")

    Server.log_out(f"Executing {len(statements)} policy updates...")
    errors = 0
    for i, stmt in enumerate(statements):
        stripped = stmt.strip()
        if not stripped:
            continue
        preview = stripped[:60].replace("\n", " ")
        try:
            graph.update(FOR_QUERY + stripped)
        except Exception as e:
            Server.log_out(f"ERROR in policy update #{i + 1} ({preview!r}): {e}")
            errors += 1

    if errors:
        Server.log_out(f"Policy execution complete with {errors} error(s).")
    else:
        Server.log_out("Policy execution complete.")

    # Build ontology schemes in Python — the SPARQL UPDATEs above that use
    # BIND(IRI(...)) are silent no-ops in RDFLib but remain in Policy.upd as
    # documentation and for Jena compatibility.
    if ont_class_uris:
        _build_ontology_schemes(graph, ont_class_uris)
