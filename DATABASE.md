# DATABASE.md — Large-Scale Deployment with Apache Jena Fuseki + TDB2

This document describes the path to scaling cwvaServer-py beyond the default
RDFLib in-memory store to Apache Jena Fuseki + TDB2 for very large deployments.

Current CE deployments — typically 20,000–100,000 triples — are well served
by the existing RDFLib in-memory approach. This path is opt-in for operators
who need persistent indexed storage at scale.

---

## When to Consider This

- Federated catalogs aggregating hundreds of artists
- Collections exceeding one million triples
- Startup time becomes unacceptable (owlrl inference over large graphs)
- Memory pressure on the server instance
- Need for persistent indexed storage that survives server restarts

---

## Architecture

At scale the clean separation of concerns is:

- **Fuseki + TDB2** — storage, indexing, SPARQL execution, inference, rules,
  SHACL validation
- **cwvaServer-py** — HTTP routing, HTML rendering, application logic

The boundary between them is a standard SPARQL endpoint. cwvaServer-py
becomes a pure HTTP/HTML application that queries Fuseki — no RDF processing
in the Python layer beyond query result handling.

---

## Migration Surface in cwvaServer-py

The migration is confined to `rdf/db_mgr.py`. All query, rendering, and
application code is unchanged — they call `self.rdfs.query()` as before,
unaware that the store is now remote.

```python
# db_mgr.py — swap RDFLib in-memory graph for Fuseki endpoint
from rdflib.plugins.stores.sparqlstore import SPARQLStore
from rdflib import Graph

store = SPARQLStore("http://localhost:3030/cwva/sparql")
self.rdfs = Graph(store=store)
```

Everything downstream — `query_support.py`, `rdf2html.py`, `explore.py`,
`services/` — is unchanged.

A `sparqlEndpoint` config field in `serverCwva.rson` would signal db_mgr.py
to use the remote store instead of loading TTL files into memory:

```json
"sparqlEndpoint": "http://localhost:3030/cwva/sparql"
```

When absent, the server loads TTL files into RDFLib in-memory as today.

---

## Inference Pipeline

The full inference and policy pipeline moves into Jena/Fuseki — everything
that currently happens in `db_mgr.py` at Python startup moves into Fuseki's
dataset initialization or a Jena-based load script.

### RDFS Inference

Jena's RDFS reasoner configured in the Fuseki dataset:

```turtle
# Fuseki dataset config (config.ttl)
:dataset a ja:RDFDataset ;
    ja:defaultGraph :inferredGraph .

:inferredGraph a ja:InfModel ;
    ja:reasoner [ ja:reasonerURL
        <http://jena.hpl.hp.com/2003/RDFSExptRuleReasoner> ] ;
    ja:baseModel :tdbGraph .

:tdbGraph a tdb2:GraphTDB2 ;
    tdb2:location "/path/to/tdb2" .
```

### SKOS Rules

The original `skos.rules` file in Jena native rule syntax runs directly —
no Python translation needed. The three rules currently implemented as
Python graph mutations in `db_mgr.py` move back to their original form:

```
[inverseRule:
    (?A skos:broader ?B) -> (?B skos:narrower ?A)]

[transitiveRule2:
    (?A skos:broader ?B), (?B skos:broader ?C) -> (?A skos:broader ?C)]

[symmetricRule:
    (?Y skos:related ?X) -> (?X skos:related ?Y)]
```

### Policy Updates (Policy.upd)

Existing `Policy.upd` SPARQL UPDATE statements run unchanged against the
Fuseki update endpoint. Jena handles `IRI()` natively and case-insensitively —
the Python workaround (`_BUILTIN_RE` normalization in `policy.py`) is no
longer needed:

```python
# policy.py at scale — direct to Fuseki update endpoint
import requests

def exec_remote(update_endpoint: str, cfg_dir: str):
    for stmt in load_policy(cfg_dir):
        requests.post(update_endpoint,
                      data={"update": FOR_QUERY + stmt})
```

### SHACL Validation

Jena's `jena-shacl` runs directly against TDB2 — no pyshacl dependency,
no Python install complexity. Violations reported through Jena's standard
validation report mechanism.

---

## Full Load Pipeline at Scale

```
TTL files
    → tdb2-tdbloader (bulk load into TDB2)
    → RDFS inference (Fuseki reasoner config)
    → skos.rules (Jena generic rule reasoner)
    → Policy.upd (SPARQL 1.1 Update via Fuseki endpoint)
    → SHACL validation (jena-shacl)
    → Fuseki serves SPARQL endpoint
    → cwvaServer-py queries endpoint
```

Compare to the current CE pipeline which runs all of the above in Python
at startup. At scale the Jena pipeline runs once at load time; subsequent
server restarts just reconnect to the persistent TDB2 store.

---

## Fuseki Setup

Fuseki is free and open source. Download from
[Apache Jena](https://jena.apache.org/documentation/fuseki2/).

```bash
# Start Fuseki with TDB2 dataset
./fuseki-server --tdb2 --loc=/path/to/tdb2 /cwva

# Load TTL files into TDB2
tdb2.tdbloader --loc=/path/to/tdb2 \
    metacontent/model/*.ttl \
    metacontent/vocab/*.ttl \
    content/data/*.ttl \
    content/tags/*.ttl
```

Fuseki runs on port 3030 by default. The SPARQL endpoint is at
`http://localhost:3030/cwva/sparql`.

---

## Advantages Over Current Approach at Scale

| Concern | RDFLib in-memory (CE) | Fuseki + TDB2 (scale) |
|---|---|---|
| Startup time | Slow for large graphs (owlrl) | Fast — data already indexed |
| Memory | Entire graph in RAM | Disk-based with OS cache |
| Persistence | Reloads from TTL on every restart | Persistent across restarts |
| Inference | owlrl Python library | Jena native reasoner |
| Rules | Python graph mutations | Jena native rule syntax |
| Policy | Python workaround for IRI() | Native SPARQL 1.1 Update |
| SHACL | pyshacl | jena-shacl |
| Query performance | Good to ~1M triples | Scales to hundreds of millions |

---

## Operational Notes

- Fuseki can run as a systemd service alongside cwvaServer-py
- TDB2 is persistent — data survives server restarts without reloading TTL
- The `/refresh` command at scale triggers a Fuseki reload rather than
  cwvaServer-py's Python load pipeline
- Monitor Fuseki memory via JVM heap settings (`-Xmx`) — TDB2 uses
  OS file cache aggressively, size heap accordingly
- Fuseki's web UI (port 3030) provides a built-in SPARQL browser —
  the cwvaServer-py built-in SPARQL browser can proxy to Fuseki directly

---

*See also: [ROADMAP.md](ROADMAP.md) — Large-Scale Deployment entry*
