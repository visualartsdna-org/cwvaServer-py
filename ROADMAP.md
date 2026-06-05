# CWVA Roadmap

Ideas and future directions. Not commitments — priorities will shift.

---

## Immediate (pre-commit) ✓ COMPLETE

- [x] LICENSE — MIT
- [x] git init and initial commit to `visualartsdna-org/cwvaServer-py`

### Notes
- SPARQL Update via `/sparqlEndpoint` is blocked (403). Enable via
  `"sparqlUpdates": true` config when needed — currently no use case.

---

## Near-term (v5.1) ✓ COMPLETE

### Conditional GCP metrics
When `GCP_BUCKET` is not set or `cloud` is null:
- Metrics Dashboard removed from Explore page Quick Access
- Cestfini skips GCP snapshot (metrics still dumped to `cwva.log` on shutdown)
- `/metricTables` returns a "not available in local deployment" page
- In-memory metrics and rate limiting continue to work unchanged

The cestfini log dump already covers immediate needs for local deployments —
the full metrics JSON is written to `cwva.log` at every shutdown.

### Reference model fetch from visualartsdna.org
Fetches canonical ontology and vocab from the reference deployment at startup
and refresh via `referenceModel: "https://visualartsdna.org"` config field.
Fails gracefully — local cached copy used if reference server is unreachable.

### Reference model proxy for browser page
A request to `/model/{cls}` or `/thesaurus/{term}` that finds no local match
redirects to `referenceModel/model/{cls}` or `referenceModel/thesaurus/{term}`.
Lets an implementor build a specialized collection using the CWVA ontology
without replicating or maintaining the full model locally.

### GitHub-backed deployment
`provider: github` as an alternative to GCP for TTL data sync. Users clone
their data repository once; the server does `git pull --ff-only` on refresh.
See `util/sync.py` dispatcher pattern.

### Hosted Image Support
Support external image URIs in `schema:image` (Postimages, Cloudinary, etc.).
Server fetches and caches on first request, generates thumbnail from cached copy.
Benefit for free-tier GCP: image traffic bypasses the VM entirely.

### Free-tier GCP Deployment Guide
Recommended zero-cost configuration:
- GCP e2-micro (0.25 vCPU burst to 2, 1GB RAM, 30GB disk) in us-central1
- HTTP only — no TLS required for read-only public art data
- Externally hosted images to eliminate image egress
- GCP bucket for TTL only (same-region — no egress charges)
- `referenceModel: "https://visualartsdna.org"` for ontology
- Metrics via cestfini log dump; optional nightly compiler cron

Practical limits:
- Personal portfolio (50 works, low traffic): excellent
- Small collection (100–200 works, modest traffic): good
- Production scale (295+ works, bot traffic): marginal on RAM and egress

TLS via Caddy is available as an optional enhancement — see TLS section below.

---

## Medium-term (v5.2) — Database Configuration

### Large-Scale Deployment — Apache Jena Fuseki + TDB2

For very large deployments — federated catalogs aggregating hundreds of
artists, or collections exceeding one million triples — replace RDFLib's
in-memory graph with Apache Jena Fuseki + TDB2.

Fuseki exposes a standard SPARQL endpoint. RDFLib queries it via
`SPARQLStore` — the migration surface in cwvaServer-py is confined to
`db_mgr.py`. All query, rendering, and application code is unchanged.

The full inference pipeline moves into Jena: RDFS reasoner, skos.rules in
native Jena rule syntax, Policy.upd via standard SPARQL 1.1 Update (IRI()
handled natively), and SHACL via jena-shacl. cwvaServer-py becomes a pure
HTTP/HTML application querying a Fuseki endpoint.

A `sparqlEndpoint` config field signals db_mgr.py to use the remote store:
```json
"sparqlEndpoint": "http://localhost:3030/cwva/sparql"
```

When absent, existing RDFLib in-memory behavior is unchanged. CE deployments
are unaffected.

v5.2 will implement and validate this configuration — running Fuseki + TDB2
parallel with RDFLib in-memory, comparing query results, inference output, and
page rendering across the full production path set before cutting over.

See [DATABASE.md](DATABASE.md) for full implementation details and
[DBTESTPLAN.md](DBTESTPLAN.md) for the v5.2 test plan.

### Log-based metrics for local deployments
Add `--log-file` option to `metricsCompiler.py` as an alternative input to
GCP snapshots. The compiler finds metrics JSON blocks in the log (each followed
by a `fini` line), aggregates by date/IP/path, and produces the same Chart.js
dashboard. No GCP required.

### TLS via Caddy (optional enhancement)
```
# Caddyfile
visualartsdna.org {
    reverse_proxy localhost:8080
}
```
Caddy handles Let's Encrypt certificates automatically. Not required for a
read-only personal collection.

### Ask page — production agent integration
Full integration requires the SPARQL agent running on the same host:
- Move agent to production GCP instance alongside cwva server
- Configure `agentUrl: "http://localhost:8090"` in production rson
- Test end-to-end with Claude API key in `ANTHROP_KEY` env var

### Security audit logging
Known scanner patterns (Log4Shell attempts, ONVIF probes, etc.) currently
log to stdout. Route to stderr so `cwva_err.log` becomes a useful security
audit trail. A simple path/UA classifier distinguishes scanner traffic from
legitimate unknown paths.

**Folder sync parameterization:** 
GCP sync clears and re-populates
all configured folders before each load — bucket is sole source of
truth. Git sync does not pre-clear — git manages its own deletes
and renames. Mixed deployments (e.g. git for user data, referenceModel
for ontology, or git for ontology with GCP for data) require a
per-folder sync provider config:

```json
"sync": {
    "data":  "gcp",
    "tags":  "gcp",
    "model": "reference",
    "vocab": "git"
}
```

This parameterization is the v5.1 GitHub-backed deployment design
decision — defer until that work begins.
---

## Longer-term

### Authoring Agent (`cwva_author.py`)
A CLI tool (or lightweight web UI) that lowers the barrier to creating RDF
content for non-technical users — artists, curators, educators — who are
domain experts but not RDF practitioners.

Capabilities envisioned:
- **Guided installation and configuration** — walks a new user through setup,
  explains each config field, validates the result
- **TTL synthesis from natural language** — given a description of an artwork,
  a document, or dictated notes, generates valid Turtle conforming to the CWVA
  ontology
- **Concept extraction and tag generation** — extracts concepts from notes and
  criticism, matches against the thesaurus, proposes new terms where needed,
  generates tag TTL with artist confirmation
- **Transitional misunderstanding capture** — identifies moments in criticism
  where an older interpretive model reorganizes, preserving the evolution of
  artistic seeing as structured data
- **Ontology and RDF tutoring** — explains concepts in context using the
  user's own data as examples
- **Data validation** — checks generated TTL against the model before writing,
  explains violations in plain language

Implementation: standalone script in `tools/`, talks to the Claude API, writes
TTL into the local content folder. Requires `ANTHROP_KEY` env var.

### Demo Dataset
A minimal curated dataset — 10–20 works with a stripped-down ontology and a
few vocabulary files — that lets someone evaluate the system end-to-end
immediately after cloning the repo. Candidate home: a separate
`cwvaContent-demo` repository.

### Windows Support
The server runs on Windows with no code changes. The shell scripts (`start`,
`stop`, `refresh`, `status`) are Linux/macOS only. PowerShell analogs would
make Windows a first-class deployment target if there is demand.

### AR Mode for 3D Works

Android and Meta Quest AR work natively via model-viewer WebXR/Scene Viewer
(GLB direct — no conversion needed). iOS AR requires USDZ sidecar via AR
Quick Look — generate from original Blender source rather than converting
from GLB. Apple Vision Pro is the longer-term spatial computing target.

```html
<model-viewer src="work.glb" ios-src="work.usdz"
              ar ar-modes="webxr scene-viewer quick-look"
              camera-controls>
</model-viewer>
```

### Federated Gallery and Artist Directory

visualartsdna.org could maintain a directory of deployed cwvaServer-py
instances and optionally query remote `/sparqlEndpoint` endpoints to display
works from multiple artists in a shared gallery. Each artist maintains full
ownership and control. No central database — the gallery is a view, not a copy.

### A Living Vocabulary

The shared ontology represents the universal grammar of creative work. The
thesaurus is where practice diverges — domain-specific concept schemes
maintained by practitioner communities.

Deployment levels:
- **CE default** — `referenceModel` fetch, zero management
- **Pinned version** — clone `cwva-ontology` at a known-good tag
- **Extended model** — domain-specific TTL alongside community ontology
- **Community contribution** — pull requests to `cwva-ontology`

On staying current: the server could log a notice when the local ontology
repo is behind its remote, leaving the update decision to the user. A
`"ontologyAutoUpdate": true` config flag could enable automatic pull.
A `referenceTag` field alongside `referenceModel` would allow pinning
to a known-good version.

---

## Architecture

The system architecture across all deployment configurations:

![CWVA Architecture](https://visualartsdna.org/images/systemArchitectureV1.0.jpg)

Five layers:
- **Source of truth** — GCP bucket, Git repo, Local disk, visualartsdna.org (referenceModel)
- **cwvaServer-py** — db_mgr (load/infer/policy) → data stores (RDFLib / Fuseki+TDB2 / any SPARQL store) → servlet + query_support
- **Clients** — Browser (desktop/mobile), AR viewer, Search engine crawlers, Remote federated nodes
- **Agents** — SPARQL agent, Authoring agent, Metrics compiler, cwva_cmd
- **Federation** (future) — Artist directory, Federated gallery, cwva-ontology governance

Also available via [System Documentation](https://visualartsdna.org/thesaurus/OperationalCollection) on the live server.

---

## Notes

- Priorities will shift based on community interest after open source release
- The authoring agent is the highest-impact longer-term feature — it makes
  the system accessible to domain experts without RDF background
- Free-tier GCP + GitHub TTL + hosted images = zero-cost production deployment
  for a personal collection — worth validating as a complete reference path
- v5.2 is a dedicated database configuration release — meaningful architectural
  change deserving its own version and proper testing
