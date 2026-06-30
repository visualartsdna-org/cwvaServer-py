# CWVA Python Server

Python/FastAPI server for [VisualArtsDNA](https://visualartsdna.org) — a single server
handling all HTML pages, RDF data, static assets, and AI agent proxy.

---

## Quick Start

### Folder layout

```
~/cwva/
├── main/           # this repo (cwvaServer-py)
├── metacontent/    # shared ontology — model/ and vocab/
│   ├── model/
│   └── vocab/
├── content/        # your data — git repo
│   ├── data/       # artwork TTL files
│   ├── tags/       # tag TTL files
│   ├── documents/  # Markdown and PDF documents
│   ├── images/     # images (excluded from git — see content/.gitignore)
│   └── provenance/ # placeholder for future expansion
└── thumbnails/     # auto-generated — not in git, not in content/
```

`metacontent/model/` and `metacontent/vocab/` are populated automatically
from `referenceModel` (visualartsdna.org) on first startup if empty. Most
users need no action here. Advanced users can optionally clone the
`cwvaMetacontent` repository into `~/cwva/metacontent/` for version control
or community participation in ontology development.

### Local deployment (no cloud storage)

```bash
pip install -r requirements.txt
cp config/serverCwva.example.rson config/serverCwva.rson
# Edit serverCwva.rson — set paths to match your ~/cwva/ layout:
#   "model":     "/home/you/cwva/metacontent/model"
#   "vocab":     "/home/you/cwva/metacontent/vocab"
#   "data":      "/home/you/cwva/content/data"
#   "tags":      "/home/you/cwva/content/tags"
#   "documents": "/home/you/cwva/content/documents"
#   "images":    "/home/you/cwva/content/images"
#   "thumbnails":"/home/you/cwva/thumbnails"
#   "cloud": null
python main.py -cfg config/serverCwva.rson
```

Place TTL files in `content/data/` and `content/tags/`. No `GCP_BUCKET` env var needed.
The ontology is fetched automatically from `referenceModel` on first startup.

### GCP-backed deployment

```bash
export GCP_BUCKET=your-bucket-name
cp config/serverCwva.example.rson config/serverCwva.rson
# Edit serverCwva.rson:
#   "cloud": {"src": "ttl", "tgt": "/home/you/cwva"}
#   set paths as above under cloud.tgt
python main.py -cfg config/serverCwva.rson
```

TTL files sync from the bucket at startup and on every `/refresh`. Images and
documents are fetched on demand and cached locally.

---

## Requirements

- Python 3.11+
- GCP Application Default Credentials: `gcloud auth application-default login`

```bash
pip install -r requirements.txt
```

---

## Environment Variables

Never stored in config files or committed to git.

| Variable | Required | Purpose |
|---|---|---|
| `GCP_BUCKET` | When `cloud` is configured | GCP bucket name for TTL/image/document sync |
| `ANTHROPIC_API_KEY` | When `agentUrl` is configured | Anthropic API key (used by the agent service, not the server directly) |

```bash
export GCP_BUCKET=your-bucket-name
export ANTHROPIC_API_KEY=your-anthropic-key
```

---

## Running

```bash
python main.py -cfg config/serverCwva.rson
```

Copy `config/serverCwva.example.rson` to `config/serverCwva.rson` and fill in values
for your environment. The config file is excluded from git via `.gitignore`.

To bind port 80 on Linux as a non-root user, grant the capability once:

```bash
sudo setcap 'cap_net_bind_service=+ep' $(which python3)
```

Or run behind a reverse proxy (nginx, etc.) on a high port.

---

## Configuration

RSON is JSON with `#` comment support. See `config/serverCwva.example.rson` for a
fully annotated template. Key fields:

| Field | Type | Description |
|---|---|---|
| `port` | int | Port to listen on (80 for production) |
| `host` | string | Public base URL of this server, no trailing slash |
| `dir` | string | Base directory for static assets and `res/`; use `"."` |
| `cloud` | object | GCP sync: `{"src": "bucket-prefix", "tgt": "/local/path"}` |
| `data` | string | Absolute path to artwork instance TTL folder |
| `model` | string | Absolute path to ontology TTL folder |
| `vocab` | string | Absolute path to vocabulary TTL folder |
| `tags` | string | Absolute path to tag TTL folder |
| `images` | string | Absolute path to images cache folder |
| `thumbnails` | string | Absolute path to thumbnails cache folder |
| `documents` | string | Absolute path to documents cache folder |
| `domain` | string | Canonical domain for RDF URIs (e.g. `http://visualartsdna.org`) |
| `verbose` | bool | Log every request path to stdout (default: `false`) |
| `clobber` | bool | Overwrite local files on GCP sync (default: `false`) |
| `multithreaded` | bool | Parallel threads for GCP TTL sync (default: `false`) |
| `primaryHost` | bool | Reserved for multi-host deployments |
| `sparql` | bool | Enable the SPARQL browser at `/sparql` (default: `false`) |
| `agentUrl` | string | AI agent base URL; omit to disable the Ask page |
| `agentTimeout` | int | Seconds to wait for agent response (default: `60`) |

### ~/.secrets.rson

Required for token-authenticated `/cmd` commands. Create this file
in your home directory on every machine that will run the server
or send admin commands:

```json
{
  "secrets": {
    "phrase": "your-secret-phrase"
  }
}
```

Never committed to git — lives only on the server and on any
machine running `cwva_cmd.py`. Choose any phrase; it must match
on both ends.

---

## Tools

### cwva_cmd.py

Standalone admin utility for sending token-authenticated commands
to the server. Lives in `tools/` inside the server repo.

```bash
python ~/cwva/main/tools/cwva_cmd.py refresh  -H http://your-server -p 8080
python ~/cwva/main/tools/cwva_cmd.py cestfini -H http://your-server -p 8080
python ~/cwva/main/tools/cwva_cmd.py status   -H http://your-server -p 8080
# Token-authenticated. Queries server OS health remotely —
# system load, Python/Java processes, disk, logs, error count.

# Or set env var to avoid typing --cfg every time
export CWVA_CFG=~/cwva/main/config/serverCwva.rson
python ~/cwva/main/tools/cwva_cmd.py status
```

Requires `~/.secrets.rson` with the same phrase as the server.
No dependencies outside the Python standard library.

---

## Production Deployment (Caddy)

The server runs on an internal port (e.g. 8080) behind [Caddy](https://caddyserver.com),
which handles TLS termination, automatic Let's Encrypt certificates, and HTTP→HTTPS redirect.

**Caddyfile:**
```
visualartsdna.org {
    reverse_proxy localhost:8080
}
```

**`serverCwva.rson` for production:**
```json
{
  "port": 8080,
  "host": "https://visualartsdna.org",
  "domain": "http://visualartsdna.org"
}
```

Set `host` to the public-facing HTTPS URL, not the internal port. This value is used
in nav links and URI rehosting — if it points to `localhost:8080` the rendered HTML
will contain URLs clients cannot reach.

The server trusts `X-Forwarded-For` from `127.0.0.1` (Caddy), so client IPs are
recorded correctly in metrics and rate limiting applies per real IP, not Caddy's address.

---

## Routes

### HTML Pages

| Route | Description |
|---|---|
| `GET /` | Gallery — paginated artwork grid, sort by Date or Title |
| `GET /browseSort` | Gallery re-sort (`?order=Date\|Title`) |
| `GET /browseFilter` | Gallery filter (`?artist=all\|name`) |
| `GET /work/{guid}` | Artwork detail page |
| `GET /model/{class}` | Ontology class detail page |
| `GET /thesaurus/{term}` | Thesaurus term detail page |
| `GET /vocabTree` | Concepts / vocabulary tree |
| `GET /explore` | Explore — Cytoscape graphs, collections, data visualizations |
| `GET /sparql` | Built-in SPARQL browser (rate-limited, timeout-guarded) |
| `GET /agentClient` | Ask/AI page (requires `agentUrl`) |
| `GET /about` | About page |

### RDF Data Endpoints

All detail routes (`/work/`, `/model/`, `/thesaurus/`) support RDF format negotiation
via `?format=` or `Accept` header: `ttl`, `rdf/xml`, `n-triples`, `n3`, `jsonld`.

| Route | Description |
|---|---|
| `GET /model` | Schema/ontology store (Turtle default) |
| `GET /vocab` | Vocabulary store |
| `GET /data` | Merged instance+tag+vocab store |
| `GET /rdfs` | RDFS-inferred store |
| `GET,POST /sparqlEndpoint` | SPARQL 1.1 endpoint — returns `application/sparql-results+json` |

### Static and Binary Assets

| Route | Description |
|---|---|
| `GET /dist/*` | JS bundles (served from `{dir}/dist/`) |
| `GET /html/*` | Static HTML files (served from `{dir}/html/`) |
| `GET /images/*` | Images (jpg, png, gif, webp, glb, ico, usdz) — on-demand GCP fetch |
| `GET /thumbnails/*` | Resized thumbnails (≤700 px wide) — on-demand GCP fetch |
| `GET /documents/*` | PDF and Markdown documents — on-demand GCP fetch |
| `GET /favicon.ico` `/favicon.png` | Favicon |

### Operations

| Route | Description |
|---|---|
| `GET /status` | Health check — `{"status": "ok"}` |
| `GET /metrics` | In-memory request metrics (JSON) |
| `GET /metricTables` | Metrics dashboard HTML (served from GCS `stats/chart.html`) |
| `GET /explore/graph-data` | Cytoscape node/edge JSON for Explore page |
| `GET /md2html?doc={url}` | Fetch a Markdown URL and return it as HTML |
| `POST /agent/query` | Proxy to `agentUrl`; rate-limited 10 req/10 min/IP |
| `GET /refresh` | 403 — use `/cmd?token=…&cmd=refresh` |
| `GET /cestfini` | 403 — use `/cmd?token=…&cmd=cestfini` |
| `GET /metrics` | Pretty JSON metrics dump (no token required) |
| `GET /cmd?token={t}&cmd={c}` | Token-validated commands: `refresh` (reload data), `cestfini` (push metrics + shutdown) |

---

## Project Structure

```
cwva-server/
├── main.py                    # entry point
├── server.py                  # Server singleton (cfg, dbm, started_at)
├── servlet.py                 # primary routes + FastAPI app factory
├── servlet_base.py            # secondary routes + static asset handlers
├── requirements.txt
├── config/
│   └── serverCwva.example.rson   # config template
├── rdf/
│   ├── db_mgr.py              # TTL loading, RDFS inference, SKOS rules, Policy
│   ├── query_support.py       # all SPARQL queries
│   ├── prefixes.py            # namespace prefixes + FOR_QUERY string
│   └── policy.py              # SPARQL UPDATE runner
├── services/
│   ├── browse_works.py        # Gallery page
│   ├── rdf2html.py            # detail page (works, model classes, thesaurus terms)
│   ├── sparql_browser.py      # built-in SPARQL browser
│   ├── explore.py             # Explore page (Cytoscape graphs + collections)
│   ├── vocab_tree.py          # Concepts page
│   ├── about.py               # About page
│   └── agent_client.py        # Ask/AI page
├── util/
│   ├── rson.py                # RSON config loader
│   ├── gcp.py                 # GCP bucket sync, on-demand fetch, metrics push
│   ├── html_template.py       # HTML head/nav/tail templates
│   ├── metrics.py             # request metrics and UA classifier
│   ├── logging.py             # log_out() → stdout, log_err() → stderr (ISO 8601)
│   └── token.py               # /cmd token validation
└── res/
    ├── Policy.upd             # SPARQL UPDATE policy (not committed)
    ├── servletPolicy.rson     # path policy config
    └── *.shacl                # SHACL validation shapes
```

---

## Architecture

```
Client
  │
  ▼
CWVA Server  (Python/FastAPI)
  ├── HTML pages — RDFLib SPARQL queries → rendered HTML
  ├── RDF stores — RDFS inference via owlrl, SKOS rules, Policy UPDATEs
  ├── Static assets — on-demand GCP fetch + local cache
  ├── SPARQL browser — built-in, rate-limited, timeout-guarded
  └── AI agent proxy — /agent/query → agentUrl
```

The RDF data layer loads at startup: instance TTL files are merged with vocabulary
and ontology, RDFS inference is applied, then SKOS rules and Policy UPDATEs run.
The resulting `rdfs` graph is the primary query target for all page rendering.

---

## Data Model

The server loads five RDF stores:

| Store | Source | Purpose |
|---|---|---|
| `instances` | `cfg.data` | Artwork instance files |
| `tags` | `cfg.tags` | Tag files |
| `vocab` | `cfg.vocab` | SKOS vocabulary files |
| `schema` | `cfg.model` | Ontology (cwva.ttl, entity.ttl, etc.) |
| `rdfs` | all of the above + inference | Primary query target |

---

## Ontology and Vocabulary

The ontology and vocabulary are also served as linked data:

- Ontology documentation: [LODE server](https://w3id.org/lode/owlapi/https://visualartsdna.org/model/)
- Ontology RDF: `/model` (Turtle) or with `?format=` for other serializations
- Vocabulary RDF: `/vocab`
- Archived at: [DBpedia Archivo](https://archivo.dbpedia.org/info?o=http://visualartsdna.org/model/)

## Getting Help
**Sample Data** — see DATA_GUIDE.md for sample data and data development best practices.

**Deployment problems** — server won't start, config issues, 
path errors:  
Share your `serverCwva.rson` (remove any API keys), the startup 
log output, your OS, and Python version with Claude.

**Development and extension** — modifying or extending the code:  
Share `CLAUDE.md` with Claude for full architecture context, 
key decisions, and implementation notes.


