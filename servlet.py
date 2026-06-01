"""Primary route handlers and FastAPI app factory — port of Servlet.groovy.

Page-rendering routes (gallery, browser, etc.) are stubs returning 200 until
Stage 3.  Routing, static file mounts, and proxy wiring are fully configured.
"""

import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Scope, Receive, Send

from server import Server
from util import metrics
from util.html_template import head, tail
from util.logging import log_err
import servlet_base
from services import about as about_svc
from services import agent_client as agent_client_svc
from services import explore as explore_svc
from services import model_viewer as model_viewer_svc
from services import sparql_browser as sparql_browser_svc
from services import vocab_tree as vocab_tree_svc


MOBILE_RE = re.compile(
    r"Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini", re.IGNORECASE
)


def _is_mobile(request: Request) -> bool:
    ua = request.headers.get("user-agent", "")
    return bool(MOBILE_RE.search(ua))


FORMAT_MIME = {
    "ttl":       "text/turtle",
    "rdf/xml":   "application/rdf+xml",
    "xml":       "application/rdf+xml",
    "n-triples": "application/n-triples",
    "n3":        "text/n3",
    "jsonld":    "application/ld+json",
    "json-ld":   "application/ld+json",
}

# Map Accept header MIME types back to our format keys
_ACCEPT_MAP = {v: k for k, v in FORMAT_MIME.items()}
# Prefer canonical keys when multiple keys share the same MIME
_ACCEPT_MAP["application/rdf+xml"]  = "rdf/xml"
_ACCEPT_MAP["application/ld+json"]  = "jsonld"

# RDFLib format string for each of our keys
_RDFLIB_FMT = {
    "ttl":       "turtle",
    "rdf/xml":   "xml",
    "xml":       "xml",
    "n-triples": "nt",
    "n3":        "n3",
    "jsonld":    "json-ld",
    "json-ld":   "json-ld",
}


def build_app(srv: Server) -> FastAPI:
    app = FastAPI(title="CWVA Server", docs_url=None, redoc_url=None)
    cfg = srv.cfg

    # ------------------------------------------------------------------
    # Static file mounts
    # ------------------------------------------------------------------
    base_dir = cfg.get("dir", ".")
    dist_dir = Path(base_dir) / "dist"
    html_dir = Path(base_dir) / "html"

    if dist_dir.exists():
        app.mount("/dist", StaticFiles(directory=str(dist_dir)), name="dist")
    if html_dir.exists():
        app.mount("/html", StaticFiles(directory=str(html_dir)), name="html")

    # ------------------------------------------------------------------
    # Proxy headers — trust X-Forwarded-For from localhost (Caddy etc.)
    # Makes request.client.host the real client IP, not the proxy address.
    # ------------------------------------------------------------------
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["127.0.0.1"])

    # ------------------------------------------------------------------
    # Middleware — record metrics on every request (skip images/favicon)
    # ------------------------------------------------------------------
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        response = await call_next(request)
        # Handlers may override the recorded path via request.state.metrics_path
        # (e.g. catch-all sets it to "/unknownPath" so the actual path is never stored)
        path = getattr(request.state, "metrics_path", request.url.path)
        if metrics.should_record(path):
            metrics.record(request, path)
        return response

    # ------------------------------------------------------------------
    # Primary routes
    # ------------------------------------------------------------------

    @app.get("/")
    async def home(request: Request, order: str = "Date", artist: str = "all",
                   offset: int = 0, limit: int = 20, page: int = 1):
        mobile = _is_mobile(request)
        return _browse_works_stub(srv, order, artist, offset, limit, page, mobile)

    @app.get("/browseSort")
    async def browse_sort(request: Request, order: str = "Date", artist: str = "all",
                          offset: int = 0, limit: int = 20, page: int = 1):
        mobile = _is_mobile(request)
        return _browse_works_stub(srv, order, artist, offset, limit, page, mobile)

    @app.get("/browseFilter")
    async def browse_filter(request: Request, order: str = "Date", artist: str = "all",
                             offset: int = 0, limit: int = 20, page: int = 1):
        mobile = _is_mobile(request)
        return _browse_works_stub(srv, order, artist, offset, limit, page, mobile)

    @app.get("/work/{guid}")
    async def work_detail(guid: str, request: Request, format: str = ""):
        return _rdf2html_stub(srv, "work", guid, request, format)

    @app.get("/model/{cls}")
    async def model_detail(cls: str, request: Request, format: str = ""):
        return _rdf2html_stub(srv, "model", cls, request, format)

    @app.get("/thesaurus/{term}")
    async def thesaurus_detail(term: str, request: Request, format: str = ""):
        return _rdf2html_stub(srv, "thesaurus", term, request, format)

    @app.get("/schema")
    async def schema_endpoint(request: Request, format: str = ""):
        if srv.dbm is None:
            return PlainTextResponse("data store not yet loaded", status_code=503)
        return _serve_graph(srv.dbm.schema, request, format)

    @app.get("/thesaurus")
    async def thesaurus_endpoint(request: Request, format: str = ""):
        if srv.dbm is None:
            return PlainTextResponse("data store not yet loaded", status_code=503)
        return _serve_graph(srv.dbm.vocab, request, format)

    @app.get("/data")
    async def data_endpoint(request: Request, format: str = ""):
        if srv.dbm is None:
            return PlainTextResponse("data store not yet loaded", status_code=503)
        return _serve_graph(srv.dbm.data, request, format)

    @app.get("/model")
    @app.get("/schema")
    async def model_ttl(request: Request, format: str = ""):
        if srv.dbm is None:
            return PlainTextResponse("data store not yet loaded", status_code=503)
        return _serve_graph(srv.dbm.schema, request, format)

    @app.get("/rdfs")
    async def rdfs_endpoint(request: Request, format: str = ""):
        if srv.dbm is None:
            return PlainTextResponse("rdfs store not yet loaded", status_code=503)
        return _serve_graph(srv.dbm.rdfs, request, format)

    @app.get("/vocab")
    async def vocab_endpoint(request: Request, format: str = ""):
        if srv.dbm is None:
            return PlainTextResponse("vocab store not yet loaded", status_code=503)
        return _serve_graph(srv.dbm.vocab, request, format)

    @app.get("/vocabTree")
    async def vocab_tree(request: Request):
        if srv.dbm is None:
            return _html(head(cfg["host"], server=srv) + "<p>Data not yet loaded.</p>" + tail())
        return _html(vocab_tree_svc.get(srv))

    @app.get("/agentClient")
    async def agent_client(request: Request):
        return _html(agent_client_svc.get(srv))

    @app.get("/explore")
    async def explore(request: Request):
        if srv.dbm is None:
            return _html(head(cfg["host"], server=srv) + "<p>Data not yet loaded.</p>" + tail())
        return _html(explore_svc.get(srv, _is_mobile(request)))

    @app.get("/about")
    async def about(request: Request):
        return _html(about_svc.get(srv))

    @app.get("/modelviewer")
    async def modelviewer(request: Request, work: str = "", bkg: str = ""):
        if srv.dbm is None or not work:
            host = srv.cfg.get("host", "")
            return _html(head(host, server=srv) + "<p>No work specified.</p>" + tail())
        mobile = _is_mobile(request)
        return _html(model_viewer_svc.get(srv, work, bkg, mobile))

    # --- temporary error-handler smoke tests (comment out after verifying) ---
    #@app.get("/boom500")
    #async def boom500():
    #    raise RuntimeError("deliberate 500 test")
    # -------------------------------------------------------------------------
    #@app.get("/boom404")
    #async def boom404():
    #    from fastapi import HTTPException
    #    raise HTTPException(status_code=404)
    # -------------------------------------------------------------------------

    # ------------------------------------------------------------------
    # /sparql — built-in browser (timeout + row cap + rate limit)
    # ------------------------------------------------------------------

    @app.get("/sparql")
    async def sparql_browser(request: Request):
        mobile = _is_mobile(request)
        result = await sparql_browser_svc.process(srv, request, mobile)
        if isinstance(result, str):
            return _html(result)
        return result  # Response (e.g. 429)

    # ------------------------------------------------------------------
    # Secondary routes (servlet_base)
    # ------------------------------------------------------------------
    app.include_router(servlet_base.router)

    # ------------------------------------------------------------------
    # Exception handlers — styled HTML error pages
    # ------------------------------------------------------------------

    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            body = head(cfg.get("host", ""), server=srv) + """
<h2>Page Not Found</h2>
<p>The page you requested doesn't exist.</p>
<p><a href="/">Return to Gallery</a></p>
""" + tail()
        else:
            body = head(cfg.get("host", ""), server=srv) + f"""
<h2>Error {exc.status_code}</h2>
<p>{exc.detail}</p>
""" + tail()
        return Response(content=body, media_type="text/html; charset=utf-8",
                        status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        log_err(f"Unhandled error on {request.url.path}: {exc}")
        body = head(cfg.get("host", ""), server=srv) + """
<h2>Server Error</h2>
<p>Something went wrong. Please try again.</p>
""" + tail()
        return Response(content=body, media_type="text/html; charset=utf-8", status_code=500)

    return app


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

def _html(body: str) -> Response:
    return Response(content=body, media_type="text/html; charset=utf-8")


def _resolve_format(request: Request, fmt_param: str) -> str:
    """Return a FORMAT_MIME key from ?format= param, then Accept header, else ''."""
    if fmt_param:
        return fmt_param.lower()
    accept = request.headers.get("accept", "")
    for mime, key in _ACCEPT_MAP.items():
        if mime in accept:
            return key
    return ""


def _serve_graph(g, request: Request, fmt_param: str = "") -> Response:
    """Serialize an RDFLib graph with format negotiation.

    Resolution order: ?format= query param → Accept header → Turtle default.
    """
    fmt = _resolve_format(request, fmt_param)
    rdflib_fmt = _RDFLIB_FMT.get(fmt, "turtle")
    mime = FORMAT_MIME.get(fmt, "text/turtle")
    content = g.serialize(format=rdflib_fmt)
    return Response(content=content, media_type=mime)


def _browse_works_stub(srv: Server, order, artist, offset, limit, page, mobile) -> Response:
    if srv.dbm is None:
        host = srv.cfg["host"]
        return _html(head(host, server=srv) + "<p>Data not yet loaded.</p>" + tail())
    from services.browse_works import build_page
    return _html(build_page(srv, order, artist, offset, limit, page, mobile))


def _rdf2html_stub(srv: Server, ns: str, guid: str, request: Request, fmt: str) -> Response:
    if srv.dbm is None:
        host = srv.cfg["host"]
        return _html(head(host, server=srv) + "<p>Data not yet loaded.</p>" + tail())

    fmt = _resolve_format(request, fmt)
    if fmt in FORMAT_MIME:
        from rdf.query_support import QuerySupport
        qs = QuerySupport(srv.dbm.rdfs)
        g = qs.get_one_instance_model(ns, guid)
        return _serve_graph(g, request, fmt)

    from services.rdf2html import process
    mobile = _is_mobile(request)
    host = srv.cfg["host"]
    body = process(srv, ns, guid, host, mobile)
    return _html(body)
