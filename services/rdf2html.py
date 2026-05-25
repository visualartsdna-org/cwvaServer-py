"""Browser/detail page — port of JsonLd2Html.groovy.

Walks the RDFLib graph directly; no JSON-LD intermediate.
"""

import html as html_mod
from urllib.parse import urlparse

from rdflib import URIRef, BNode, Literal
from rdflib.namespace import RDF, RDFS, SKOS, OWL

from rdf.prefixes import VAD, WORK, THE, SCHEMA, NS_MAP, FOR_QUERY
from rdf.query_support import QuerySupport, sparql_select
from server import Server
from util.html_template import head, table_head, TABLE_TAIL, TAIL

# Predicates whose object URIs get query_label for display text
_ARTIST_PREDS = frozenset({
    str(VAD.artist),
    str(VAD.background), str(VAD.pseudonymFor),
})


# ---------------------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------------------

def _ns_map(graph):
    """Build prefix→namespace map with preferred short prefixes taking priority.

    The graph may register 'thesaurus' or 'model' for namespaces we want to
    display as 'the:' and 'vad:'.  Evict any existing entry that maps to the
    same namespace before inserting the preferred prefix.
    """
    m = dict(graph.namespaces())
    preferred = [("vad", VAD), ("work", WORK), ("the", THE),
                 ("schema", SCHEMA), ("skos", SKOS), ("rdf", RDF),
                 ("rdfs", RDFS), ("owl", OWL)]
    preferred_ns_strs = {str(ns) for _, ns in preferred}
    # Remove any graph-registered prefix that collides with a preferred namespace
    for pfx in [p for p, n in m.items() if str(n) in preferred_ns_strs]:
        del m[pfx]
    for pfx, ns in preferred:
        m[pfx] = ns
    return m


def _href(uri: str) -> str:
    """Root-relative path for internal URIs; absolute URL for external ones.

    Only http://visualartsdna.org URIs are rehosted and converted to a
    root-relative path (avoiding WSL2 localhost forwarding failures).
    External URIs (W3C, schema.org, etc.) are returned unchanged so their
    host and fragment are preserved.
    """
    if "visualartsdna.org" in uri:
        return urlparse(Server.rehost(uri)).path
    return uri


def _short(uri, ns_map: dict) -> str:
    """Return a prefixed name for uri, or the last path/fragment segment.

    Bare namespace URIs (no local name after the prefix) are returned in full
    rather than abbreviated to 'pfx:' — e.g. rdfs:isDefinedBy values.
    """
    s = str(uri)
    for pfx, ns in ns_map.items():
        ns_s = str(ns)
        if pfx and s.startswith(ns_s):
            local = s[len(ns_s):]
            if local:
                return f"{pfx}:{local}"
    return s.split("#")[-1].split("/")[-1] or s


def _to_curi(uri: str, ns_map: dict) -> str:
    """Return a CURI (e.g. work:abc123) if a prefix matches, else the full URI."""
    for pfx, ns in ns_map.items():
        ns_s = str(ns)
        if pfx and uri.startswith(ns_s):
            local = uri[len(ns_s):]
            if local:
                return f"{pfx}:{local}"
    return uri


# ---------------------------------------------------------------------------
# Property label lookup
# ---------------------------------------------------------------------------

def _fetch_prop_labels(preds: list, qs: QuerySupport) -> dict:
    """Return {pred_uri_str: label} for all predicates in one SPARQL query."""
    if not preds:
        return {}
    values_clause = " ".join(f"<{str(p)}>" for p in preds)
    rows = sparql_select(
        qs.graph,
        f"""SELECT ?p ?label WHERE {{
            VALUES ?p {{ {values_clause} }}
            ?p rdfs:label ?label .
        }}"""
    )
    return {row["p"]: row["label"] for row in rows}


# ---------------------------------------------------------------------------
# 3D model-viewer widget
# ---------------------------------------------------------------------------

def _do_3d(src: str) -> str:
    return (
        f'<model-viewer src="{src}" alt="3D model" camera-controls auto-rotate '
        f'style="width:500px;height:500px;"></model-viewer>\n'
        f'<script type="module" src="https://unpkg.com/@google/model-viewer'
        f'/dist/model-viewer.min.js"></script>'
    )


# ---------------------------------------------------------------------------
# Single-value renderer
# ---------------------------------------------------------------------------

def _render_one(pred: URIRef, val, qs: QuerySupport, ns_map: dict, host: str) -> str:
    """Render a single non-blank-node object value as an HTML string."""
    pred_s = str(pred)

    if isinstance(val, BNode):
        return ""  # blank nodes handled at predicate level

    if isinstance(val, Literal):
        # RDF literals come from controlled TTL files — pass HTML entities through unescaped
        return str(val).replace("\n", "<br/>")

    # val is URIRef from here down
    uri  = str(val)
    href = _href(uri)

    # schema:image → image wrapped in link
    if pred_s == str(SCHEMA.image):
        return f'<a href="{href}"><img src="{href}" width="500"></a>'

    # vad:image3d → model-viewer
    if pred_s == str(VAD.image3d):
        return _do_3d(href)

    # vad:qrcode → small image
    if pred_s == str(VAD.qrcode):
        return f'<a href="{href}"><img src="{href}" width="100"></a>'

    # the:pdfDocument → plain link, filename only as display text
    if pred_s == str(THE.pdfDocument):
        filename = urlparse(uri).path.split("/")[-1]
        return f'<a href="{href}">{html_mod.escape(filename)}</a>'

    # the:tag → link with label lookup
    if pred_s == str(THE.tag):
        label = qs.query_label(uri) or _short(val, ns_map)
        return f'<a href="{href}">{html_mod.escape(label)}</a>'

    # Artist properties → internal root-relative link with label lookup
    if pred_s in _ARTIST_PREDS:
        label = qs.query_label(uri) or _short(val, ns_map)
        return f'<a href="{href}">{html_mod.escape(label)}</a>'

    # Plain-text properties
    if pred_s in (str(VAD.media), str(SCHEMA.keywords)):
        return html_mod.escape(uri)

    # General URI → linked short name
    return f'<a href="{href}">{html_mod.escape(_short(val, ns_map))}</a>'


# ---------------------------------------------------------------------------
# Predicate-level renderer (handles multi-value and special cases)
# ---------------------------------------------------------------------------

def _render_pred(pred: URIRef, values: list, is_collection: bool,
                 qs: QuerySupport, ns_map: dict, host: str) -> str:
    pred_s = str(pred)

    # the:mdDocument: /md2html?doc=<absolute-url>; display filename only
    if pred_s == str(THE.mdDocument):
        from urllib.parse import urlencode
        uris = [str(v) for v in values if not isinstance(v, BNode)]
        links = []
        for u in uris:
            doc_url = Server.rehost(u)          # absolute URL for the service to fetch
            filename = urlparse(u).path.split("/")[-1]
            qs_str = urlencode({"doc": doc_url})
            links.append(f'<a href="/md2html?{qs_str}">{html_mod.escape(filename)}</a>')
        return ", ".join(links)

    # skos:member inside a Collection → batch label lookup
    if pred_s == str(SKOS.member) and is_collection:
        uri_list = [str(v) for v in values if isinstance(v, URIRef)]
        label_map = qs.query_collection(uri_list)
        links = []
        for uri in uri_list:
            label = label_map.get(uri, _short(URIRef(uri), ns_map))
            links.append(f'<a href="{_href(uri)}">{html_mod.escape(label)}</a>')
        return ", ".join(links)

    # Default: render each non-blank value and join with commas
    parts = [_render_one(pred, v, qs, ns_map, host)
             for v in values if not isinstance(v, BNode)]
    return ", ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Recursive property-row builder
# ---------------------------------------------------------------------------

def _build_rows(subject, graph, qs: QuerySupport, ns_map: dict, host: str) -> list:
    """Return a list of <tr> HTML strings for subject's properties."""
    props: dict = {}
    for p, o in graph.predicate_objects(subject):
        props.setdefault(p, []).append(o)

    if not props:
        return []

    is_collection = any(
        str(o) == str(SKOS.Collection)
        for o in props.get(RDF.type, [])
    )

    # Batch-fetch ontology labels for all predicates in one query
    prop_labels = _fetch_prop_labels(list(props.keys()), qs)

    def _display_name(pred: URIRef) -> str:
        label = prop_labels.get(str(pred), "")
        return html_mod.escape(label if label else _short(pred, ns_map))

    def _sort_key(pred: URIRef):
        if pred == RDF.type:
            return (0, "")
        label = prop_labels.get(str(pred), _short(pred, ns_map))
        return (1, label.lower())

    sorted_preds = sorted(props.keys(), key=_sort_key)

    rows = []
    for pred in sorted_preds:
        values = props[pred]
        display = _display_name(pred)

        reg = []
        labeled_bn_cells = []
        unlabeled_bns = []

        for v in values:
            if isinstance(v, BNode):
                bn_label = next(graph.objects(v, RDFS.label), None)
                if bn_label is not None:
                    labeled_bn_cells.append(html_mod.escape(str(bn_label)))
                else:
                    unlabeled_bns.append(v)
            else:
                reg.append(v)

        # skos:member on a Collection → one row per member
        if pred == SKOS.member and is_collection and reg:
            uri_list = [str(v) for v in reg if isinstance(v, URIRef)]
            label_map = qs.query_collection(uri_list)
            for uri in uri_list:
                label = label_map.get(uri, _short(URIRef(uri), ns_map))
                rows.append(
                    f'<tr><td>{display}</td>'
                    f'<td><a href="{_href(uri)}">{html_mod.escape(label)}</a></td></tr>\n'
                )
            continue

        # Regular values + labeled BNs → one row
        parts = []
        if reg:
            cell = _render_pred(pred, reg, is_collection, qs, ns_map, host)
            if cell:
                parts.append(cell)
        parts.extend(labeled_bn_cells)

        if parts:
            rows.append(
                f'<tr><td>{display}</td><td>{", ".join(parts)}</td></tr>\n'
            )

        # Unlabeled BNs → separator + recursive flatten
        for bn in unlabeled_bns:
            rows.append('<tr><td colspan="2">&nbsp;</td></tr>\n')
            rows.extend(_build_rows(bn, graph, qs, ns_map, host))

    return rows


# ---------------------------------------------------------------------------
# Tags section renderer
# ---------------------------------------------------------------------------

def _render_tags(tags: list, qs: QuerySupport, ns_map: dict, host: str) -> str:
    """Render the Tags section — a heading + table of tag/label/description rows."""
    if not tags:
        return ""

    # Fetch ontology labels for the three fixed tag properties in one query
    tag_prop_labels = _fetch_prop_labels([THE.tag, RDFS.label, SKOS.definition], qs)

    def _pname(p) -> str:
        label = tag_prop_labels.get(str(p), "")
        return html_mod.escape(label if label else _short(p, ns_map))

    tag_col   = _pname(THE.tag)
    label_col = _pname(RDFS.label)
    desc_col  = _pname(SKOS.definition)

    out = "<h3>Tags</h3>\n"
    out += table_head("Property", "Value")
    for row in tags:
        uri   = row.get("c", "")
        label = row.get("l", "")
        desc  = row.get("d", "")
        if uri:
            short = _short(URIRef(uri), ns_map)
            out += f'<tr><td>{tag_col}</td><td><a href="{_href(uri)}">{html_mod.escape(short)}</a></td></tr>\n'
        if label:
            out += f'<tr><td>{label_col}</td><td>{html_mod.escape(label)}</td></tr>\n'
        if desc:
            out += f'<tr><td>{desc_col}</td><td>{html_mod.escape(desc)}</td></tr>\n'
    out += TABLE_TAIL
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process(srv: Server, ns: str, guid: str, host: str, is_mobile: bool) -> str:
    """Build and return the full HTML for a browser/detail page."""
    graph = srv.dbm.rdfs
    qs = QuerySupport(graph)

    result = qs.query(ns, guid)
    label    = result["label"]
    main_g   = result[f"{ns}:{guid}"]

    # Route keys use full words; display uses preferred short prefixes
    _DISPLAY_PFX = {"work": "work", "model": "vad", "thesaurus": "the"}

    uri_str   = str(NS_MAP[ns]) + guid
    uri_short = f"{_DISPLAY_PFX.get(ns, ns)}:{guid}"
    uri_path  = _href(uri_str)

    nm = _ns_map(main_g)

    display_name = label if label else uri_short

    html = head(host, server=srv)
    html += f'<h3 id="title">About: {html_mod.escape(display_name)}</h3>\n'
    html += table_head("Property", "Value")

    # @id row — display as CURI, link to full URI
    curi = _to_curi(uri_str, nm)
    html += f'<tr><td>@id</td><td><a href="{uri_path}">{html_mod.escape(curi)}</a></td></tr>\n'

    # All property rows
    subject = URIRef(uri_str)
    html += "".join(_build_rows(subject, main_g, qs, nm, host))

    html += TABLE_TAIL

    # Tags section
    tags = qs.query_tags(uri_str)
    html += _render_tags(tags, qs, nm, host)

    html += TAIL
    return html
