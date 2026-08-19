"""Secondary route handlers — port of ServletBase.groovy."""

import asyncio
import os
import re
import mimetypes
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from server import Server
from util import metrics
from util.logging import log_out, log_err

router = APIRouter()

MOBILE_RE = re.compile(
    r"Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini", re.IGNORECASE
)

_SPARQL_UPDATE_RE = re.compile(
    r'\b(INSERT|DELETE|DROP|CLEAR|CREATE|COPY|MOVE|ADD|LOAD)\b', re.IGNORECASE
)


def _is_sparql_update(query: str) -> bool:
    return bool(_SPARQL_UPDATE_RE.search(query))


def is_mobile(request: Request) -> bool:
    ua = request.headers.get("user-agent", "")
    return bool(MOBILE_RE.search(ua))


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

@router.get("/status")
async def status():
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# /status/os — token-validated OS health (system, processes, disk, logs)
# ---------------------------------------------------------------------------

@router.get("/status/os")
async def status_os(request: Request):
    from util.token import validate as token_validate
    token = request.query_params.get("token", "")
    if not token_validate(token):
        log_err(f"Invalid token for /status/os from {metrics.get_ip(request)}")
        return Response("Unauthorized", status_code=401)

    import subprocess

    def run(cmd: str) -> str:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            return r.stdout.strip() or r.stderr.strip()
        except Exception as e:
            return str(e)

    return JSONResponse({
        "system":  run("top -b -n1 | head -5"),
        "python":  run("top -b -n1 | grep python | head -3"),
        "java":    run("top -b -n1 | grep java | head -3"),
        "disk":    run("df | grep sda1 || df -h | head -5"),
        "logs":    run("ls -lh *.log 2>/dev/null || echo 'no logs in cwd'"),
        "errors":  run("grep -ic 'exception\\|error\\|traceback' *err.log 2>/dev/null || echo 0"),
    })


# ---------------------------------------------------------------------------
# /metrics — raw JSON metrics (internal)
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def get_metrics_endpoint(request: Request):
    import json as _json
    return Response(
        content=_json.dumps(metrics.get_all(), indent=2),
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# /metricTables — metrics dashboard (chart.html from GCS stats/ folder)
# ---------------------------------------------------------------------------

@router.get("/metricTables")
async def metric_tables(request: Request):
    srv = Server.get_instance()
    bucket_name = os.environ.get("GCP_BUCKET")
    if not bucket_name:
        return PlainTextResponse("GCP_BUCKET not configured", status_code=503)
    try:
        from util.gcp import _get_client
        blob = _get_client().bucket(bucket_name).blob("stats/chart.html")
        content = blob.download_as_text()
        return Response(content=content, media_type="text/html; charset=utf-8")
    except Exception as e:
        Server.log_out(f"metricTables: could not fetch stats/chart.html: {e}")
        return PlainTextResponse(f"Metrics dashboard not available: {e}", status_code=404)


# ---------------------------------------------------------------------------
# /sparqlEndpoint — internal SPARQL (Stage 2 will wire up real graph)
# ---------------------------------------------------------------------------

@router.get("/sparqlEndpoint")
@router.post("/sparqlEndpoint")
async def sparql_endpoint(request: Request, query: str = ""):
    """SPARQL 1.1 Protocol endpoint.

    Accepts:
      GET  ?query=<sparql>
      POST application/x-www-form-urlencoded  query=<sparql>
      POST application/sparql-query           (raw SPARQL body)

    Returns application/sparql-results+json in SPARQL 1.1 format.
    """
    srv = Server.get_instance()
    if srv.dbm is None:
        return JSONResponse({"error": "data layer not loaded"}, status_code=503)

    if not query:
        ct = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in ct:
            form = await request.form()
            query = form.get("query", "")
        else:
            body = await request.body()
            query = body.decode("utf-8", errors="replace")

    if not query:
        return JSONResponse({"error": "query parameter required"}, status_code=400)

    if _is_sparql_update(query):
        log_err(f"Blocked SPARQL Update from {metrics.get_ip(request)}: {query[:100]}")
        return JSONResponse(
            {"error": "SPARQL Update operations are not permitted"},
            status_code=403,
        )

    try:
        from rdf.prefixes import FOR_QUERY
        from rdflib import URIRef, BNode
        from rdflib.term import Literal as RDFLiteral
        import json as _json

        results = srv.dbm.rdfs.query(FOR_QUERY + query)

        vars_ = [str(v) for v in results.vars]
        bindings = []
        for row in results:
            binding = {}
            for var, val in zip(results.vars, row):
                if val is None:
                    continue
                if isinstance(val, URIRef):
                    binding[str(var)] = {"type": "uri", "value": str(val)}
                elif isinstance(val, BNode):
                    binding[str(var)] = {"type": "bnode", "value": str(val)}
                elif isinstance(val, RDFLiteral):
                    entry = {"type": "literal", "value": str(val)}
                    if val.language:
                        entry["xml:lang"] = val.language
                    elif val.datatype:
                        entry["datatype"] = str(val.datatype)
                    binding[str(var)] = entry
                else:
                    binding[str(var)] = {"type": "literal", "value": str(val)}
            bindings.append(binding)

        payload = _json.dumps({"head": {"vars": vars_}, "results": {"bindings": bindings}})
        return Response(content=payload, media_type="application/sparql-results+json")

    except Exception as e:
        log_err(f"sparqlEndpoint error: {e}")
        return JSONResponse({"error": str(e)}, status_code=400)


# ---------------------------------------------------------------------------
# /agent/query — proxy to agentUrl with rate limiting
# ---------------------------------------------------------------------------

@router.post("/agent/query")
async def agent_query(request: Request):
    ip = metrics.get_ip(request)
    if not metrics.check_rate_limit(ip):
        return Response(
            content="Rate limit exceeded",
            status_code=429,
            headers={"Retry-After": str(metrics.RATE_WINDOW)},
        )
    srv = Server.get_instance()
    agent_url = srv.cfg.get("agentUrl")
    if not agent_url:
        return JSONResponse({"error": "agentUrl not configured"}, status_code=503)
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    timeout = srv.cfg.get("agentTimeout", 60)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{agent_url}/query", content=body, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"))
    except httpx.ConnectError as e:
        log_err(f"agent/query: cannot connect to {agent_url}: {e}")
        return JSONResponse({"error": f"Cannot connect to agent at {agent_url}"}, status_code=503)
    except httpx.TimeoutException as e:
        log_err(f"agent/query: timeout reaching {agent_url}: {e}")
        return JSONResponse({"error": "Agent request timed out"}, status_code=504)
    except Exception as e:
        log_err(f"agent/query: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


# ---------------------------------------------------------------------------
# /explore/graph-data — Cytoscape node/edge JSON for the Explore page
# ---------------------------------------------------------------------------

@router.get("/explore/graph-data")
async def explore_graph_data(
    request: Request,
    type: str = "ontology",
    schemes: str = "",
):
    srv = Server.get_instance()
    if srv.dbm is None:
        return JSONResponse({"error": "data not loaded"}, status_code=503)
    from services.explore import build_graph_data
    import asyncio
    scheme_list = [s.strip() for s in schemes.split(",") if s.strip()]
    data = await asyncio.to_thread(
        build_graph_data, srv.dbm.rdfs, type, scheme_list, srv.dbm.schema
    )
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# /md2html — markdown → HTML (stub; Stage 4)
# ---------------------------------------------------------------------------

_DEFAULT_PORT = {"http": 80, "https": 443}


def _origin(url: str):
    """Return (hostname, port) with the scheme default filled in, or None."""
    from urllib.parse import urlparse as _up
    p = _up(str(url))
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    try:
        port = p.port or _DEFAULT_PORT[p.scheme]
    except ValueError:                       # malformed port
        return None
    return (p.hostname.lower(), port)


def _md2html_allowed_origins(cfg: dict) -> set:
    """Origins /md2html may fetch from — this deployment and its reference server.

    Compared on host *and* port. Host alone is not enough: on a development
    config where host is http://localhost:8080, matching by hostname would also
    permit http://localhost:8090 — the agent — and every other local service.
    """
    origins = set()
    for key in ("host", "domain", "referenceModel"):
        val = cfg.get(key)
        if val:
            o = _origin(val)
            if o:
                origins.add(o)
    return origins


# ---------------------------------------------------------------------------
# /robots.txt — the most-requested path on the site; 404 without it
# ---------------------------------------------------------------------------

# Content pages are open to crawlers. Disallowed paths are either expensive to
# serve (the SPARQL browser runs arbitrary queries) or operational endpoints
# that have no business in an index. robots.txt is advisory, not access
# control — /cmd remains token-validated regardless.
_ROBOTS_TXT = """User-agent: *
Disallow: /sparql
Disallow: /sparqlEndpoint
Disallow: /cmd
Disallow: /refresh
Disallow: /cestfini
Disallow: /status
Disallow: /metrics
Disallow: /metricTables
Disallow: /md2html
Disallow: /agent/
Allow: /
"""


@router.get("/robots.txt")
async def robots_txt():
    return PlainTextResponse(_ROBOTS_TXT, media_type="text/plain")


@router.get("/md2html")
async def md2html(request: Request, doc: str = ""):
    if not doc:
        return PlainTextResponse("doc parameter required", status_code=400)

    # Fetch only our own documents.  Without this the endpoint is an open proxy:
    # it would fetch any URL on request (internal services, link-local metadata,
    # third-party hosts) and render the result as HTML on this origin — and
    # mistune passes raw HTML through, so a remote markdown file containing a
    # <script> tag would execute here.  Legitimate use only ever fetches
    # documents from this deployment.
    origin = _origin(doc)
    if origin is None or origin not in _md2html_allowed_origins(Server.get_instance().cfg):
        log_err(f"[md2html] refused off-site document fetch: {doc[:120]}")
        return PlainTextResponse("Document host not permitted", status_code=403)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(doc, timeout=10.0)
        if resp.status_code != 200:
            return PlainTextResponse(f"Could not fetch document: HTTP {resp.status_code}",
                                     status_code=502)
        import mistune
        from util.html_template import head, tail
        srv = Server.get_instance()
        body = mistune.html(resp.text)
        page = head(srv.cfg.get("host", ""), server=srv) + body + tail()
        return Response(content=page, media_type="text/html; charset=utf-8")
    except Exception as e:
        return PlainTextResponse(f"Error fetching document: {e}", status_code=502)


# ---------------------------------------------------------------------------
# /documents/* — serve PDFs and markdown files
# ---------------------------------------------------------------------------

@router.get("/documents/{filename:path}")
async def serve_document(filename: str, request: Request):
    srv = Server.get_instance()
    docs_dir = srv.cfg.get("documents", "")
    file_path = Path(docs_dir) / filename
    if not file_path.exists():
        if os.environ.get("GCP_BUCKET"):
            from util.gcp import fetch_document
            log_out(f"[document] not cached, fetching from bucket: {filename}")
            fetched = await asyncio.to_thread(fetch_document, filename, docs_dir)
            if fetched is None:
                return Response(content="Not found", status_code=404)
            file_path = fetched
        else:
            return Response(content="Not found", status_code=404)

    # An empty filename resolves to the documents folder itself; read_bytes()
    # on a directory raised IsADirectoryError and escaped as a 500.
    if not file_path.is_file():
        return Response(content="Not found", status_code=404)

    mime, _ = mimetypes.guess_type(str(file_path))
    return Response(content=file_path.read_bytes(), media_type=mime or "application/octet-stream")


# ---------------------------------------------------------------------------
# /images/* — serve images
# ---------------------------------------------------------------------------

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".glb", ".ico", ".usdz"}

# Video is served by /media, never by /images or /thumbnails:
#   * the image handler does file_path.read_bytes() — a 50 MB MP4 per request is
#     untenable on the free-tier VM the deployment guide targets
#   * it sends no Accept-Ranges, so browsers cannot seek; some refuse to start
#     playback at all
#   * .mp4 handed to Pillow by the thumbnail handler would simply fail
VIDEO_SUFFIXES = {".mp4", ".webm", ".m4v", ".mov", ".ogv"}

# Read size for streamed video. Large enough to keep syscalls down, small
# enough that memory stays flat regardless of file size.
_MEDIA_CHUNK = 256 * 1024

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _parse_range(range_header: str, file_size: int):
    """Parse a single-range 'bytes=' header.

    Returns (start, end) inclusive, or None when the header is absent or not a
    form we serve; raises ValueError when the range is unsatisfiable so the
    caller can answer 416.

    Only single ranges are handled — multipart/byteranges is not implemented.
    Browsers seeking in a video send single ranges, so this covers the case
    that matters; anything else falls back to a normal 200 with the full body.
    """
    if not range_header:
        return None
    m = _RANGE_RE.match(range_header.strip())
    if not m:
        return None
    first, last = m.group(1), m.group(2)

    if first == "":
        # Suffix form: bytes=-N — the final N bytes
        if last == "":
            return None
        n = int(last)
        if n == 0:
            raise ValueError("unsatisfiable")
        start, end = max(0, file_size - n), file_size - 1
    else:
        start = int(first)
        end = int(last) if last else file_size - 1
        if start >= file_size or start > end:
            raise ValueError("unsatisfiable")
        end = min(end, file_size - 1)
    return start, end


def _stream_file(path: Path, start: int, end: int):
    """Yield bytes [start, end] inclusive in bounded chunks."""
    remaining = end - start + 1
    with open(path, "rb") as fh:
        fh.seek(start)
        while remaining > 0:
            chunk = fh.read(min(_MEDIA_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.head("/media/{filename:path}")
@router.get("/media/{filename:path}")
async def serve_media(filename: str, request: Request):
    """Serve video with HTTP Range support so browsers can seek.

    Files live alongside images by default; set a "media" config path to keep
    them in their own folder. Excluded from metrics — one video produces many
    Range requests.

    HEAD is registered explicitly: FastAPI does not derive it from GET, and
    players issue HEAD to learn the length and range support before seeking.
    """
    srv = Server.get_instance()
    media_dir = srv.cfg.get("media") or srv.cfg.get("images", "")

    # Direct path first, then filename-only search, then bucket (as /images)
    base = Path(media_dir).resolve()
    file_path = base / filename
    # Keep '..' in the request path from escaping the media folder
    try:
        if not file_path.resolve().is_relative_to(base):
            log_err(f"[media] path escapes media folder, refused: {filename}")
            return Response(content="Forbidden", status_code=403)
    except (OSError, ValueError):
        return Response(content="Forbidden", status_code=403)

    if not file_path.exists():
        candidates = list(Path(media_dir).rglob(Path(filename).name))
        if candidates:
            file_path = candidates[0]
        elif os.environ.get("GCP_BUCKET"):
            from util.gcp import fetch_image
            log_out(f"[media] not cached, fetching from bucket: {filename}")
            prefix = "media" if srv.cfg.get("media") else "images"
            fetched = await asyncio.to_thread(fetch_image, filename, media_dir,
                                              prefix)
            if fetched is None:
                log_err(f"[media] not found in bucket: {filename}")
                return Response(content="Not found", status_code=404)
            file_path = fetched
        else:
            log_err(f"[media] GCP_BUCKET not set, cannot fetch: {filename}")
            return Response(content="Not found", status_code=404)

    if file_path.suffix.lower() not in VIDEO_SUFFIXES:
        return Response(content="Forbidden", status_code=403)

    file_size = file_path.stat().st_size
    mime, _ = mimetypes.guess_type(str(file_path))
    mime = mime or "application/octet-stream"

    if request.method == "HEAD":
        # Headers only — never run the body generator for a HEAD
        return Response(status_code=200, media_type=mime,
                        headers={"Accept-Ranges": "bytes",
                                 "Content-Length": str(file_size)})

    try:
        rng = _parse_range(request.headers.get("range", ""), file_size)
    except ValueError:
        return Response(status_code=416,
                        headers={"Content-Range": f"bytes */{file_size}",
                                 "Accept-Ranges": "bytes"})

    if rng is None:
        # Whole file, still advertising range support so the player can seek
        return StreamingResponse(
            _stream_file(file_path, 0, file_size - 1),
            media_type=mime,
            headers={"Accept-Ranges": "bytes",
                     "Content-Length": str(file_size)},
        )

    start, end = rng
    return StreamingResponse(
        _stream_file(file_path, start, end),
        status_code=206,
        media_type=mime,
        headers={"Accept-Ranges": "bytes",
                 "Content-Range": f"bytes {start}-{end}/{file_size}",
                 "Content-Length": str(end - start + 1)},
    )

@router.get("/images/{filename:path}")
async def serve_image(filename: str, request: Request):
    # images excluded from metrics per spec
    srv = Server.get_instance()
    images_dir = srv.cfg.get("images", "")

    # Direct path first, then filename-only search (FileUtil behaviour)
    file_path = Path(images_dir) / filename
    if not file_path.exists():
        candidates = list(Path(images_dir).rglob(Path(filename).name))
        if candidates:
            file_path = candidates[0]
        elif os.environ.get("GCP_BUCKET"):
            from util.gcp import fetch_image
            log_out(f"[image] not cached, fetching from bucket: {filename}")
            fetched = await asyncio.to_thread(fetch_image, filename, images_dir)
            if fetched is None:
                log_err(f"[image] not found in bucket: {filename}")
                return Response(content="Not found", status_code=404)
            file_path = fetched
        else:
            log_err(f"[image] GCP_BUCKET not set, cannot fetch: {filename}")
            return Response(content="Not found", status_code=404)

    suffix = file_path.suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        return Response(content="Forbidden", status_code=403)

    mime, _ = mimetypes.guess_type(str(file_path))
    return Response(content=file_path.read_bytes(), media_type=mime or "application/octet-stream")


# ---------------------------------------------------------------------------
# /thumbnails/* — serve resized gallery thumbnails (created on demand)
# ---------------------------------------------------------------------------

@router.get("/thumbnails/{filename:path}")
async def serve_thumbnail(filename: str, request: Request):
    srv = Server.get_instance()
    images_dir = srv.cfg.get("images", "")
    thumbnails_dir = srv.cfg.get("thumbnails", "")
    if not thumbnails_dir:
        # No thumbnails dir configured — fall through to full image
        return await serve_image(filename, request)

    thumb_path = Path(thumbnails_dir) / filename
    if not thumb_path.exists():
        if os.environ.get("GCP_BUCKET"):
            from util.gcp import fetch_thumbnail
            log_out(f"[thumbnail] not cached, fetching: {filename}")
            result = await asyncio.to_thread(fetch_thumbnail, filename, images_dir, thumbnails_dir)
            if result is None:
                log_err(f"[thumbnail] not found in bucket: {filename}")
                return Response(content="Not found", status_code=404)
            thumb_path = result
        else:
            log_err(f"[thumbnail] GCP_BUCKET not set — cannot fetch: {filename}")
            return Response(content="Not found", status_code=404)

    suffix = thumb_path.suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        return Response(content="Forbidden", status_code=403)

    mime, _ = mimetypes.guess_type(str(thumb_path))
    return Response(content=thumb_path.read_bytes(), media_type=mime or "application/octet-stream")


# ---------------------------------------------------------------------------
# /favicon.ico / /favicon.png
# ---------------------------------------------------------------------------

@router.get("/favicon.ico")
@router.get("/favicon.png")
async def favicon(request: Request):
    srv = Server.get_instance()
    images_dir = srv.cfg.get("images", "")
    suffix = ".png" if request.url.path.endswith(".png") else ".ico"
    for name in (f"favicon{suffix}", "dblHelix.png"):
        candidate = Path(images_dir) / name
        if candidate.exists():
            mime, _ = mimetypes.guess_type(str(candidate))
            return Response(content=candidate.read_bytes(),
                            media_type=mime or "image/x-icon")
    return Response(content="Not found", status_code=404)


# ---------------------------------------------------------------------------
# /refresh and /cestfini — disabled as direct routes; token-gated via /cmd
# ---------------------------------------------------------------------------

# @router.get("/refresh")
# async def refresh(): ...  # moved to _do_refresh()

# @router.get("/cestfini")
# async def cestfini(): ...  # moved to _do_cestfini()

@router.get("/guid")
async def guid_page():
    new_guid = str(uuid.uuid4())
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>UUID Generator</title>
<style>
body {{ font-family: monospace; font-size: 18px; padding: 2rem; }}
#guid {{ font-size: 1.4rem; letter-spacing: 0.05em; margin: 1rem 0; }}
button {{ font-size: 1rem; margin-right: 0.5rem; padding: 0.3rem 0.8rem; cursor: pointer; }}
</style>
</head><body>
<p id="guid">{new_guid}</p>
<button onclick="location.reload()">Generate New</button>
<button id="copyBtn" onclick="copyGuid()">Copy</button>
<script>
function copyGuid() {{
  var text = document.getElementById('guid').textContent;
  var btn = document.getElementById('copyBtn');
  if (navigator.clipboard && window.isSecureContext) {{
    navigator.clipboard.writeText(text).then(function() {{
      btn.textContent = 'Copied!';
    }}).catch(function() {{ fallbackCopy(text, btn); }});
  }} else {{
    fallbackCopy(text, btn);
  }}
}}
function fallbackCopy(text, btn) {{
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {{
    document.execCommand('copy');
    btn.textContent = 'Copied!';
  }} catch(e) {{
    btn.textContent = 'Failed';
  }}
  document.body.removeChild(ta);
}}
</script>
</body></html>"""
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get("/refresh")
async def refresh_direct():
    return Response("Use /cmd?token={token}&cmd=refresh", status_code=403)

@router.get("/cestfini")
async def cestfini_direct():
    return Response("Use /cmd?token={token}&cmd=cestfini", status_code=403)


# ---------------------------------------------------------------------------
# Private implementations (called by /cmd after token validation)
# ---------------------------------------------------------------------------

async def _do_refresh() -> Response:
    from rdf.db_mgr import DBMgr
    from util import gcp
    srv = Server.get_instance()
    log_out("Refresh requested — reloading data stores...")
    srv.dbm = await asyncio.to_thread(DBMgr, srv.cfg)
    srv.dbm.print_stats()
    await asyncio.to_thread(
        gcp.evict_stale_images, srv.cfg["images"], srv.cfg["thumbnails"], "images"
    )
    await asyncio.to_thread(
        gcp.evict_stale_images, srv.cfg["documents"], None, "documents"
    )
    return JSONResponse({"status": "ok"})


async def _do_cestfini() -> Response:
    import json
    import signal
    from util import gcp
    srv = Server.get_instance()
    all_metrics = metrics.get_all()
    payload = json.dumps(all_metrics, indent=2)

    bucket = os.environ.get("GCP_BUCKET")
    if getattr(srv, "test_mode", False):
        log_out("--test mode — skipping metrics snapshot upload to GCP (stats dumped to log below)")
    elif bucket:
        await asyncio.to_thread(gcp.push_metrics, all_metrics, bucket, srv.started_at)

    async def _shutdown():
        await asyncio.sleep(1)
        log_out(payload)
        log_out("fini")
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_shutdown())
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# /cmd — token-validated command dispatcher
# ---------------------------------------------------------------------------

@router.get("/cmd")
async def cmd(request: Request):
    from util.token import validate as token_validate
    token = request.query_params.get("token", "")
    command = request.query_params.get("cmd", "")

    if not token_validate(token):
        log_err(f"Invalid or missing token for /cmd?cmd={command} from {metrics.get_ip(request)}")
        return Response("Unauthorized", status_code=401)

    if command == "refresh":
        return await _do_refresh()
    if command == "cestfini":
        return await _do_cestfini()

    log_err(f"Unknown cmd: {command}")
    return JSONResponse({"status": "unknown command"}, status_code=400)


# ---------------------------------------------------------------------------
# Policy helpers (servletPolicy.rson)
# ---------------------------------------------------------------------------

def load_policy(cfg_dir: str) -> dict:
    from util.rson import load as rson_load
    policy_path = Path(cfg_dir) / "res" / "servletPolicy.rson"
    if policy_path.exists():
        return rson_load(str(policy_path))
    return {}


def policy_accept(name: str, path: str, policy: dict) -> bool:
    patterns = policy.get(name, {}).get("path", [])
    return any(re.search(pat, path) for pat in patterns if pat)


# ---------------------------------------------------------------------------
# Catch-all — must be the last route registered
# ---------------------------------------------------------------------------

@router.api_route("/{path:path}", methods=["GET", "POST"])
async def unknown_path(request: Request, path: str):
    request.state.metrics_path = "unknownPath"
    log_out(f"unknown path /{path} {request.url.query}")
    return Response("Not found", status_code=404)
