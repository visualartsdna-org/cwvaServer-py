"""Gallery page — port of BrowseWorks.groovy."""

import html as html_mod
from urllib.parse import urlencode, urlparse

from server import Server
from rdf.query_support import sparql_select
from util.html_template import head, tail

PAGE = 20
WIDTH = 336

_WELCOME_DEFAULT = (
    "Welcome to VisualArtsDNA — an online art gallery. Feel free to explore. "
    "This site began as a project to understand how information connects to "
    "artwork. That effort grew into a structured information model, now an "
    "evolving ontology with vocabularies and linked concepts that document "
    "each piece. While the model lives in the background, the gallery up "
    "front invites you to enjoy the artwork directly."
)


def build_page(srv: Server, order: str, artist: str, offset: int,
               limit: int, page: int, mobile: bool) -> str:
    graph = srv.dbm.rdfs
    host = srv.cfg["host"]
    welcome = srv.cfg.get("welcomeText", _WELCOME_DEFAULT)

    # Limit is always PAGE regardless of what the URL says
    limit = PAGE

    artist_filter = ""
    if artist and artist != "all":
        safe = artist.replace("'", "\\'")
        artist_filter = f"filter(?artist = '{safe}')"

    # str() cast avoids RDFLib bug where timezone-aware and naive xsd:dateTime
    # values sort into separate groups; ISO 8601 strings sort lexicographically correctly.
    sort_clause = "asc(?label)" if order == "Title" else "desc(str(?dt))"

    # Explicit triple patterns instead of property path — avoids a RDFLib
    # SPARQL bug where SELECT DISTINCT + property path + ORDER BY returns nothing.
    base_where = f"""
        ?uri a vad:CreativeWork ;
             schema:image ?image ;
             rdfs:label ?label ;
             schema:dateCreated ?dt .
        ?uri vad:hasArtistProfile ?_profile .
        ?_profile vad:artist ?_artistUri .
        ?_artistUri rdfs:label ?artist .
        OPTIONAL {{ ?uri vad:workOnSite ?site }}
        {artist_filter}
    """

    count_rows = sparql_select(graph, f"""
        SELECT (COUNT(DISTINCT ?uri) AS ?cnt) {{
            {base_where}
        }}
    """)
    data_size = 0
    if count_rows:
        try:
            data_size = int(count_rows[0].get("cnt", 0))
        except (ValueError, TypeError):
            data_size = 0

    rows = sparql_select(graph, f"""
        SELECT ?image ?label ?uri ?artist ?site ?dt {{
            {base_where}
        }} ORDER BY {sort_clause}
          OFFSET {offset}
          LIMIT {limit}
    """)

    for row in rows:
        row["uri"] = Server.rehost(row["uri"])
        row["image"] = Server.rehost(row["image"])
        row["thumb"] = row["image"].replace("/images/", "/thumbnails/", 1)

    # Pagination page list
    pages = []
    p = 1
    for i in range(0, data_size, PAGE):
        k = min(i + PAGE, data_size)
        pages.append({"pg": p, "oset": i, "lim": k})
        p += 1

    html = head(host, bg_color="white", server=srv)
    html += _pagination_css(mobile)
    html += _controls(order, artist, page, welcome)
    html += _grid_css()
    html += '<div id="table-container"></div>\n'
    html += _gallery_js(rows)
    if pages:
        html += _pagination(pages, page, order, artist, mobile)
    html += tail()
    return html


# ---------------------------------------------------------------------------
# CSS helpers
# ---------------------------------------------------------------------------

def _pagination_css(mobile: bool) -> str:
    base = """
<style>
.pagination { display:flex; justify-content:center; list-style:none; padding:0; }
.pagination li a { display:block; text-decoration:none;
  border:1px solid gray; color:black; margin:0 4px; border-radius:5px; }
.pagination li a.active { background-color:cornflowerblue; color:white; }
.pagination li a:hover:not(.active):not(.disabled) { background-color:lightgray; }
.pagination li a.disabled { color:lightgray; border-color:lightgray;
  cursor:not-allowed; pointer-events:none; }
"""
    if mobile:
        return base + """
.pagination li a { padding:12px 16px; }
@media only screen and (max-width:600px) {
  .pagination li a { display:none; }
  .pagination li a.active,
  .pagination li.prev-next a { display:block; }
}
</style>
"""
    return base + ".pagination li a { padding:8px 12px; }\n</style>\n"


def _grid_css() -> str:
    return f"""<style>
#table-container {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax({WIDTH}px, 1fr));
  gap: 20px;
  padding: 10px;
}}
.grid-item {{ display:flex; justify-content:center; align-items:center; }}
.grid-item table {{ background:white; width:100%; }}
</style>
"""


# ---------------------------------------------------------------------------
# Controls — sort radio + artist select + welcome text
# ---------------------------------------------------------------------------

def _controls(order: str, artist: str, page: int, welcome: str) -> str:
    ck_date  = "checked" if order == "Date"  else ""
    ck_title = "checked" if order == "Title" else ""

    sel_all    = "selected" if artist == "all"        else ""
    sel_rick   = "selected" if artist == "Rick Spates" else ""
    sel_rspates= "selected" if artist == "rspates"    else ""

    artist_esc = html_mod.escape(artist)

    return f"""<table><tr valign="top">
<td>
  <form id="sortForm" action="/browseSort" method="GET">
    <input type="radio" name="order" value="Date"
      onclick="document.getElementById('sortForm').submit()" {ck_date}> Date<br/>
    <input type="radio" name="order" value="Title"
      onclick="document.getElementById('sortForm').submit()" {ck_title}> Title<br/>
    <input type="hidden" name="artist" value="{artist_esc}">
    <input type="hidden" name="page" value="{page}">
  </form>
  <form id="filterForm" action="/browseFilter" method="GET">
    <select name="artist" id="artistSelect">
      <option value="all" {sel_all}>all</option>
      <option value="Rick Spates" {sel_rick}>Rick Spates</option>
      <option value="rspates" {sel_rspates}>rspates</option>
    </select>
    <input type="hidden" name="order" value="{order}">
    <input type="hidden" name="page" value="{page}">
  </form>
  <script>
    document.getElementById('artistSelect').addEventListener('change', function() {{
      document.getElementById('filterForm').submit();
    }});
  </script>
</td>
<td><p>{html_mod.escape(welcome)}</p></td>
</tr></table>
"""


# ---------------------------------------------------------------------------
# Gallery JavaScript
# ---------------------------------------------------------------------------

def _gallery_js(rows: list) -> str:
    cells = []
    for row in rows:
        # Use root-relative paths so the browser resolves against whatever origin
        # it used to load the page — avoids WSL2 localhost forwarding failures
        # with parallel sub-resource requests.
        uri    = urlparse(row["uri"]).path
        thumb  = urlparse(row.get("thumb") or row["image"]).path
        label  = html_mod.escape(row.get("label", ""))
        artist = html_mod.escape(row.get("artist", ""))
        site   = row.get("site", "")

        artist_html = f'<a href="{html_mod.escape(site)}">{artist}</a>' if site else ""

        cells.append(
            "    cw = document.createElement('div');\n"
            "    cw.className = 'grid-item';\n"
            f"    cw.innerHTML = `<table><tr><td>"
            f'<a href="{uri}"><img src="{thumb}" width="{WIDTH}" /></a>'
            f"</td></tr><tr><td><center>"
            f'<a href="{uri}">{label}</a><br/>{artist_html}'
            f"</center></td></tr></table>`;\n"
            "    container.appendChild(cw);\n"
        )

    return (
        "<script>\n"
        "  const container = document.getElementById('table-container');\n"
        "  function generateTable() {\n"
        "    container.innerHTML = '';\n"
        "    let cw;\n"
        + "".join(cells)
        + "  }\n"
        "  generateTable();\n"
        "</script>\n"
    )


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def _page_url(artist: str, order: str, pg: dict) -> str:
    params = urlencode({
        "artist": artist, "order": order,
        "offset": pg["oset"], "limit": pg["lim"], "page": pg["pg"],
    })
    return f"/browseFilter?{params}"


def _pagination(pages: list, page: int, order: str,
                artist: str, mobile: bool) -> str:
    if not pages:
        return ""

    if mobile:
        return _pagination_mobile(pages, page, order, artist)
    return _pagination_desktop(pages, page, order, artist)


def _pagination_desktop(pages, page, order, artist) -> str:
    first, last = pages[0], pages[-1]
    out = '<ul class="pagination">\n'
    out += f'  <li><a href="{_page_url(artist, order, first)}">&laquo;</a></li>\n'
    for pg in pages:
        cls = ' class="active"' if pg["pg"] == page else ''
        out += f'  <li><a{cls} href="{_page_url(artist, order, pg)}">{pg["pg"]}</a></li>\n'
    out += f'  <li><a href="{_page_url(artist, order, last)}">&raquo;</a></li>\n'
    out += '</ul>\n'
    return out


def _pagination_mobile(pages, page, order, artist) -> str:
    cur  = next((p for p in pages if p["pg"] == page), pages[0])
    prev = next((p for p in pages if p["pg"] == page - 1), None)
    nxt  = next((p for p in pages if p["pg"] == page + 1), None)

    out = '<ul class="pagination">\n'

    if prev:
        out += f'  <li class="prev-next"><a href="{_page_url(artist, order, prev)}">&laquo;Previous</a></li>\n'
    else:
        out += '  <li class="prev-next"><a class="disabled">&laquo;Previous</a></li>\n'

    out += f'  <li><a class="active" href="{_page_url(artist, order, cur)}">{page}</a></li>\n'

    if nxt:
        out += f'  <li class="prev-next"><a href="{_page_url(artist, order, nxt)}">Next&raquo;</a></li>\n'
    else:
        out += '  <li class="prev-next"><a class="disabled">Next&raquo;</a></li>\n'

    out += '</ul>\n'
    return out
