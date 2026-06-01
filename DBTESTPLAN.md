# DBTESTPLAN.md — v5.2 Fuseki + TDB2 Validation

Test plan for validating Apache Jena Fuseki + TDB2 as a drop-in backend
replacement for RDFLib in-memory store in cwvaServer-py.

The goal is to confirm that the Fuseki-backed server produces identical
results to the RDFLib-backed server across all page types, query results,
inference output, and policy application — before cutting over production.

---

## Approach

Run both stores in parallel against the same TTL dataset:

- **Store A** — RDFLib in-memory (current production behavior)
- **Store B** — Fuseki + TDB2 (new backend)

Compare outputs systematically. Any divergence reveals an inference,
policy, or query difference to resolve before cutover.

The `sparqlEndpoint` config field controls which store is active:
```json
// Store A — RDFLib (sparqlEndpoint absent)
{}

// Store B — Fuseki
{ "sparqlEndpoint": "http://localhost:3030/cwva/sparql" }
```

---

## Phase 1 — Fuseki Setup and Data Load

### 1.1 Install Fuseki
```bash
# Download Apache Jena Fuseki
wget https://dlcdn.apache.org/jena/binaries/apache-jena-fuseki-{version}.tar.gz
tar -xzf apache-jena-fuseki-{version}.tar.gz
cd apache-jena-fuseki-{version}
```

### 1.2 Load TTL into TDB2
```bash
tdb2.tdbloader --loc=/path/to/tdb2 \
    ~/cwva/metacontent/model/*.ttl \
    ~/cwva/metacontent/vocab/*.ttl \
    ~/cwva/content/data/*.ttl \
    ~/cwva/content/tags/*.ttl
```

### 1.3 Configure Fuseki dataset with RDFS inference
Create `config.ttl` with RDFS reasoner bound to TDB2 graph.
See DATABASE.md for the full Fuseki dataset config.

### 1.4 Start Fuseki
```bash
./fuseki-server --config=config.ttl
# Verify: http://localhost:3030/cwva/sparql
```

### 1.5 Verify data loaded correctly
```sparql
SELECT (COUNT(*) AS ?n) { ?s ?p ?o }
```
Compare triple count against RDFLib rdfs store count from startup log.
Expected: same order of magnitude — Fuseki may differ slightly due to
inference implementation differences.

---

## Phase 2 — Inference Validation

### 2.1 RDFS inference

Run against both stores and compare results:

```sparql
# subClassOf transitivity
SELECT ?cls ?ancestor {
    ?cls rdfs:subClassOf+ ?ancestor .
    FILTER(!isBlank(?cls) && !isBlank(?ancestor))
}
ORDER BY ?cls ?ancestor
```

```sparql
# rdf:type inference
SELECT ?inst ?type {
    ?inst a vad:CreativeWork .
    ?inst a ?type .
}
ORDER BY ?inst ?type
```

Expected: identical results. Any divergence indicates a reasoner
configuration difference.

### 2.2 SKOS rules

```sparql
# inverseRule: broader → narrower
SELECT ?a ?b {
    ?a skos:broader ?b .
    FILTER NOT EXISTS { ?b skos:narrower ?a }
}
```
Expected: zero results in both stores (inverse already applied).

```sparql
# transitiveRule: broader transitivity
SELECT (COUNT(*) AS ?n) { ?a skos:broader+ ?b }
```
Compare counts — should match between stores.

```sparql
# symmetricRule: related symmetry
SELECT ?x ?y {
    ?x skos:related ?y .
    FILTER NOT EXISTS { ?y skos:related ?x }
}
```
Expected: zero results in both stores.

### 2.3 Policy updates (Policy.upd)

```sparql
# Copyright triples applied by policy
SELECT (COUNT(*) AS ?n) {
    ?work schema:copyrightNotice ?notice .
}
```

```sparql
# Ontology concept schemes created by policy
SELECT (COUNT(*) AS ?n) {
    ?s the:tag the:forOntology .
}
```

```sparql
# skos:Collections created by policy
SELECT (COUNT(*) AS ?n) {
    ?s a skos:Collection .
}
```

Compare all counts between stores. Any divergence indicates a Policy.upd
execution difference — likely IRI() handling or UPDATE statement ordering.

---

## Phase 3 — Query Result Validation

Run each QuerySupport query against both stores and diff results.

### 3.1 Gallery query

```sparql
SELECT ?uri ?image ?label ?artist ?site {
    ?uri a vad:CreativeWork ;
         schema:image ?image ;
         rdfs:label ?label ;
         schema:dateCreated ?dt ;
         vad:hasArtistProfile/vad:artist/rdfs:label ?artist .
    OPTIONAL { ?uri vad:workOnSite ?site }
} ORDER BY desc(str(?dt))
```

Expected: identical result sets, same order.

### 3.2 Browser page CONSTRUCT

For a known work URI, run the promoteBNData CONSTRUCT and Tags CONSTRUCT
against both stores. Compare the resulting graphs:

```python
# Compare triple counts and specific triples
assert len(store_a_graph) == len(store_b_graph)
for triple in store_a_graph:
    assert triple in store_b_graph
```

### 3.3 Concept schemes

```sparql
SELECT ?uri ?label {
    ?uri a vad:ConceptScheme ; rdfs:label ?label .
} ORDER BY ?label
```

```sparql
SELECT ?uri ?label {
    ?uri a skos:ConceptScheme ; rdfs:label ?label .
} ORDER BY ?label
```

Compare scheme lists — both count and URIs must match.

### 3.4 Collections

```sparql
SELECT ?uri ?label {
    ?uri a skos:Collection ; rdfs:label ?label .
} ORDER BY ?label
```

### 3.5 VocabTree

```sparql
SELECT ?concept ?label ?broader {
    ?concept a skos:Concept ;
             skos:prefLabel ?label .
    OPTIONAL { ?concept skos:broader ?broader }
}
```

Compare concept counts and hierarchy structure.

---

## Phase 4 — Page Rendering Validation

Start two server instances — one RDFLib-backed, one Fuseki-backed —
and compare rendered HTML for each page type.

### 4.1 Setup

```bash
# Instance A — RDFLib (port 8081)
python main.py -cfg serverCwva_rdflib.rson   # no sparqlEndpoint

# Instance B — Fuseki (port 8082)  
python main.py -cfg serverCwva_fuseki.rson   # sparqlEndpoint set
```

### 4.2 Gallery page
```bash
curl http://localhost:8081/ > gallery_rdflib.html
curl http://localhost:8082/ > gallery_fuseki.html
diff gallery_rdflib.html gallery_fuseki.html
```

Expected: identical HTML. Any diff in work list order or content
indicates a query result difference.

### 4.3 Browser page — for each work
```bash
for guid in $(cat test_guids.txt); do
    curl "http://localhost:8081/work/$guid" > "work_${guid}_rdflib.html"
    curl "http://localhost:8082/work/$guid" > "work_${guid}_fuseki.html"
    diff "work_${guid}_rdflib.html" "work_${guid}_fuseki.html"
done
```

### 4.4 Concepts page
```bash
diff <(curl http://localhost:8081/vocabTree) \
     <(curl http://localhost:8082/vocabTree)
```

### 4.5 Explore page — graph data
```bash
# Ontology graph
diff <(curl "http://localhost:8081/explore/graph-data?type=ontology&schemes=...") \
     <(curl "http://localhost:8082/explore/graph-data?type=ontology&schemes=...")

# Thesaurus graph
diff <(curl "http://localhost:8081/explore/graph-data?type=thesaurus&schemes=...") \
     <(curl "http://localhost:8082/explore/graph-data?type=thesaurus&schemes=...")
```

### 4.6 About page
```bash
diff <(curl http://localhost:8081/about) \
     <(curl http://localhost:8082/about)
```

---

## Phase 5 — Production Path Test

Run the full 2300-path production test against the Fuseki-backed server
and compare with the RDFLib-backed baseline.

```bash
# Run path test against both instances
python tools/path_test.py --host http://localhost:8081 \
    --paths production_paths.txt > results_rdflib.txt

python tools/path_test.py --host http://localhost:8082 \
    --paths production_paths.txt > results_fuseki.txt

# Compare status codes
diff results_rdflib.txt results_fuseki.txt
```

Expected: identical status codes for all 2300 paths. Any 500 on
the Fuseki instance where RDFLib returns 200 is a bug to investigate.

---

## Phase 6 — Performance Validation

### 6.1 Startup time
```bash
# Time the full load sequence including inference
time python main.py -cfg serverCwva_rdflib.rson &
time python main.py -cfg serverCwva_fuseki.rson &
```

Fuseki-backed server should start faster — no TTL load, no owlrl
inference pass, just connect to the persistent store.

### 6.2 Memory usage
```bash
# Check RSS after startup
ps aux | grep python
```

Fuseki-backed server should use significantly less RAM — no in-memory
RDF graph.

### 6.3 Query latency
```bash
# Time gallery page response
time curl http://localhost:8081/
time curl http://localhost:8082/
```

Fuseki introduces network overhead (loopback) but TDB2 indexing
should compensate for larger datasets.

---

## Phase 7 — Refresh Validation

### 7.1 Add a new work TTL to the dataset

Create `test_work.ttl`, add to `~/cwva/content/data/`.

### 7.2 Reload Fuseki

The Fuseki reload on `/refresh` is different from the RDFLib reload:
- RDFLib: re-reads all TTL files, re-runs inference
- Fuseki: re-runs `tdb2.tdbloader`, Fuseki picks up changes

Define and test the Fuseki reload path in `db_mgr.py`.

### 7.3 Verify new work appears in gallery

```bash
curl http://localhost:8082/ | grep test_work_title
```

---

## Pass Criteria

| Test | Pass condition |
|---|---|
| Triple count | Within 5% of RDFLib store (inference may differ slightly) |
| RDFS inference | Identical subClassOf transitivity and rdf:type results |
| SKOS rules | Zero violations on all three rule checks |
| Policy updates | Identical copyright, scheme, and collection counts |
| Gallery query | Identical work list, same order |
| Browser pages | Identical HTML for all tested works |
| Concepts page | Identical concept tree |
| Explore graph data | Identical node/edge sets |
| Production path test | Identical status codes for all 2300 paths |
| Startup time | Faster than RDFLib baseline |
| Memory usage | Less than RDFLib baseline |
| Refresh | New work appears after reload |

---

## Rollback Plan

If any phase fails and cannot be resolved:

1. Stop the Fuseki-backed server instance
2. Remove `sparqlEndpoint` from production config
3. Restart with RDFLib in-memory (existing behavior)
4. Document the failure and open a GitHub issue

The `sparqlEndpoint` config field is the only change required to
switch between backends — rollback is a one-line config change.

---

*See also: [DATABASE.md](DATABASE.md) — implementation details*
*See also: [ROADMAP.md](ROADMAP.md) — v5.2 context*
