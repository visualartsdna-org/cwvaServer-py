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
from util.html_template import ai_notice, head, table_head, TABLE_TAIL, tail

# Rendering rule for owl:ObjectProperty values:
#
#   If a property object or collection member is discretely anchored — rendered
#   as its own standalone link — the anchor text is its label, with the CURI as
#   fallback when no label exists.
#
# This applies uniformly, including to the structural predicates rdf:type,
# rdfs:subClassOf and skos:broader/narrower/related/inScheme, which
# schemaSupplement.ttl ("rdfs hardening") also declares owl:ObjectProperty.
# Values that are not discretely anchored — images, the model-viewer embed,
# plain-text properties — early-return before the general rule below.


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


def _label_or(uri: str, obj_labels: dict, fallback: str) -> str:
    """Return the object's label when it has one, else fallback.

    Qualifies the discrete-anchor rule: an object is anchored by its label only
    when it actually carries one.  A plain document URI has no label and falls
    back to its filename; a complex object (e.g. a schema:VideoObject with a
    name) supplies one and is anchored by it instead.

    query_labels() returns the URI itself when nothing is found, so that case
    counts as "no label".
    """
    label = obj_labels.get(uri, "")
    return label if label and label != uri else fallback


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

_VIDEO_SUFFIXES = (".mp4", ".webm", ".m4v", ".mov", ".ogv")


def _media_href(uri: str) -> str:
    """Root-relative /media path for a locally hosted video; external URLs pass through.

    Routed to /media regardless of the folder in the URI, because /images and
    /thumbnails cannot serve video: no Range support, and the image handler
    reads the whole file into memory.  The /media handler falls back to a
    filename-only search, so the basename is enough.
    """
    if "visualartsdna.org" not in uri:
        return uri
    return "/media/" + urlparse(uri).path.split("/")[-1]


def _do_video(src: str, caption: str = "") -> str:
    """Native <video> player. No video.js — every current browser handles this.

    The caption is anchored to the video URL, consistent with the
    discrete-anchor rule: a label shown for a resource links to that resource.
    Colour is left to the site's a:link styling rather than being overridden
    here, so it reads as a link.
    """
    cap = (f'<div style="font-size:0.8em;margin-top:0.3em;">'
           f'<a href="{src}">{html_mod.escape(caption)}</a></div>') if caption else ""
    return (
        f'<video controls preload="metadata" width="500" '
        f'style="max-width:100%;height:auto;background:#000;">'
        f'<source src="{src}" type="video/mp4">'
        f'Your browser cannot play this video. '
        f'<a href="{src}">Download it instead</a>.'
        f'</video>{cap}'
    )


def _resolve_video(val, graph, qs: QuerySupport):
    """Return (media_url, caption) for a schema:video value.

    Handles both authoring shapes:
      schema:video <http://.../clip.mp4>
      schema:video [ a schema:VideoObject ;
                     schema:contentUrl <http://.../clip.mp4> ;
                     rdfs:label  "360 degree view" ;
                     schema:name "360 degree turntable, 12s" ]

    Caption prefers rdfs:label: on a VideoObject it is always present and is
    the site-wide label predicate.  schema:name is optional schema.org interop
    and may carry a different literal, so it is only a fallback — a direct-URI
    video with no VideoObject has neither and renders uncaptioned.

    Blank-node properties are already in the page graph via the promoteBNData
    CONSTRUCT; a named VideoObject needs one lookup.
    """
    curl = next(graph.objects(val, SCHEMA.contentUrl), None)
    if curl is None and isinstance(val, URIRef):
        found = qs.query_one_property(str(val), str(SCHEMA.contentUrl))
        curl = URIRef(found) if found else None
    if curl is None:
        if not isinstance(val, URIRef):
            return None, ""          # blank node with no contentUrl
        curl = val                   # direct URI to the file

    caption = (next(graph.objects(val, RDFS.label), None)
               or next(graph.objects(val, SCHEMA.name), None))
    if caption is None and isinstance(val, URIRef) and val != curl:
        caption = qs.query_label(str(val))
    return str(curl), str(caption or "")


def _do_3d(src: str, skybox: str = "") -> str:
    skybox_attr = f' skybox-image="{skybox}"' if skybox else ""
    return (
        f'<model-viewer src="{src}" alt="3D model"{skybox_attr} '
        f'camera-controls auto-rotate '
        f'style="width:500px;height:500px;"></model-viewer>\n'
        f'<script type="module" src="https://unpkg.com/@google/model-viewer'
        f'/dist/model-viewer.min.js"></script>'
    )


# ---------------------------------------------------------------------------
# Single-value renderer
# ---------------------------------------------------------------------------

def _render_one(pred: URIRef, val, qs: QuerySupport, ns_map: dict, host: str,
                subject=None, object_props: frozenset = frozenset(),
                obj_labels: dict = None) -> str:
    """Render a single non-blank-node object value as an HTML string.

    object_props / obj_labels are the per-page batches built in _build_rows:
    which predicates are owl:ObjectProperty, and the label for every URI they
    point at.  Both are looked up rather than queried per value.
    """
    pred_s = str(pred)
    obj_labels = obj_labels or {}

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

    # vad:image3d → model-viewer, with the work's background HDR as skybox
    if pred_s == str(VAD.image3d):
        skybox = ""
        if subject is not None:
            bkg_uri = qs.query_one_property(str(subject), str(VAD.background))
            if bkg_uri:
                hdr = qs.query_one_property(bkg_uri, str(SCHEMA.image))
                if hdr:
                    skybox = _href(str(hdr))
        return _do_3d(href, skybox)

    # vad:qrcode → small image
    if pred_s == str(VAD.qrcode):
        return f'<a href="{href}"><img src="{href}" width="100"></a>'

    # the:pdfDocument → link; label when the object carries one, else filename
    if pred_s == str(THE.pdfDocument):
        filename = urlparse(uri).path.split("/")[-1]
        text = _label_or(uri, obj_labels, filename)
        return f'<a href="{href}">{html_mod.escape(text)}</a>'

    # Plain-text properties
    if pred_s in (str(VAD.media), str(SCHEMA.keywords)):
        return html_mod.escape(uri)

    # owl:ObjectProperty → label as anchor text.  Subsumes what used to be
    # special cases for the:tag and the artist predicates: all are declared
    # owl:ObjectProperty, so the general rule now covers them.
    if pred_s in object_props:
        text = _label_or(uri, obj_labels, _short(val, ns_map))
        return f'<a href="{href}">{html_mod.escape(text)}</a>'

    # General URI → linked short name
    return f'<a href="{href}">{html_mod.escape(_short(val, ns_map))}</a>'


# ---------------------------------------------------------------------------
# Predicate-level renderer (handles multi-value and special cases)
# ---------------------------------------------------------------------------

def _render_pred(pred: URIRef, values: list,
                 qs: QuerySupport, ns_map: dict, host: str, subject=None,
                 object_props: frozenset = frozenset(),
                 obj_labels: dict = None) -> str:
    pred_s = str(pred)
    obj_labels = obj_labels or {}

    # Multi-value ObjectProperty rows render alphabetically by the text the
    # reader actually sees.  RDFLib graph iteration order is arbitrary — it is
    # neither source order nor stable — so without this the same page can list
    # values in a different order from one load to the next.  Literal-valued
    # predicates are left alone: source order can carry meaning in prose.
    if len(values) > 1 and pred_s in object_props:
        values = sorted(
            values,
            key=lambda v: "" if isinstance(v, BNode)
            else _label_or(str(v), obj_labels, _short(v, ns_map)).lower()
        )

    # the:mdDocument: /md2html?doc=<absolute-url>; label when the object carries
    # one, else the filename
    if pred_s == str(THE.mdDocument):
        from urllib.parse import urlencode
        uris = [str(v) for v in values if not isinstance(v, BNode)]
        links = []
        for u in uris:
            doc_url = Server.rehost(u)          # absolute URL for the service to fetch
            filename = urlparse(u).path.split("/")[-1]
            text = _label_or(u, obj_labels, filename)
            qs_str = urlencode({"doc": doc_url})
            links.append(f'<a href="/md2html?{qs_str}">{html_mod.escape(text)}</a>')
        return ", ".join(links)

    # NB: skos:member on a Collection never reaches here — _build_rows()
    # intercepts that case and emits one row per member.

    # Default: render each non-blank value and join with commas
    parts = [_render_one(pred, v, qs, ns_map, host, subject,
                         object_props, obj_labels)
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

    # Which predicates are owl:ObjectProperty (one query), and the labels for
    # every URI they point at (one more).  Batched here so _render_one does
    # lookups rather than a query per value.
    object_props = qs.query_object_properties([str(p) for p in props.keys()])
    obj_uris = {
        str(v)
        for p, vals in props.items() if str(p) in object_props
        for v in vals if isinstance(v, URIRef)
    }
    obj_labels = qs.query_labels(sorted(obj_uris)) if obj_uris else {}

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

        # schema:video → player row.  Intercepted ahead of the blank-node split
        # below: a VideoObject is an unlabelled blank node and would otherwise
        # be flattened into recursive property sub-rows instead of a player.
        if pred == SCHEMA.video:
            players = []
            for v in values:
                src, caption = _resolve_video(v, graph, qs)
                if src:
                    players.append(_do_video(_media_href(src), caption))
            if players:
                rows.append(f'<tr><td>{display}</td>'
                            f'<td>{"".join(players)}</td></tr>\n')
            continue

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

        # skos:member on a Collection → one row per member, alphabetical.
        # query_collection() applies the ORDER BY and returns every member, so
        # iterate its result rather than uri_list.
        if pred == SKOS.member and is_collection and reg:
            uri_list = [str(v) for v in reg if isinstance(v, URIRef)]
            for uri, label in qs.query_collection(uri_list).items():
                # COALESCE falls back to the URI itself when unlabeled — show
                # the short form instead
                if label == uri:
                    label = _short(URIRef(uri), ns_map)
                rows.append(
                    f'<tr><td>{display}</td>'
                    f'<td><a href="{_href(uri)}">{html_mod.escape(label)}</a></td></tr>\n'
                )
            continue

        # Regular values + labeled BNs → one row
        parts = []
        if reg:
            cell = _render_pred(pred, reg, qs, ns_map, host, subject,
                                object_props, obj_labels)
            if cell:
                if pred == VAD.image3d:
                    subject_curi = _to_curi(str(subject), ns_map)
                    cell += (f'<br/><a href="/modelviewer?work='
                             f'{html_mod.escape(subject_curi)}">3D Viewer</a>')
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

    # Fetch ontology label for skos:definition only (tag row uses fixed "Tag" header)
    tag_prop_labels = _fetch_prop_labels([SKOS.definition], qs)
    desc_col = tag_prop_labels.get(str(SKOS.definition), "") or _short(SKOS.definition, ns_map)

    out = "<h3>Tags</h3>\n"
    out += table_head("Property", "Value")
    for row in tags:
        uri   = row.get("c", "")
        label = row.get("l", "")
        desc  = row.get("d", "")
        if uri:
            # Use label as anchor text when available; fall back to CURI
            anchor_text = label if label else _to_curi(uri, ns_map)
            out += (f'<tr><td>Tag</td>'
                    f'<td><a href="{_href(uri)}">{html_mod.escape(anchor_text)}</a></td></tr>\n')
        if desc:
            out += f'<tr><td>{html_mod.escape(desc_col)}</td><td>{html_mod.escape(desc)}</td></tr>\n'
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
    # Property labels (first column) — slightly smaller and italic
    html += '<style>td:first-child { font-size: 0.85em; font-style: italic; }</style>\n'
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

    # AI-generated content disclosure — only when the subject displayed on this
    # page is itself typed the:AI.  Works linking to an AI criticism do not
    # carry the notice; the link direction is criticism -> work, so a work page
    # never displays the criticism in the first place.
    if (subject, RDF.type, THE.AI) in main_g:
        html += ai_notice()

    html += tail()
    return html
