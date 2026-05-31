# CWVA Roadmap

Ideas and future directions. Not commitments — priorities will shift.

---

## Immediate (pre-commit)

- [ ] LICENSE — MIT
- [ ] git init and initial commit to `visualartsdna-org/cwvaServer-py`

### Notes
- SPARQL Update via `/sparqlEndpoint` is blocked (403). Enable via
  `"sparqlUpdates": true` config when needed — currently no use case.

---

## Near-term (v5.1)

### Conditional GCP metrics
When `GCP_BUCKET` is not set or `cloud` is null:
- Metrics Dashboard removed from Explore page Quick Access
- Cestfini skips GCP snapshot (metrics still dumped to `cwva.log` on shutdown)
- `/metricTables` returns a "not available in local deployment" page
- In-memory metrics and rate limiting continue to work unchanged

The cestfini log dump already covers immediate needs for local deployments —
the full metrics JSON is written to `cwva.log` at every shutdown.

### Reference model fetch from visualartsdna.org
A config option to fetch the canonical ontology and thesaurus from the
reference deployment at startup and refresh:

```json
"referenceModel": "https://visualartsdna.org"
```

When set, `DBMgr._load()` fetches:
- `https://visualartsdna.org/model?format=ttl` → local `model/cwva.ttl`
- `https://visualartsdna.org/thesaurus?format=ttl` → local `model/thesaurus.ttl`

Fails gracefully — if the reference server is unreachable the local cached
copy is used. A local deployer gets a working ontology without managing model
TTL files. Ontology updates at visualartsdna.org propagate automatically to
downstream deployments on next refresh.

The browser page redirect variant (redirect `/model/{cls}` misses to
`referenceModel/model/{cls}`) is a separate future feature — see below.

### Reference model proxy for browser page
A request to `/model/{cls}` or `/thesaurus/{term}` that finds no local match
redirects to `referenceModel/model/{cls}` or `referenceModel/thesaurus/{term}`.
Lets an implementor build a specialized collection using the CWVA ontology
without replicating or maintaining the full model locally.

---

## Near-term (v5.1) — GitHub-backed deployment

Add `provider: github` as an alternative to GCP for TTL data sync.
Users clone their data repository once; the server does `git pull --ff-only`
on refresh. Free data management with full version history for self-hosted
deployments.

```json
"cloud": {
    "provider": "github",
    "repo_path": "/home/user/cwvaContent"
}
```

Implementation: `git pull --ff-only` via subprocess in a new `util/github_sync.py`.
The sync dispatcher in `DBMgr._load()` routes to GCP or git based on
`cfg["cloud"]["provider"]`. Clobber concept does not apply to git sync —
git manages what is new, changed, or deleted.

Images are managed separately — see Hosted Image Support below.

```python
# util/sync.py — dispatcher
def sync_data(cfg):
    provider = (cfg.get("cloud") or {}).get("provider", "gcp")
    if provider == "github":
        github_sync(cfg["cloud"]["repo_path"])
    elif provider == "gcp":
        gcp_cp_dir_recurse(...)
    # cloud: null → skip
```

---

## Near-term (v5.1) — Hosted Image Support

Support external image URIs in `schema:image` (e.g. Postimages, Cloudinary,
Imgur). Server fetches and caches on first request, generates thumbnail from
cached copy. Enables fully self-contained deployments with no GCP dependency
and no binary files in the data repository.

TTL references the canonical image URI — proper linked data practice:
```turtle
work:abc schema:image <https://i.postimg.cc/xyz/painting.jpg> .
```

The server detects external URIs in `schema:image`, fetches and caches
transparently, serves thumbnails from cache. No TTL change required for
existing works — local GCP-backed images continue to work alongside
externally hosted ones.

Benefit for free-tier GCP deployment: image traffic bypasses the VM entirely,
keeping egress well within the 1GB/month free tier cap.

---

## Near-term (v5.1) — Free-tier GCP Deployment Guide

Document the recommended zero-cost production configuration:
- GCP e2-micro (0.25 vCPU burst to 2, 1GB RAM, 30GB disk) in us-central1
- HTTP only — no TLS required for read-only public art data
- Externally hosted images (Postimages or similar) to eliminate image egress
- GCP bucket for TTL only (same-region — no egress charges)
- `referenceModel: "https://visualartsdna.org"` for ontology
- Metrics via cestfini log dump; optional nightly compiler cron

Practical limits:
- Personal portfolio (50 works, low traffic): excellent
- Small collection (100–200 works, modest traffic): good
- Production scale (295+ works, bot traffic): marginal on RAM and egress

TLS via Caddy is available as an optional enhancement — see TLS section below.

---

## Medium-term (v5.2)

### Log-based metrics for local deployments
When GCP is not configured, the cestfini log dump provides metrics data in
JSON format. Add `--log-file` option to `metricsCompiler.py` as an alternative
input to GCP snapshots:

```bash
python tools/metricsCompiler.py --log-file ~/cwva/main/cwva.log --test
```

The compiler finds metrics JSON blocks in the log (each followed by a `fini`
line), aggregates by date/IP/path, and produces the same Chart.js dashboard.
No GCP required. Enables chart generation for any deployment that keeps logs.

Structured JSON request logging (one line per request) would make log-based
metrics more granular — every request recorded, not just session aggregates.
Worth considering if demand exists.

### TLS via Caddy (optional enhancement)
For deployments requiring HTTPS — corporate networks, browsers that flag
HTTP warnings as blockers, or SEO considerations:

```
# Caddyfile
visualartsdna.org {
    reverse_proxy localhost:8080
}
```

Caddy handles Let's Encrypt certificates automatically. The CWVA server
requires no changes — all URLs are already root-relative. Not required for
a read-only personal collection (no user data, no transactions, no credentials).

### Ask page — production agent integration
The Ask page (`/agentClient`) is currently stubbed when `agentUrl` is null.
Full integration requires the SPARQL agent running on the same host:
- Move agent to production GCP instance alongside cwva server
- Configure `agentUrl: "http://localhost:8090"` in production rson
- Test end-to-end with Claude API key in `ANTHROP_KEY` env var

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
- **Ontology and RDF tutoring** — explains concepts (classes, properties,
  triples, inference) in context, using the user's own data as examples
- **Data validation** — checks generated TTL against the model before writing
  files, explains violations in plain language

Implementation: standalone script in `tools/`, talks to the Claude API, writes
TTL into the local content folder. Requires `ANTHROP_KEY` env var.

### Windows Support
The server runs on Windows with no code changes. The shell scripts (`start`,
`stop`, `refresh`, `status`) are Linux/macOS only. PowerShell analogs would
make Windows a first-class deployment target if there is demand.


--- 

## AR Mode for 3D Works

Android and Meta Quest AR work natively via model-viewer WebXR/
Scene Viewer (GLB direct — no conversion needed). iOS AR requires
USDZ sidecar via AR Quick Look — generate from original Blender
source rather than converting from GLB.

Apple Vision Pro is the longer-term spatial computing target —
same USDZ/Reality format as iOS, significantly richer presentation.

model-viewer handles platform detection automatically:
ar-modes="webxr scene-viewer quick-look"

---

## Notes

- Priorities will shift based on community interest after open source release
- The authoring agent concept is the highest-impact longer-term feature —
  it makes the system accessible to domain experts without RDF background
- Free-tier GCP + GitHub TTL + hosted images = zero-cost production deployment
  for a personal collection — worth validating as a complete reference path
