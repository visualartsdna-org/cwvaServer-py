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

## v5.1 — RELEASED

`VERSION` is `5.1.0`, tagged `v5.1.0`, merged to `main`. QA passed.

The feature cycle is documented in **[V5.1PLAN.md](V5.1PLAN.md)** — full design
notes, decisions and verification for each item:

| # | Item |
|---|---|
| 1 | AI-generated content notice — `the:AI` pages, configurable text and icon |
| 2 | Video support — `schema:video`, `/media` route with HTTP Range, native player |
| 3 | Labels instead of CURIs for ObjectProperty values (discrete-anchor rule) |
| 4 | Alphabetical ordering of collection members, ordered in SPARQL |
| 5 | Model/vocabulary label integrity shape — `res/checks/labels.shacl` |

Also delivered in this release:

| Item | Status |
|---|---|
| Reference model fetch | ✓ complete — `util/reference.py`, `rdf/db_mgr.py` |
| Free-tier GCP deployment guide | ✓ complete — documentation only, below |
| Conditional GCP metrics | ◐ partial — one piece outstanding, see below |
| Production log fixes | ✓ five issues found in 5.0 logs — see V5.1PLAN.md |

The log fixes closed the two sources of 500s (detail-route identifier
validation, `/documents/` directory read), an unauthenticated open fetch proxy
on `/md2html`, the missing `robots.txt` (5,237 404s), and HEAD returning 405
across the whole site. All were pre-existing in 5.0.

### Conditional GCP metrics ◐ PARTIAL
When `GCP_BUCKET` is not set or `cloud` is null:
- Metrics Dashboard removed from Explore page Quick Access
- Cestfini skips GCP snapshot (metrics still dumped to `cwva.log` on shutdown)
- `/metricTables` returns a "not available in local deployment" page
- In-memory metrics and rate limiting continue to work unchanged

The cestfini log dump already covers immediate needs for local deployments —
the full metrics JSON is written to `cwva.log` at every shutdown.

**Remaining:** `/metricTables` currently returns a bare `PlainTextResponse`
("GCP_BUCKET not configured", 503) rather than the styled "not available in
local deployment" page described above — see `servlet_base.py`. The Explore
page Quick Access link is likewise still unconditional.

### Reference model fetch from visualartsdna.org ✓ COMPLETE
Fetches canonical ontology and vocab from the reference deployment at startup
and refresh via `referenceModel: "https://visualartsdna.org"` config field.
Fails gracefully — local cached copy used if reference server is unreachable.
Implemented in `util/reference.py`, invoked from `rdf/db_mgr.py`.

### Free-tier GCP Deployment Guide ✓ COMPLETE (documentation)
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

Note the externally-hosted-images line depends on Hosted Image Support, which
is still pending under v5.2 below.

---

## Near-term (v5.2)

Three items carried forward from the v5.1 scope. None started.

| Item | Status |
|---|---|
| Reference model proxy for browser page | ☐ not started |
| GitHub-backed deployment | ☐ not started |
| Hosted image support | ☐ not started |

### Reference model proxy for browser page ☐ NOT STARTED
A request to `/model/{cls}` or `/thesaurus/{term}` that finds no local match
redirects to `referenceModel/model/{cls}` or `referenceModel/thesaurus/{term}`.
Lets an implementor build a specialized collection using the CWVA ontology
without replicating or maintaining the full model locally.

The detail routes in `servlet.py` currently have no redirect path — a miss
falls through to the standard not-found page.

### GitHub-backed deployment ☐ NOT STARTED
`provider: github` as an alternative to GCP for TTL data sync. Users clone
their data repository once; the server does `git pull --ff-only` on refresh.
A `util/sync.py` dispatcher would front both providers; it does not exist yet,
and today's sync path is GCP-only (`util/gcp.py`, called from `rdf/db_mgr.py`).

**Folder sync parameterization** — a prerequisite for this work, not a
separate task. GCP sync clears and re-populates all configured folders before
each load — the bucket is sole source of truth. Git sync must not pre-clear;
git manages its own deletes and renames. Mixed deployments (git for user data
with `referenceModel` for ontology, or git for ontology with GCP for data)
therefore need a per-folder sync provider config:

```json
"sync": {
    "data":  "gcp",
    "tags":  "gcp",
    "model": "reference",
    "vocab": "git"
}
```

### Hosted Image Support ☐ NOT STARTED
Support external image URIs in `schema:image` (Postimages, Cloudinary, etc.).
Server fetches and caches on first request, generates thumbnail from cached copy.
Benefit for free-tier GCP: image traffic bypasses the VM entirely.

**Blocker to clear first:** `_href()` in `services/rdf2html.py` and the gallery
thumbnail path in `services/browse_works.py` unconditionally reduce every URI
to `urlparse(uri).path`, so an absolute external URL is truncated to its path
component (`https://i.postimg.cc/abc.jpg` → `/abc.jpg`). Those call sites must
pass absolute off-host URLs through unchanged before hosted images can work.
Note this rule exists for a reason — see the root-relative/WSL2 decision in
CLAUDE.md — so the fix is a host check, not removal.

---

## Medium-term (v6.0) — Database Configuration

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

v6.0 will implement and validate this configuration — running Fuseki + TDB2
parallel with RDFLib in-memory, comparing query results, inference output, and
page rendering across the full production path set before cutting over.

See [DATABASE.md](DATABASE.md) for full implementation details and
[DBTESTPLAN.md](DBTESTPLAN.md) for the v6.0 test plan.

### Log-based metrics for local deployments
Add `--log-file` option to `metricsCompiler.py` as an alternative input to
GCP snapshots. The compiler finds metrics JSON blocks in the log (each followed
by a `fini` line), aggregates by date/IP/path, and produces the same Chart.js
dashboard. No GCP required.

**Note:** `tools/metricsCompiler.py` is not in this repo (never committed) —
it currently lives only on the production host. Bring it into `tools/` before
extending it, so the `/metricTables` pipeline is reproducible from a clone.

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
- Test end-to-end with Claude API key in `ANTHROPIC_API_KEY` env var

### Security audit logging
Known scanner patterns (Log4Shell attempts, ONVIF probes, etc.) currently
log to stdout. Route to stderr so `cwva_err.log` becomes a useful security
audit trail. A simple path/UA classifier distinguishes scanner traffic from
legitimate unknown paths.

*(Folder sync parameterization moved to the v5.2 GitHub-backed deployment
entry above — it is a prerequisite of that work, not a separate v6.0 item.)*

---

## Longer-term

### Integration with the concept derivation/tagging agent

(see `concept_agent_design.md` — maintained with the concept agent, not in this repo)

At tagging time the concept agent generates a concise critical abstract alongside tag output in a single Claude API call. The summary is stored as vad:hasSummary on the document instance in the TTL output — generated once, stored permanently, no API call at browse time.
Format: one or two paragraphs, 100-150 words, present tense, third person. Written as a critical abstract grounded in the tagged concepts.
Displayed in rdf2html.py as a styled block above the property table for criticism document instances — a fast reading path for large documents without opening the full markdown.
Requires vad:hasSummary added to the cwva ontology as a datatype property. See concept_agent_design.md, Document Summary Generation section for full implementation details.

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
TTL into the local content folder. Requires `ANTHROPIC_API_KEY` env var.

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
- v6.0 is a dedicated database configuration release — meaningful architectural
  change deserving its own version and proper testing
