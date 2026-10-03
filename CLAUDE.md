# CWVA Python Server — Project Specification for Claude Code

## Project Overview

Retool the CWVA main server from Groovy/Jetty to Python/FastAPI as a **single server**.
The Groovy function server is retired — all functionality is consolidated here.

Production: GCP Debian VM. Development: Linux (WSL or native).
Open source on GitHub — write clean, well-commented, idiomatic Python.

Live reference: https://visualartsdna.org (Groovy v4.0.x)

---

## Architecture

```
Client
  │
  ▼
CWVA Server  (Python/FastAPI, port 80)
  ├── HTML pages (gallery, browser, concepts, explore, about, ask)
  ├── Internal SPARQL queries via RDFLib
  ├── SPARQL browser (built-in, rate-limited, timeout-guarded)
  ├── Static assets (dist/, html/, images/, documents/, thumbnails/)
  └── AI agent proxy (/agent/query → agentUrl)
```

`twinHost` config field is **retired**. `/sparql` is handled internally — no proxy.

---

## Technology Stack

| Concern | Choice |
|---|---|
| HTTP framework | **FastAPI** + Uvicorn |
| RDF library | **RDFLib 7.x** + **owlrl** |
| GCP storage | **google-cloud-storage** (ADC) |
| Config | RSON (JSON + `#` comments) |
| Static files | FastAPI `StaticFiles` for dist/html; explicit handlers for images/docs |
| Markdown→HTML | **mistune** |
| Image resize | **Pillow** (thumbnails ≤700px) |
| Graph rendering | **Cytoscape.js** + **dagre** layout (browser-side) |

---

## Environment Variables

Never stored in config files or committed to git.

| Variable | Purpose |
|---|---|
| `GCP_BUCKET` | GCP bucket name (required for cloud sync) |
| `ANTHROPIC_API_KEY` | Anthropic API key (required only if `agentUrl` configured) |

---

## Repository Layout

```
cwva-server/
├── CLAUDE.md
├── README.md / LICENSE / requirements.txt
├── main.py                    # entry point
├── server.py                  # Server singleton (cfg, dbm, started_at)
├── servlet.py                 # primary routes
├── servlet_base.py            # secondary routes + shared helpers
├── config/serverCwva.rson     # not committed — use .example.rson template
├── rdf/
│   ├── db_mgr.py              # TTL loading, inference, policy
│   ├── query_support.py       # all SPARQL queries
│   ├── prefixes.py            # namespace prefixes + FOR_QUERY string
│   └── policy.py              # SPARQL UPDATE runner
├── services/
│   ├── browse_works.py        # Gallery page
│   ├── rdf2html.py            # Browser/detail page
│   ├── sparql_browser.py      # Built-in SPARQL browser (replaces proxy)
│   ├── vocab_tree.py          # Concepts page
│   ├── explore.py             # Explore page (Cytoscape graphs + collections)
│   ├── about.py               # About page
│   ├── agent_client.py        # Ask/AI page
│   └── model_viewer.py        # 3D GLB viewer (/modelviewer)
├── util/
│   ├── rson.py / gcp.py / html_template.py / metrics.py
│   ├── logging.py             # log_out → stdout, log_err → stderr (ISO 8601 timestamps)
│   └── token.py               # Java hashCode * time_ms token validation
└── res/
    ├── Policy.upd / servletPolicy.rson / *.shacl
    └── checks/                 # on-demand shapes, not run at load
```

---

## Configuration — Key Fields

```json
{
  "port": 80,
  "host": "http://192.168.1.71:80",
  "dir": ".",
  "model":      "/home/user/cwva-py/metacontent/model",
  "vocab":      "/home/user/cwva-py/metacontent/vocab",
  "data":       "/home/user/cwva-py/content/data",
  "tags":       "/home/user/cwva-py/content/tags",
  "documents":  "/home/user/cwva-py/content/documents",
  "images":     "/home/user/cwva-py/content/images",
  "thumbnails": "/home/user/cwva-py/thumbnails",
  "domain":     "http://visualartsdna.org",
  "sparql":     true,
  "agentUrl":   "http://localhost:8090",
  "welcomeText": "Welcome to my gallery. Feel free to explore.",
  "contactEmail": "your@email.com",
  "copyrightName": "your.org"
}
```

Content folder layout:
```
~/cwva-py/
├── main/           # cwvaServer-py (code repo)
├── metacontent/    # shared ontology — model/ and vocab/
├── content/        # user data — data/ tags/ documents/ images/ provenance/
└── thumbnails/     # auto-generated, not in any repo
```

`model`/`vocab` live in `metacontent/` — auto-populated from `referenceModel` on first startup; most users need no action. Advanced: optionally clone `cwvaMetacontent` there.
`data`/`tags`/`documents`/`images` live in `content/` (user-governed git repo).
`images/` excluded from the content repo via `.gitignore`; `thumbnails/` outside it entirely.
`welcomeText` — gallery home page welcome paragraph (falls back to VisualArtsDNA default).
`contactEmail` — footer email address (falls back to `visualartsdna@gmail.com`).
`copyrightName` — name in footer copyright line (falls back to `visualartsdna.org`).

---

## Data Layer (`rdf/db_mgr.py`)

### Stores

| Name | Source | Purpose |
|---|---|---|
| `instances` | `cfg.data` | 295 artwork TTL files |
| `tags` | `cfg.tags` | 28 tag TTL files |
| `vocab` | `cfg.vocab` | 23 vocabulary TTL files |
| `schema` | `cfg.model` | cwva.ttl + entity.ttl + schemaSupplement.ttl + thesaurus.ttl |
| `data` | instances+tags+vocab | merged non-inferred |
| `rdfs` | RDFS(schema+data) + SKOS rules + Policy | primary query target |

### Load sequence
1. Load discrete stores from TTL folders
2. Merge into `data`; load `schema`
3. RDFS inference via `owlrl.DeductiveClosure(owlrl.RDFS_Semantics)`
4. Apply SKOS rules (3 rules as Python graph mutations)
5. Run `Policy.exec()` (SPARQL UPDATEs from `res/Policy.upd`)
6. SHACL validation (log only, never abort) — see SHACL note below
7. Release `.loadLock`

### SKOS rules (Python graph mutations — Jena rule syntax not portable)
- Pass 1: `skos:broader` → inverse `skos:narrower`
- Pass 2: `skos:broader` transitivity to fixpoint
- Pass 3: `skos:related` symmetry
- The `skos:related` transitivity rule is **commented out** in source — do NOT add it

### Policy
`res/Policy.upd` — delimiter-separated SPARQL UPDATE statements.
Strip `# ...` comments, split on `# update delimiter` lines, run each via
`graph.update(FOR_QUERY + stmt)`.

---

## SPARQL Prefixes (`rdf/prefixes.py`)

`FOR_QUERY` string with: `dct foaf owl rdf rdfs schema skos the tko vad work xs xsd`

Namespace constants: `VAD WORK THE TKO SCHEMA` (rdflib `Namespace` objects).

---

## Routing

### Primary (`servlet.py`)

| Path | Handler |
|---|---|
| `GET /` `/browseSort` `/browseFilter` | `browse_works()` |
| `GET /work/{guid}` | `rdf2html()` |
| `GET /model/{cls}` `/thesaurus/{term}` | `rdf2html()` |
| `GET /model` `/data` `/rdfs` `/vocab` | RDF store, content-negotiated |
| `GET /vocabTree` | `vocab_tree()` |
| `GET /agentClient` | `agent_client()` |
| `GET /about` | `about()` |
| `GET /explore` | `explore()` |
| `GET /sparql` | `sparql_browser()` — built-in, guarded |
| `GET /modelviewer` | `model_viewer()` — 3D GLB viewer; `?work=work:guid`; optional `?selectBkgnd=uri` |
| `GET /dist/*` `/html/*` `/static/*` | StaticFiles |

### Secondary (`servlet_base.py`)

| Path | Handler |
|---|---|
| `GET /guid` | UUID generator utility page — new UUID on each load, copy button |
| `GET /status` | health check → `{"status": "ok"}` |
| `GET /status/os` | token-validated OS health → JSON (system, processes, disk, logs, errors) |
| `GET /metrics` | pretty JSON metrics dump (no token) |
| `GET /metricTables` | serves `stats/chart.html` from GCS bucket |
| `GET,POST /sparqlEndpoint` | internal SPARQL → JSON |
| `GET /explore/graph-data` | graph JSON for Cytoscape |
| `POST /agent/query` | proxy to agentUrl, rate-limited (10/10min/IP) |
| `GET /md2html` | fetch `?doc=` URL, convert markdown→HTML |
| `GET /images/*` `/thumbnails/*` `/documents/*` | on-demand GCP fetch + serve |
| `GET,HEAD /media/*` | video, streamed with Range support (206/416); on-demand GCP fetch |
| `GET /favicon.ico` `/favicon.png` | from images folder |
| `GET /refresh` | 403 — use `/cmd?token=…&cmd=refresh` |
| `GET /cestfini` | 403 — use `/cmd?token=…&cmd=cestfini` |
| `GET /cmd` | token-validated: `refresh` reload data, `cestfini` push metrics + shutdown |
| `GET,POST /{path:path}` | catch-all → 404 |

### Format negotiation
`?format=` or `Accept` → `ttl`, `rdf/xml`, `n-triples`, `n3`, `jsonld`.

### Built-in SPARQL browser (`services/sparql_browser.py`)

Three mitigations make open-query risk negligible:
1. **Timeout**: run `graph.query()` in `ThreadPoolExecutor`, `future.result(timeout=10)` → 408
2. **Row cap**: append `LIMIT 10000` if absent in query
3. **Rate limit**: 10 requests/10min/IP → 429

Run via `asyncio.to_thread()`. Return clean HTML error page on timeout or cap.

---

## Browser/Detail Page (`services/rdf2html.py`)

Walks RDFLib graph directly — no JSON-LD intermediate.

### Process flow
1. `qs.query(ns, guid)` → `{f"{ns}:{guid}": Graph, "label": str, "Tags": Graph}`
2. Build rows via `_build_rows(subject, main_g, qs)`
3. Batch-fetch predicate labels via `_fetch_prop_labels()` (one SPARQL VALUES query)
4. Sort: `rdf:type` first, then alphabetical
5. Render Tags section from `qs.query_tags(uri_str)`

### Property special cases

All hrefs/srcs use `_href(uri)` — **root-relative** only (WSL2 requirement).

**Discrete-anchor rule**: any `owl:ObjectProperty` value rendered as its own
standalone link uses the object's **label** as anchor text, with the CURI as
fallback when no label exists. This is the general rule; the table below lists
only what deviates from it. Applies to `rdf:type`, `rdfs:subClassOf`,
`skos:broader/narrower/related/inScheme` too — `schemaSupplement.ttl`
("rdfs hardening") declares those `owl:ObjectProperty` as well.

Two per-page batched queries drive it: `query_object_properties()` (which
predicates are ObjectProperty) and `query_labels()` (labels for every URI they
point at). Never call `query_label()` per value — that is N queries per page.

| Property | Rendering |
|---|---|
| `rdf:type` | Comma-separated links, labels; also detects `skos:Collection` |
| `schema:image` | `<img width="500">` in link |
| `schema:video` | native `<video controls>`; accepts a direct URI **or** a `schema:VideoObject` with `schema:contentUrl` + `rdfs:label` (label → caption beneath the player, **anchored to the video URL**; `schema:name` optional, fallback only) |
| `vad:image3d` | model-viewer widget |
| `vad:qrcode` | image link width=100 |
| `the:mdDocument` | `/md2html?doc=<url>`; **filename** as text, not label |
| `the:pdfDocument` | root-relative link, **filename** as text |
| `vad:media`, `schema:keywords` | plain text, not anchored |
| `skos:member` | batch `query_labels()`; one row per member, alphabetical (ORDER BY in SPARQL) |
| Blank node with `rdfs:label` | inline label in parent row |
| Blank node without label | recursive sub-rows |
| URI (general) | root-relative linked short name |
| Literal | plain text, `\n`→`<br>` |

**Namespace note**: `mdDocument`, `pdfDocument`, `tag` are `the:` not `vad:`.

---

## Gallery Page (`services/browse_works.py`)

Params: `order` (Date|Title), `artist` (all|name), `offset`, `limit`, `page`, `isMobile`.

Two SPARQL queries: count (pagination) + paginated SELECT on `vad:CreativeWork`.
Sort: `desc(str(?dt))` for Date (timezone-safe), `asc(?label)` for Title.

Layout: CSS grid `repeat(auto-fill, minmax(336px, 1fr))` — n columns by viewport.
JS: `let cw;` declared once, bare assignment per item (avoids `const` SyntaxError).
All image/URI values → root-relative via `_href()`.

Controls: order radio (`/browseSort`) + artist select (`/browseFilter`), auto-submit.
Pagination: desktop (all pages) or mobile (prev/current/next). `PAGE = 20`.

---

## Explore Page (`services/explore.py`)

### Overview

Interactive graph visualization — **Cytoscape.js** + **dagre** layout (browser-side).
No Graphviz binary required. Server returns JSON; browser renders interactive graph.
Supports zoom, pan, and click-to-navigate (node → Browser page).

CDN (no local install):
```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dagre/0.8.5/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.js"></script>
```

### Page layout

```
Explore — Ontologies, thesauri, collections, and data visualizations

[ Quick Access ]
  SPARQL Browser  |  Metrics Dashboard

[ Ontology Graph ]              [ Thesaurus Graph ]
  Select schemes <listbox>        Select schemes <listbox>
  ☐ Include comments on tooltip   ☐ Include definitions on tooltip
  [Generate Graph]                [Generate Graph]

[ Concept Collections ]
  3-column linked grid of collection names

[ Data Visualizations ]
  Ontology | Thesaurus | Watercolor data trees (model-viewer GLB)
```

### Graph data endpoint

`GET /explore/graph-data?type=ontology|thesaurus&schemes=A,B,...`

Returns:
```json
{
  "nodes": [{"id": "vad:Process", "label": "Process", "type": "class", "comment": "..."}],
  "edges": [{"source": "vad:ArtProcess", "target": "vad:Process", "label": "subClassOf"}]
}
```

**Ontology SPARQL** (vad:ConceptScheme → member classes → subClassOf edges):
```sparql
SELECT ?cls ?label ?comment ?parent ?parentLabel {
  ?scheme a vad:ConceptScheme ; skos:member ?cls .
  ?cls rdfs:label ?label .
  OPTIONAL { ?cls rdfs:comment ?comment }
  OPTIONAL { ?cls rdfs:subClassOf ?parent . ?parent rdfs:label ?parentLabel }
  FILTER(?scheme IN ({schemes}))
}
```

**Thesaurus SPARQL** (skos:ConceptScheme → concepts via skos:inScheme → broader edges):
```sparql
SELECT DISTINCT ?concept ?label ?definition ?broader ?broaderLabel WHERE {
  ?concept skos:inScheme ?scheme ;
           rdfs:label ?label .
  OPTIONAL { ?concept skos:definition ?definition }
  OPTIONAL {
    ?concept skos:broader ?broader .
    OPTIONAL { ?broader rdfs:label ?broaderLabel }
    FILTER(!isBlank(?broader))
  }
  FILTER(!isBlank(?concept))
  FILTER(?scheme IN ({schemes}))
}
```

**Key data fact**: thesaurus concepts use `rdfs:label` (not `skos:prefLabel`) and `skos:inScheme`
(not `skos:member`). Confirmed from actual TTL files. Using wrong predicates returns 0 rows.

### Cytoscape rendering

```javascript
const cy = cytoscape({
  container: document.getElementById('cy'),
  elements: {
    nodes: data.nodes.map(n => ({ data: { id: n.id, label: n.label, type: n.type }})),
    edges: data.edges.map(e => ({ data: { source: e.source, target: e.target }}))
  },
  style: [
    { selector: 'node',
      style: { shape: 'ellipse', 'background-color': '#40E0D0',
               label: 'data(label)', 'font-size': '10px', color: '#000' }},
    { selector: 'node[type="external"]',
      style: { 'background-color': '#FFA500' }},  // owl:Thing etc
    { selector: 'edge',
      style: { 'line-color': '#aaa', 'target-arrow-color': '#aaa',
               'target-arrow-shape': 'triangle', 'curve-style': 'bezier' }}
  ],
  layout: { name: 'dagre', rankDir: 'TB', nodeSep: 20, rankSep: 40, animate: false }
});
cy.ready(() => cy.fit());

// Click node → root-relative browser page
cy.on('tap', 'node', e => { window.location = _toHref(e.target.id()); });

// Hover tooltip for comment/definition (shown only when checkbox checked)
cy.on('mouseover', 'node', e => { /* show #node-tip div with node.data('comment') */ });
cy.on('mousemove', e => { /* reposition #node-tip */ });
cy.on('mouseout', 'node', e => { /* hide #node-tip */ });
```

Tooltip element: `<div id="node-tip">` — hidden by default, positioned absolute, shown on node hover.
The `comment` field is always returned by the server (OPTIONAL in query); the checkbox controls
whether the tooltip div is populated and visible.

For large graphs (>50 nodes): show spinner during layout; add `cy.fit()` reset button.

### Scheme selectors

Two `<select multiple>` dropdowns populated from SPARQL at page load (not hardcoded).

**Ontology schemes**: `SELECT ?scheme ?label { ?scheme a vad:ConceptScheme ; rdfs:label ?label } ORDER BY ?label`

**Thesaurus schemes**: `SELECT ?scheme ?label { ?scheme a skos:ConceptScheme ; rdfs:label ?label } ORDER BY ?label`

No "View All" button — showing all schemes at once produces an illegible graph.

### Concept Collections

`query_collections(graph)` → `[{uri, label}]` sorted by label.
Rendered as 3-column linked grid.

### Data Visualizations

`<model-viewer>` GLB files for Ontology, Thesaurus, Watercolor data trees:
```html
<script type="module" src="https://unpkg.com/@google/model-viewer/dist/model-viewer.min.js"></script>
<model-viewer src="/documents/ontologyTree.glb" camera-controls auto-rotate
              style="width:300px;height:300px;background:#e0f0ff"></model-viewer>
```

---

## HTML Templates (`util/html_template.py`)

`head(bg_color="#FFFFFF")` → `<head>` + `<body>` open + nav bar.
All nav hrefs are **root-relative**.

Nav: Home `/` | Ask `/agentClient` (conditional on `agentUrl`) |
Concepts `/vocabTree` | Explore `/explore` | About `/about`

GA tag: `G-0GRY55G849`. Font: Krub (22px). Favicon: `/images/dblHelix.png`.
Link colors: steelblue / cornflowerblue / navy / blue.
`TAIL` — footer with email, copyright year, `VERSION`.
`TABLE_TAIL`, `table_head(h1, h2)`, `title(uri_long, uri_short)` as before.

---

## GCP Sync (`util/gcp.py`)

- **TTL**: bulk recursive download at startup
- **Images/docs**: on-demand fetch + local cache
- **Thumbnails**: on-demand fetch + Pillow resize ≤700px
- **Eviction**: at startup, compare local mtime to bucket `updated`; delete stale
- **Metrics snapshot on shutdown**: `push_metrics(metrics_dict, bucket_name, started_at)` uploads
  `stats/metrics-<timestamp>.json` to GCS; called by `_do_cestfini()` before SIGTERM
- **GCP project**: resolved from `GOOGLE_CLOUD_PROJECT` / `GCLOUD_PROJECT` env vars, or
  `quota_project_id` in `~/.config/gcloud/application_default_credentials.json`

---

## Metrics (`util/metrics.py`)

**In-memory store**: `{date: {ip: {path: {count}, ua_classes: [...], referer_class: str, count: n, links: [...]}}}`.
Collect all requests except `/images/*` and `/favicon.*`.
Track 30+ UA classes and a referer class per IP per day.
Rate limit `/agent/query` and `/sparql`: 10/10min/IP → 429.

**Referer detection**: `r=same` when `Referer` header hostname matches `Host` header netloc.

**Shutdown snapshot**: on `/cmd?cmd=cestfini`, full metrics dict uploaded to GCS via `gcp.push_metrics()`
with `started_at` timestamp (set at server init in `server.py`).

**`/metricTables`**: serves pre-compiled `stats/chart.html` from GCS bucket (generated by
`tools/metricsCompiler.py` nightly cron).

**Metrics compiler** (`tools/metricsCompiler.py` — **not in this repo**; lives on the production
host only, never committed): standalone script that downloads GCS metric
snapshots, fetches live metrics from `/metrics`, merges periods (summing UA/referer/totals,
`max()` for path counts across restart periods), applies 125-day TTL window, generates
Chart.js dashboard HTML, and uploads to `stats/chart.html`. Run with `--test` to write
locally instead of uploading.

---

## Requirements

```
fastapi>=0.111
uvicorn[standard]>=0.29
rdflib>=7.0
owlrl>=6.0
httpx>=0.27
google-cloud-storage>=2.16
mistune>=3.0
python-multipart>=0.0.9
Pillow>=10.0
```

---

## Git / Open Source

`.gitignore`: `*.rson`, `res/Policy.upd`, `.loadLock`, `__pycache__/`,
`*.pyc`, `/stage/`, `/temp/`, `.env`

Include `config/serverCwva.example.rson` with placeholder values.

---

## Development Stages

### Stage 1 ✓ COMPLETE — Foundation
Server startup, RSON config, static files, images/thumbnails/docs on-demand,
metrics, GCP sync, folder structure.

### Stage 2 ✓ COMPLETE — Data Layer
RDFLib stores, RDFS inference, SKOS rules, Policy UPDATEs, SHACL validation,
all QuerySupport queries.

### Stage 3 ✓ COMPLETE — Gallery & Browser Pages
browse_works, rdf2html, format negotiation, /sparqlEndpoint, /documents/*, /md2html.

### Stage 4 ✓ COMPLETE — Remaining Pages
- [x] `services/sparql_browser.py` — built-in with timeout + rate limit
- [x] `services/explore.py` — Cytoscape.js graphs, collections, data viz
- [x] `services/vocab_tree.py` — Concepts page
- [x] `services/about.py` — About page
- [x] `services/agent_client.py` — Ask page (stub if agentUrl unavailable)
- [x] `/cmd` — token-validated command dispatcher (refresh, cestfini)
- [x] **Deliverable:** all pages functional, single-server deployment ready

### Stage 5 ✓ COMPLETE — Hardening
- [x] Error pages — `StarletteHTTPException` handler (404 + other HTTP codes) and `Exception` handler (500) in `build_app()`; styled with site nav/footer
- [x] README — exists at `README.md`
- LICENSE, git init — deployment steps, not code tasks

### Stage 6 ✓ COMPLETE — Refinements
- [x] `services/model_viewer.py` — 3D GLB viewer; dark-themed; background HDR selector; rotation/expand/fullscreen; port of ModelViewer.groovy
- [x] `/modelviewer` route — handles initial load (`?work=`) and background change (`?selectBkgnd=`) on same endpoint
- [x] `vad:image3d` in `rdf2html._build_rows()` — appends inline "3D Viewer" link (`/modelviewer?work=subject_curi`)
- [x] Tags section — label used as anchor text; "Tag" (literal) as property column; label row suppressed; CURI fallback when no label
- [x] `_ARTIST_PREDS` frozenset — `vad:hasArtistProfile`, `artist`, `background`, `pseudonymFor` all show label as anchor
- [x] Configurable footer/welcome — `welcomeText`, `contactEmail`, `copyrightName` in config; `tail()` function replaces `TAIL` constant
- [x] Content folder restructure — `metacontent/` for ontology (model/vocab), `content/` for user data, `thumbnails/` outside both
- [x] GCP sync `path_map` routing — blob routing via config path keys; `cloud.tgt` retired
- [x] `/schema` alias for `/model` — Groovy server compatibility
- [x] `/guid` UUID generator page
- [x] `referenceModel` fetches from `/schema?format=ttl` for full ontology

---

## Key Decisions

| Decision | Rationale |
|---|---|
| Single server — retire function server | 0.001% SPARQL browser usage; Python timeout+rowcap+ratelimit contain all risk; simpler deployment |
| Built-in SPARQL browser with guards | 10s timeout + 10k LIMIT + 10/10min rate limit = zero crash risk |
| Cytoscape.js + dagre for graphs | No Graphviz binary; browser-side; interactive zoom/pan/click; dagre ≈ dot rankdir=TB |
| Skip JSON-LD in rdf2html | Avoids JSON-LD 1.0/1.1 issue; cleaner Python |
| owlrl for RDFS inference | Best-maintained Python RDFS reasoner |
| SKOS rules as Python | Jena rule format not portable; 3 rules trivial directly |
| Images on-demand | Avoids downloading 200+ at startup |
| Thumbnails ≤700px separate folder | 2× display width; clean eviction |
| Root-relative URLs everywhere | WSL2 drops parallel absolute localhost requests |
| `desc(str(?dt))` for date sort | RDFLib timezone grouping bug; ISO 8601 string sort correct |
| `let cw;` once in JS | `const` redeclaration in same scope is SyntaxError |
| `_href()` for all links | Root-relative; absolute localhost → blank pages in WSL2 |
| `query_label()` unions both label predicates | Instances: rdfs:label; concepts: skos:prefLabel |
| `the:` namespace for mdDocument/pdfDocument/tag | Confirmed from live graph — not `vad:` |
| Thesaurus uses `rdfs:label` + `skos:inScheme` | Confirmed from TTL files — `skos:prefLabel`/`skos:member` return 0 rows |
| View All buttons removed from Explore graphs | All-schemes graph has too many nodes to be legible |
| Tooltips for comments/definitions on graph nodes | Comment/definition always fetched (OPTIONAL); checkbox controls display |
| `util/logging.py` for all output | `log_out()` → stdout, `log_err()` → stderr, both with ISO 8601 timestamps |
| Token validation via Java hashCode scheme | `/cmd` token = `String.hashCode(secret) * time_ms`; validated in `util/token.py` |
| `/refresh` and `/cestfini` return 403 direct | Must go through `/cmd?token=…` to prevent unauthorized data reloads/shutdowns |
| `server.started_at` set at init | Enables accurate uptime in metrics snapshots pushed on shutdown |
| `tail()` function replaces `TAIL` constant | Reads `contactEmail`, `copyrightName` from live server config; no import-time coupling to cfg |
| `welcomeText` / `contactEmail` / `copyrightName` config fields | Customizable per deployment; fallback defaults preserve visualartsdna.org identity |
| GCP sync `path_map` routing; `cloud.tgt` retired | Each content type routes to its own config-specified path; decoupled from a single base dir |
| `/schema` alias for `/model` | Groovy compatibility — clients using `/schema?format=ttl` continue to work against Python server |
| Tags section: label as anchor, suppress label row | Eliminates redundant row; CURI fallback when no label; "Tag" literal avoids predicate-derived casing |
| Single `/modelviewer` route for initial load + background change | Groovy had two routes (`/modelviewer` + `/modelviewer.bkgnd`); Python re-queries each request |
| Dark-themed model viewer via `head(bg_color="#18181b")` + nav CSS override | Consistent page entry point; `<style>` block in body overrides nav colors for dark theme |
| `vad:image3d` appends "3D Viewer" link in `_build_rows()` | Direct path from artwork detail to model viewer; root-relative, consistent with all other links |
| Content folder split: `metacontent/` vs `content/` | Ontology (community-governed) separate from user data (user-governed); supports independent git repos |

---

## Notes for Claude Code

- `rdfs` store is the primary query target for all page rendering
- All URIs in HTML use `_href(uri)` → root-relative (never absolute localhost)
- `.loadLock` in `cfg.dir`; `Policy.upd` + `servletPolicy.rson` in `{cfg.dir}/res/`
- **Editing `metacontent/model/` or `vocab/`**: when `referenceModel` is null and `cloud` is set (the production config), `_clear_synced_folders()` **deletes those folders** before each load and GCP sync repopulates them — the bucket is the sole source of truth. A local edit is destroyed by the next server start. Ontology changes must be uploaded to `gs://$GCP_BUCKET/ttl/model/…` (and pushed to `cwvaMetacontent`) to persist. Only when `referenceModel` is set are those folders left alone
- **Video**: `schema:video` → `/media/{file}`, never `/images` (that handler does `read_bytes()` and sends no `Accept-Ranges`, so seeking fails and a large file blows memory). `_media_href()` rewrites any local video URI to `/media/` by basename, so `/images/*.mp4` in data still works. `_build_rows()` intercepts `schema:video` **before** the blank-node split — a `VideoObject` is an unlabelled BNode and would otherwise flatten into recursive sub-rows. `HEAD` is registered explicitly on `/media` (FastAPI does not derive it from GET; players use it to probe length/range support). Native `<video>` only — no video.js. **`schema:video` has `rdfs:range rdfs:Resource`, not `schema:VideoObject`** — RDFS turns a range into an assertion, so the schema.org range would infer every direct-URI video to be a VideoObject, trip `res/video.shacl`, and publish a false triple saying a `.mp4` URL is a VideoObject. Both authoring forms are supported on purpose. **`schema:VideoObject` always carries `rdfs:label`** (the caption); `schema:name` is optional schema.org interop and only a caption fallback. Enforced by `res/video.shacl` (SHACL Core, runs at load). `res/checks/labels.shacl` excludes `schema:video` — a player is not a discretely anchored link — which is why the label requirement is stated in `video.shacl` instead
- **AI notice**: `ai_notice()` in `util/html_template.py`, emitted by `rdf2html.process()` when the displayed subject is typed `the:AI` — above the footer, dark chip (the EU label ships white, invisible on the white page otherwise). Configurable via `aiNoticeText` / `aiNoticeIcon`; either set to `""` disables that part. Deliberately **not** shown on work pages that link a criticism: the link direction is criticism → work, so a work page never displays the criticism anyway
- **`static/`**: project assets shipped in the code repo, mounted at `/static`. Distinct from `cfg.images` (user content, gitignored, GCP-synced). Compliance and UI assets belong here so a fresh clone renders them without depending on synced content
- **SHACL**: `_validate()` runs every `res/*.shacl` at load, log-only, never aborts
- **HDRI**: `HDRI_SUFFIXES = {".hdr"}` is folded into `IMAGE_SUFFIXES`, so `/images/*.hdr` serves (`image/vnd.radiance`, already known to `mimetypes`) — but **streamed**, not `read_bytes()`: a 2k `.hdr` is ~7 MB. `/thumbnails/*.hdr` returns 404 and never reaches `fetch_thumbnail()`, because **Pillow cannot decode Radiance** (`UnidentifiedImageError`) and a thumbnail request must not be answered with megabytes. Browsers cannot render `.hdr` in `<img>` either — so never emit one; embed a skybox-only `<model-viewer>` with a JPEG `poster` and `reveal="interaction"`. Nothing references an HDR yet: no ontology term, no renderer change. See ROADMAP v5.2 for the decided `vad:imageHdrSky`/`vad:imageHdrEnv` modelling and its three traps
- **Explore data-viz GUIDs are hardcoded**: `explore.py::_VIZ_GUIDS` pins three work GUIDs and reads their **`schema:image`** for `<model-viewer src>`. Those three carry `.glb` in `schema:image` (where `vad:image3d` belongs) — an operational experiment, expected to change or go away. Moving the values is therefore *not* data-only: `_load_viz_items()` must change with them, and `vad:image3d`'s `rdfs:domain vad:CreativeWork` would infer those entities to be CreativeWorks
- **Format handling never reads the RDF**: `vad:fileFormat` is display-only — zero occurrences in the codebase. What files are served is decided by extension (`IMAGE_SUFFIXES`, `VIDEO_SUFFIXES`), `Content-Type` by `mimetypes.guess_type()`, and rendering by *predicate* identity (`schema:image`, `vad:image3d`, `schema:video`, `the:mdDocument`). `?format=`/`FORMAT_MIME` is RDF serialization negotiation, unrelated. A format in neither suffix set (e.g. `.blend`) is servable only from `/documents/`, which has no whitelist
- **Detail-route identifiers**: `/work/{guid}`, `/model/{cls}`, `/thesaurus/{term}` interpolate the path segment into a SPARQL `CONSTRUCT` as `<ns+name>`. `_valid_local_name()` in `query_support.py` rejects anything carrying whitespace, `<>"{}|^`\` or control chars, and `_rdf2html_stub()` turns that into a **404**. Without it a tab-prefixed but otherwise valid GUID raised a pyparsing `ParseException` that escaped as a 500 — 88 occurrences in six weeks of production logs
- **`/md2html` origin allowlist**: fetches only from this deployment's own origins (`host`, `domain`, `referenceModel`), compared on **host and port**. Host alone is insufficient — on a dev config of `localhost:8080` it would also permit `localhost:8090`, the agent. Unrestricted, the endpoint is an open proxy and an XSS vector: `mistune.html()` passes raw HTML through, so a remote markdown file containing `<script>` executes on this origin
- **HEAD**: FastAPI does not derive HEAD from GET. `build_app()` adds HEAD to every `APIRoute` that has GET, after registration — otherwise uptime monitors and crawlers get 405, including on `/`
- **`res/checks/`**: on-demand integrity shapes, deliberately **outside** the `res/*.shacl` glob so nothing runs them at load. `res/checks/labels.shacl` reports object-property values with no label — a **model/vocabulary** check (all 32 results on the reference dataset were `model/` or `thesaurus/` terms; no `work/` instances), destined for the dashboard as a post-modification check. Two requirements for any runner: pass **`advanced=True`** (it uses a SPARQL target; without advanced mode pyshacl reports "conforms" with zero results rather than erroring — it silently checks nothing), and run it against the **inferred `rdfs` store** (the `data` store has no `owl:ObjectProperty` declarations, so it yields zero targets). Costs ~13–15s, which is why it is not in the load path
- Mobile: check UA for Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini
- `isMobile` propagated as query param on relevant routes
- `the:mdDocument`, `the:pdfDocument`, `the:tag` — thesaurus namespace, not `vad:`
- Other special predicates (image, image3d, qrcode, media, hasArtistProfile, etc.) are `vad:`
- Always `qs.query_label(uri)` for a *single* display label — not `query_one_property` with RDFS.label. For many URIs at once use `qs.query_labels(uri_list)` (batched, alphabetical, CURI fallback)
- **`_ARTIST_PREDS` is gone** — `vad:hasArtistProfile/artist/background/pseudonymFor` and `the:tag` are all declared `owl:ObjectProperty`, so the discrete-anchor rule covers them. Do not re-add per-predicate label special cases; declare the property `owl:ObjectProperty` in the ontology instead
- **WSL2**: root-relative paths only in all generated HTML
- **RDFLib ORDER BY**: `str(?dt)` cast for xsd:dateTime — timezone grouping bug otherwise
- **Cytoscape**: populate scheme selectors from SPARQL at page load, not hardcoded
- **SPARQL browser**: always append `LIMIT 10000` if absent; run in `asyncio.to_thread()`
- **Thesaurus data**: concepts use `rdfs:label` (not `skos:prefLabel`) and `skos:inScheme` (not `skos:member`) — confirmed from TTL
- **RDFLib UNION + FILTER**: outer FILTER on a variable bound inside a UNION branch is unreliable; use a single direct predicate instead
- **RDFLib VALUES + OPTIONAL**: a bare `VALUES ?x {...}` followed by `OPTIONAL` is evaluated as an *inner* join, silently dropping every binding with no matching triple. Wrap the VALUES in a subselect — `{ SELECT ?x WHERE { VALUES ?x {...} } } OPTIONAL {...}` — to restore left-join semantics. Verified on rdflib 7.6.0; see `query_collection()`
- **RDFLib sequential OPTIONALs**: two OPTIONALs in a row lose the second binding. Use one OPTIONAL containing a UNION instead
- **SPARQL ORDER BY + SELECT alias**: ORDER BY precedes projection, so an alias defined in the SELECT clause is not reliably visible to ORDER BY. `BIND(... AS ?v)` in the WHERE clause instead
- **Alphabetical ordering**: `ORDER BY LCASE(?label)` — bare `ORDER BY ?label` is codepoint order and sorts all uppercase ahead of all lowercase
- **Token**: stored in `~/.secrets.rson` (never committed); validated by `util/token.py`
- **`/refresh` / `/cestfini`**: return 403 on direct GET; only reachable via `/cmd?token=…&cmd=…`
- **Logging**: use `log_out()` / `log_err()` from `util/logging.py` — never bare `print()`
- **`tail()`**: call as `tail()` with no args — it's a function, not a constant; reads cfg live from `Server.get_instance()`
- **Tags section**: property column uses literal string `"Tag"` (not predicate-derived); label is the anchor text; label row omitted; CURI fallback when no label
- **`vad:image3d`**: `_build_rows()` appends `3D Viewer` link (`/modelviewer?work=subject_curi`) after the inline model-viewer embed
- **`/modelviewer`**: dark-themed via `head(bg_color="#18181b", server=srv)`; additional `<style>` block in body overrides nav colors; single route handles both initial load and `?selectBkgnd=` change
- **GCP sync**: `cloud.tgt` is retired; routing via `path_map` dict keyed on `model`, `vocab`, `data`, `tags` — values are the full paths from cfg
- **Content layout**: `metacontent/model` and `metacontent/vocab` for shared ontology; `content/data`, `content/tags`, `content/documents`, `content/images` for user data; `thumbnails/` outside both repos
- **`welcomeText` / `contactEmail` / `copyrightName`**: optional config fields; omitting them falls back to visualartsdna.org defaults in `tail()` and `browse_works.py`
- **`query_backgrounds(work_uri)`**: NOT a `vad:Background` class query — traverses tag/collection/topic graph: `?series the:tag <work> ; the:tag ?col . ?col a the:Collection ; the:topic the:Background ; the:tag ?uri . ?uri a the:Image ; rdfs:label ?label`
