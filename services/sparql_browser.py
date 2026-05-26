"""Built-in SPARQL browser with timeout, row cap, and rate limiting.

Security mitigations:
  - 10-second query timeout via ThreadPoolExecutor
  - LIMIT 10000 injected on SELECT queries that lack a LIMIT clause
  - 10 requests / 10-minute / IP rate limit
  - 5 MB response cap
"""

import asyncio
import concurrent.futures
import datetime
import html as html_mod
import io
import json as json_mod
import re
import threading
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response

from rdf.prefixes import FOR_QUERY, VAD, WORK, THE, TKO, SCHEMA
from rdflib.namespace import RDF, RDFS, SKOS, OWL, XSD
from server import Server
from util.html_template import head, tail

# ---------------------------------------------------------------------------
# Security constants
# ---------------------------------------------------------------------------

_TIMEOUT_SECS = 10
_ROW_CAP = 10_000
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

_RATE_LIMIT = 10
_RATE_WINDOW = 600  # 10 minutes

_rate_limits: dict = {}
_rate_lock = threading.Lock()

# Namespace prefix table for URI shortening in result cells
_NS_MAP = [
    ("work",   str(WORK)),
    ("vad",    str(VAD)),
    ("the",    str(THE)),
    ("tko",    str(TKO)),
    ("schema", str(SCHEMA)),
    ("rdf",    str(RDF)),
    ("rdfs",   str(RDFS)),
    ("skos",   str(SKOS)),
    ("owl",    str(OWL)),
    ("xsd",    str(XSD)),
]

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def _check_rate_limit(ip: str) -> bool:
    """Return True if the request is within the rate window."""
    now = datetime.datetime.now().timestamp()
    with _rate_lock:
        entry = _rate_limits.get(ip)
        if entry is None or (now - entry[1]) > _RATE_WINDOW:
            _rate_limits[ip] = (1, now)
            return True
        count, window_start = entry
        if count >= _RATE_LIMIT:
            return False
        _rate_limits[ip] = (count + 1, window_start)
        return True


# ---------------------------------------------------------------------------
# Query guards
# ---------------------------------------------------------------------------

_UPDATE_RE = re.compile(
    r"(?i)\b(INSERT\s*\{|INSERT\s+DATA\s*\{|DELETE\s*\{|DELETE\s+DATA\s*\{)",
)


def _is_update(sparql: str) -> bool:
    return bool(_UPDATE_RE.search(sparql))


def _inject_limit(sparql: str) -> str:
    """Append LIMIT 10000 to SELECT queries that have no LIMIT clause."""
    if re.match(r"\s*SELECT\b", sparql, re.IGNORECASE):
        if not re.search(r"\bLIMIT\b", sparql, re.IGNORECASE):
            return sparql.rstrip() + f"\nLIMIT {_ROW_CAP}"
    return sparql


def _run_query(graph, full_sparql: str):
    """Execute graph.query() in a thread; abandon it after timeout.

    Returns (result, error_str).  result is None on timeout or exception.
    """
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = ex.submit(graph.query, full_sparql)
    try:
        result = future.result(timeout=_TIMEOUT_SECS)
        ex.shutdown(wait=False)
        return result, None
    except concurrent.futures.TimeoutError:
        ex.shutdown(wait=False)
        return None, f"Query timed out after {_TIMEOUT_SECS} seconds."
    except Exception as e:  # includes SPARQL parse errors
        ex.shutdown(wait=False)
        return None, str(e)


# ---------------------------------------------------------------------------
# URI helpers
# ---------------------------------------------------------------------------

def _short_uri(uri: str) -> str:
    """Return a prefixed short name for known namespaces, else last segment."""
    for pfx, ns in _NS_MAP:
        if uri.startswith(ns) and len(uri) > len(ns):
            return f"{pfx}:{uri[len(ns):]}"
    return uri.split("#")[-1].split("/")[-1] or uri


def _uri_cell(uri: str) -> str:
    """Render a URI value as a table cell with a link."""
    display = html_mod.escape(_short_uri(uri))
    if "visualartsdna.org" in uri:
        from urllib.parse import urlparse
        href = html_mod.escape(urlparse(uri).path)
        return f'<td><a href="{href}">{display}</a></td>'
    href = html_mod.escape(uri)
    return f'<td><a href="{href}" target="_blank" rel="noopener">{display}</a></td>'


# ---------------------------------------------------------------------------
# Result formatters
# ---------------------------------------------------------------------------

def _html_table(result) -> tuple[str, int]:
    """Render a SELECT result as a scrollable HTML table."""
    vars_ = [str(v) for v in result.vars]
    rows = list(result)

    parts = [
        '<div class="sparql-results">',
        '<table class="sparql-table">',
        "<tr>" + "".join(f"<th>{html_mod.escape(v)}</th>" for v in vars_) + "</tr>",
    ]
    for row in rows:
        parts.append("<tr>")
        for v in vars_:
            val = row.get(v)
            if val is None:
                parts.append("<td></td>")
            else:
                s = str(val)
                if s.startswith("http://") or s.startswith("https://"):
                    parts.append(_uri_cell(s))
                else:
                    parts.append(f"<td>{html_mod.escape(s)}</td>")
        parts.append("</tr>")
    parts.append("</table></div>")
    return "\n".join(parts), len(rows)


def _csv_text(result) -> tuple[str, int]:
    vars_ = [str(v) for v in result.vars]
    rows = list(result)
    buf = io.StringIO()
    buf.write(",".join(vars_) + "\r\n")
    for row in rows:
        buf.write(",".join(
            '"' + str(row.get(v) or "").replace('"', '""') + '"'
            for v in vars_
        ) + "\r\n")
    return buf.getvalue(), len(rows)


def _tsv_text(result) -> tuple[str, int]:
    vars_ = [str(v) for v in result.vars]
    rows = list(result)
    buf = io.StringIO()
    buf.write("\t".join(f"?{v}" for v in vars_) + "\n")
    for row in rows:
        buf.write("\t".join(str(row.get(v) or "") for v in vars_) + "\n")
    return buf.getvalue(), len(rows)


def _json_text(result) -> tuple[str, int]:
    vars_ = [str(v) for v in result.vars]
    rows = list(result)
    bindings = []
    for row in rows:
        b = {}
        for v in vars_:
            val = row.get(v)
            if val is not None:
                b[v] = {"value": str(val)}
        bindings.append(b)
    doc = {"head": {"vars": vars_}, "results": {"bindings": bindings}}
    return json_mod.dumps(doc, indent=2), len(rows)


def _xml_text(result) -> tuple[str, int]:
    vars_ = [str(v) for v in result.vars]
    rows = list(result)
    buf = io.StringIO()
    buf.write('<?xml version="1.0"?>\n')
    buf.write('<sparql xmlns="http://www.w3.org/2005/sparql-results#">\n')
    buf.write("  <head>\n")
    for v in vars_:
        buf.write(f'    <variable name="{html_mod.escape(v)}"/>\n')
    buf.write("  </head>\n  <results>\n")
    for row in rows:
        buf.write("    <result>\n")
        for v in vars_:
            val = row.get(v)
            if val is not None:
                buf.write(
                    f'      <binding name="{html_mod.escape(v)}">'
                    f"<literal>{html_mod.escape(str(val))}</literal>"
                    f"</binding>\n"
                )
        buf.write("    </result>\n")
    buf.write("  </results>\n</sparql>\n")
    return buf.getvalue(), len(rows)


def _textarea(text: str, is_mobile: bool) -> str:
    if is_mobile:
        size = 'style="width:calc(100vw - 32px);box-sizing:border-box;"'
    else:
        size = 'cols="60"'
    return (f'<textarea readonly rows="20" {size} spellcheck="false"'
            f' class="sparql-result-text">{html_mod.escape(text)}</textarea>')


# ---------------------------------------------------------------------------
# Query execution (synchronous — called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _execute(graph, sparql: str, fmt: str, is_mobile: bool) -> dict:
    """Run SPARQL and return {time_ms, status, row_count, result_html}."""
    info: dict = {"time_ms": 0, "status": "ok", "row_count": 0, "result_html": ""}

    sparql = sparql.strip()
    if not sparql:
        return info

    if _is_update(sparql):
        info["status"] = "update disabled"
        return info

    guarded = FOR_QUERY + _inject_limit(sparql)
    t0 = datetime.datetime.now().timestamp()
    result, err = _run_query(graph, guarded)
    info["time_ms"] = int((datetime.datetime.now().timestamp() - t0) * 1000)

    if result is None:
        info["status"] = "timeout" if "timed out" in (err or "") else "error"
        info["result_html"] = _textarea(err or "Unknown error.", is_mobile)
        return info

    try:
        qtype = result.type  # 'SELECT', 'CONSTRUCT', 'DESCRIBE', 'ASK'

        if qtype == "SELECT":
            fmt_map = {
                "HTML": _html_table,
                "CSV":  _csv_text,
                "TSV":  _tsv_text,
                "JSON": _json_text,
                "XML":  _xml_text,
            }
            formatter = fmt_map.get(fmt, _html_table)
            text_or_html, row_count = formatter(result)
            info["row_count"] = row_count
            info["result_html"] = text_or_html if fmt == "HTML" else _textarea(text_or_html, is_mobile)

        elif qtype in ("CONSTRUCT", "DESCRIBE"):
            # RDFLib returns triples — collect into a temporary graph for serialization
            from rdflib import Graph as RDFGraph
            g = RDFGraph()
            for triple in result:
                g.add(triple)
            ttl = g.serialize(format="turtle")
            info["result_html"] = _textarea(ttl, is_mobile)
            info["row_count"] = len(g)

        elif qtype == "ASK":
            answer = "true" if result.askAnswer else "false"
            info["result_html"] = _textarea(f"ASK result: {answer}", is_mobile)
            info["row_count"] = 1

        else:
            info["status"] = f"unsupported query type: {qtype}"

    except Exception as e:
        info["status"] = "error"
        info["result_html"] = _textarea(str(e), is_mobile)

    if len(info["result_html"]) > _MAX_BYTES:
        info["result_html"] = _textarea(
            f"Response truncated: exceeds {_MAX_BYTES // 1024 // 1024} MB limit.",
            is_mobile,
        )
        info["status"] = "result size limit exceeded"

    return info


# ---------------------------------------------------------------------------
# Cached query loader
# ---------------------------------------------------------------------------

def _load_cached_queries(cfg_dir: str) -> list[tuple[str, str]]:
    """Parse res/cached.sparql and return [(label, full_query)] pairs.

    ## lines are file-level comments, skipped entirely.
    Blank lines separate queries.
    The first # comment in each query becomes the dropdown label.
    """
    path = Path(cfg_dir) / "res" / "cached.sparql"
    if not path.exists():
        return []

    queries: list[tuple[str, str]] = []
    current: list[str] = []

    def _flush(lines):
        q = "\n".join(lines).strip()
        if not q:
            return
        label = ""
        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith("#"):
                label = stripped[1:].strip()
                break
        if not label:
            label = q[:40] + ("…" if len(q) > 40 else "")
        queries.append((label, q))

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("##"):
            continue
        if line.strip() == "" and current:
            _flush(current)
            current = []
        else:
            current.append(line)

    if current:
        _flush(current)

    return queries


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

_CSS = """\
<style>
.sparql-layout{border-collapse:collapse;}
.sparql-layout td{vertical-align:top;}
.sparql-main-cell{padding-right:1rem;}
.sparql-sidebar-cell{white-space:nowrap;min-width:200px;}
.sparql-controls{display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap;
  padding:0.5rem 0.75rem;background:#f5f5f5;border:1px solid #ddd;
  border-radius:4px;margin-bottom:0.5rem;}
.sparql-controls label{margin-right:0.15rem;font-size:0.9em;}
.sparql-status{font-size:0.82em;color:#555;margin:0.3rem 0;}
.sparql-results{border:1px solid #ccc;overflow:auto;width:600px;height:500px;margin-top:0.5rem;resize:both;}
.sparql-result-text{display:block;resize:both;}
.sparql-table{border-collapse:collapse;width:100%;font-size:0.88em;}
.sparql-table th{background:#4682b4;color:#fff;padding:4px 8px;
  text-align:left;position:sticky;top:0;white-space:nowrap;}
.sparql-table td{padding:3px 8px;border-bottom:1px solid #eee;white-space:nowrap;}
.sparql-table tr:hover td{background:#f0f0ff;}
.sparql-sidebar-cell select{font-size:0.85em;}
.sparql-sidebar-cell h4{margin:0 0 0.25rem;}
.pfx-table{font-size:0.82em;border-collapse:collapse;}
.pfx-table td{padding:1px 6px;}
.sparql-notes{font-size:0.82em;color:#555;line-height:1.5;}
.sparql-below{display:flex;gap:3rem;margin-top:1.25rem;flex-wrap:wrap;}
@media(max-width:700px){.sparql-layout{flex-direction:column;}
  .sparql-sidebar{width:100%;}
  #sparqlForm textarea{width:100%;box-sizing:border-box;max-width:calc(100vw - 16px);}
  #sparqlForm select{width:100%;box-sizing:border-box;max-width:calc(100vw - 16px);}
  .sparql-results{width:100% !important;box-sizing:border-box;max-width:calc(100vw - 16px);}
  .sparql-result-text{max-width:calc(100vw - 16px);box-sizing:border-box;}
  .pfx-below{overflow-x:auto;max-width:100%;}
  .sparql-below{max-width:100%;}}
</style>"""

_PREFIXES_TABLE = """\
<table class="pfx-table">
<tr><td>dct:</td><td>&lt;http://purl.org/dc/terms/&gt;</td></tr>
<tr><td>foaf:</td><td>&lt;http://xmlns.com/foaf/0.1/&gt;</td></tr>
<tr><td>owl:</td><td>&lt;http://www.w3.org/2002/07/owl#&gt;</td></tr>
<tr><td>rdf:</td><td>&lt;http://www.w3.org/1999/02/22-rdf-syntax-ns#&gt;</td></tr>
<tr><td>rdfs:</td><td>&lt;http://www.w3.org/2000/01/rdf-schema#&gt;</td></tr>
<tr><td>schema:</td><td>&lt;https://schema.org/&gt;</td></tr>
<tr><td>skos:</td><td>&lt;http://www.w3.org/2004/02/skos/core#&gt;</td></tr>
<tr><td>the:</td><td>&lt;http://visualartsdna.org/thesaurus/&gt;</td></tr>
<tr><td>tko:</td><td>&lt;http://visualartsdna.org/takeout/&gt;</td></tr>
<tr><td>vad:</td><td>&lt;http://visualartsdna.org/model//&gt;</td></tr>
<tr><td>work:</td><td>&lt;http://visualartsdna.org/work/&gt;</td></tr>
<tr><td>xsd:</td><td>&lt;http://www.w3.org/2001/XMLSchema#&gt;</td></tr>
</table>"""


def _build_page(srv: Server, sparql: str, fmt: str, info: dict,
                queries: list, is_mobile: bool) -> str:
    host = srv.cfg["host"]

    def _chk(f):
        return "checked" if fmt == f else ""

    q_esc = html_mod.escape(sparql)
    cols = "40" if is_mobile else "60"

    status_line = (
        f'{info["time_ms"]} ms &nbsp;|&nbsp; '
        f'{info["row_count"]} rows &nbsp;|&nbsp; '
        f'{html_mod.escape(info["status"])}'
    )

    options = "\n".join(
        f'  <option value="{html_mod.escape(q)}">{html_mod.escape(lbl)}</option>'
        for lbl, q in queries
    )
    qs_size = max(5, min(len(queries), 20))

    js = """\
<script>
document.getElementById('queryList').addEventListener('change', function() {
  document.getElementById('query').value = this.options[this.selectedIndex].value;
});
</script>"""

    below = f"""\
<div class="sparql-below">
  <div class="pfx-below">
    <h4>Prefixes</h4>
    {_PREFIXES_TABLE}
  </div>
  <div>
    <h4>Notes</h4>
    <p class="sparql-notes">
      Format applies to SELECT results.<br>
      CONSTRUCT / DESCRIBE return Turtle.<br>
      UPDATE statements are disabled.<br>
      Timeout: {_TIMEOUT_SECS}s &nbsp; Row cap: {_ROW_CAP:,}<br>
      <a href="https://www.w3.org/TR/sparql11-query/" target="_blank" rel="noopener">SPARQL 1.1 spec</a>
    </p>
  </div>
</div>"""

    page = head(host, bg_color="#f9f9f9", server=srv)
    page += "<h3>SPARQL Browser</h3>\n"
    page += _CSS

    if is_mobile:
        # Mobile: single-column stacked layout — Query Set above textarea
        page += f"""\
<form id="sparqlForm" action="/sparql" method="get">
  <div class="sparql-controls">
    <input type="radio" name="format" value="HTML" id="fH" {_chk("HTML")}><label for="fH">HTML</label>
    <input type="radio" name="format" value="CSV"  id="fC" {_chk("CSV")}><label for="fC">CSV</label>
    <input type="radio" name="format" value="TSV"  id="fT" {_chk("TSV")}><label for="fT">TSV</label>
    <input type="radio" name="format" value="JSON" id="fJ" {_chk("JSON")}><label for="fJ">JSON</label>
    <input type="radio" name="format" value="XML"  id="fX" {_chk("XML")}><label for="fX">XML</label>
    &nbsp;<label for="sparqlUpdate">Update</label>
    <input type="checkbox" id="sparqlUpdate" name="sparqlUpdate" value="Update" disabled title="SPARQL UPDATE is not enabled">
    &nbsp;<input type="submit" value="Execute">
  </div>
  <div style="margin:0.5rem 0">
    <h4 style="margin:0 0 0.25rem">Query Set</h4>
    <select id="queryList" style="width:calc(100vw - 32px);box-sizing:border-box;">
{options}
    </select>
  </div>
  <textarea id="query" name="query" rows="10" style="width:calc(100vw - 32px);box-sizing:border-box;" spellcheck="false">{q_esc}</textarea>
  <div class="sparql-status">{status_line}</div>
  {info["result_html"]}
</form>
"""
    else:
        # Desktop: table layout — Query Set in right sidebar cell
        form = f"""\
<form id="sparqlForm" action="/sparql" method="get">
  <div class="sparql-controls">
    <input type="radio" name="format" value="HTML" id="fH" {_chk("HTML")}><label for="fH">HTML</label>
    <input type="radio" name="format" value="CSV"  id="fC" {_chk("CSV")}><label for="fC">CSV</label>
    <input type="radio" name="format" value="TSV"  id="fT" {_chk("TSV")}><label for="fT">TSV</label>
    <input type="radio" name="format" value="JSON" id="fJ" {_chk("JSON")}><label for="fJ">JSON</label>
    <input type="radio" name="format" value="XML"  id="fX" {_chk("XML")}><label for="fX">XML</label>
    &nbsp;<label for="sparqlUpdate">Update</label>
    <input type="checkbox" id="sparqlUpdate" name="sparqlUpdate" value="Update" disabled title="SPARQL UPDATE is not enabled">
    &nbsp;<input type="submit" value="Execute">
  </div>
  <textarea id="query" name="query" rows="10" cols="{cols}" spellcheck="false">{q_esc}</textarea>
  <div class="sparql-status">{status_line}</div>
  {info["result_html"]}
</form>"""

        sidebar_cell = f"""\
  <h4>Query Set</h4>
  <select id="queryList" size="{qs_size}">
{options}
  </select>"""

        page += (
            f'<table class="sparql-layout"><tr>'
            f'<td class="sparql-main-cell">{form}</td>'
            f'<td class="sparql-sidebar-cell">{sidebar_cell}</td>'
            f'</tr></table>\n'
        )

    page += below
    page += js
    page += tail()
    return page


def _rate_limit_page(srv: Server) -> str:
    host = srv.cfg["host"]
    page = head(host, bg_color="#fff5f5", server=srv)
    page += "<h3>Rate limit exceeded</h3>\n"
    page += (
        f"<p>The SPARQL browser allows {_RATE_LIMIT} queries per "
        f"{_RATE_WINDOW // 60} minutes per IP address.</p>\n"
        f'<p><a href="/sparql">Return to SPARQL Browser</a></p>\n'
    )
    page += tail()
    return page


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def process(srv: Server, request: Request, is_mobile: bool = False):
    """Handle GET /sparql — rate-check, execute if query present, return HTML.

    Returns a string (HTML page) or a Response (429 rate limit).
    is_mobile is detected by the servlet from the User-Agent header.
    """
    from util import metrics

    params = dict(request.query_params)
    sparql = params.get("query", "").strip()
    fmt = params.get("format", "HTML")

    queries = _load_cached_queries(srv.cfg.get("dir", "."))

    if sparql:
        ip = metrics.get_ip(request)
        if not _check_rate_limit(ip):
            return Response(
                content=_rate_limit_page(srv),
                status_code=429,
                media_type="text/html",
                headers={"Retry-After": str(_RATE_WINDOW)},
            )
        info = await asyncio.to_thread(_execute, srv.dbm.rdfs, sparql, fmt, is_mobile)
    else:
        info = {"time_ms": 0, "status": "ok", "row_count": 0, "result_html": ""}

    return _build_page(srv, sparql, fmt, info, queries, is_mobile)
