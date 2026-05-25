"""Secondary route handlers — port of ServletBase.groovy."""

import asyncio
import os
import re
import mimetypes
from pathlib import Path

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

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

@router.get("/md2html")
async def md2html(request: Request, doc: str = ""):
    if not doc:
        return PlainTextResponse("doc parameter required", status_code=400)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(doc, timeout=10.0)
        if resp.status_code != 200:
            return PlainTextResponse(f"Could not fetch document: HTTP {resp.status_code}",
                                     status_code=502)
        import mistune
        from util.html_template import head, TAIL
        srv = Server.get_instance()
        body = mistune.html(resp.text)
        page = head(srv.cfg.get("host", ""), server=srv) + body + TAIL
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

    mime, _ = mimetypes.guess_type(str(file_path))
    return Response(content=file_path.read_bytes(), media_type=mime or "application/octet-stream")


# ---------------------------------------------------------------------------
# /images/* — serve images
# ---------------------------------------------------------------------------

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".glb", ".ico", ".usdz"}

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
    if bucket:
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
