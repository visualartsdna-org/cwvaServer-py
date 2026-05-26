"""Explore page — ontology/thesaurus graphs, collections, data visualizations."""

import html as html_mod
import os
from urllib.parse import urlparse

from rdf.prefixes import VAD, WORK, THE, SCHEMA, FOR_QUERY
from rdf.query_support import QuerySupport, sparql_select
from server import Server
from util.html_template import head, tail

# Thesaurus schemes that are internal-only and excluded from the browser dropdown
_EXCLUDED_SCHEMES = ("/digitalNotes", "/paintingNotes")

# Work GUIDs for the three data-visualization model-viewers
_VIZ_GUIDS = [
    "93a8d9a6-a074-4f9d-b668-c0ce3c98f710",
    "1fd19483-f64a-4409-b83c-870904814b35",
    "4b0c867d-acc0-4f05-a83e-a4c5926f70bd",
]


# ---------------------------------------------------------------------------
# URI helpers
# ---------------------------------------------------------------------------

def _href(uri: str) -> str:
    """Return a root-relative path for internal URIs, unchanged for external."""
    if "visualartsdna.org" in uri:
        return urlparse(Server.rehost(uri)).path
    return uri


def _short(uri: str) -> str:
    """Return a short label for a URI (last path/fragment segment)."""
    return uri.split("#")[-1].split("/")[-1] or uri


# ---------------------------------------------------------------------------
# Graph data builder (called by /explore/graph-data endpoint)
# ---------------------------------------------------------------------------

def build_graph_data(graph, graph_type: str, scheme_uris: list, schema_graph=None) -> dict:
    """Return {nodes, edges} JSON for Cytoscape rendering.

    graph_type:   'ontology' or 'thesaurus'
    scheme_uris:  list of fully-qualified scheme URI strings
    schema_graph: pre-inference schema store (used for ontology subClassOf edges only)

    Ontology edges come from the schema (pre-inference) store so only direct
    one-hop parent relationships appear — not the full transitive closure that
    RDFS inference adds to the rdfs store.  Scheme membership is still resolved
    against the rdfs store (where skos:inScheme triples were written by policy).

    Edge rules for ontology:
      cls in scheme, parent in scheme   → edge, both teal nodes
      cls in scheme, parent NOT scheme  → edge, parent orange terminal (no outgoing edges)
      cls NOT in scheme                 → skip entirely
    """
    if not scheme_uris:
        return {"nodes": [], "edges": []}

    in_clause = ", ".join(f"<{s}>" for s in scheme_uris)
    nodes: dict = {}
    edges: list = []

    if graph_type == "ontology":
        # Step 1: scheme members with labels — rdfs store (has inferred/policy data)
        member_rows = list(graph.query(FOR_QUERY + f"""
            SELECT DISTINCT ?cls ?label ?comment WHERE {{
                ?cls skos:inScheme ?scheme ;
                     rdfs:label ?label .
                OPTIONAL {{ ?cls rdfs:comment ?comment }}
                FILTER(!isBlank(?cls))
                FILTER(?scheme IN ({in_clause}))
            }}
        """))
        scheme_members  = {str(r.cls): str(r.label)   for r in member_rows}
        scheme_comments = {str(r.cls): str(r.comment) for r in member_rows if r.comment}
        Server.log_out(f"  graph-data ontology: {len(scheme_members)} members, schemes={scheme_uris}")

        if not scheme_members:
            return {"nodes": [], "edges": []}

        # Step 2: direct one-hop subClassOf edges — schema store (pre-inference)
        edge_src = schema_graph if schema_graph is not None else graph
        subclass_rows = list(edge_src.query(FOR_QUERY + """
            SELECT ?cls ?parent WHERE {
                ?cls rdfs:subClassOf ?parent .
                FILTER(!isBlank(?cls) && !isBlank(?parent))
            }
        """))

        # Step 3: build graph — only edges where cls is a scheme member
        external_parents: set = set()
        for row in subclass_rows:
            cls    = str(row.cls)
            parent = str(row.parent)
            if cls not in scheme_members:
                continue
            if cls not in nodes:
                nodes[cls] = {"id": cls, "label": scheme_members[cls], "type": "class",
                              "href": _href(cls), "comment": scheme_comments.get(cls, "")}
            if parent in scheme_members:
                if parent not in nodes:
                    nodes[parent] = {"id": parent, "label": scheme_members[parent], "type": "class",
                                     "href": _href(parent), "comment": scheme_comments.get(parent, "")}
            else:
                external_parents.add(parent)
                if parent not in nodes:
                    nodes[parent] = {"id": parent, "label": _short(parent), "type": "external",
                                     "href": _href(parent), "comment": ""}
            edges.append({"source": cls, "target": parent})

        # Batch-fetch labels for external terminal nodes from rdfs store
        if external_parents:
            ext_values = " ".join(f"<{p}>" for p in external_parents)
            for row in graph.query(FOR_QUERY + f"""
                SELECT ?uri ?label WHERE {{
                    ?uri rdfs:label ?label .
                    VALUES ?uri {{ {ext_values} }}
                }}
            """):
                uri = str(row.uri)
                if uri in nodes:
                    nodes[uri]["label"] = str(row.label)

    elif graph_type == "thesaurus":
        results = graph.query(FOR_QUERY + f"""
            SELECT DISTINCT ?concept ?label ?definition ?broader ?broaderLabel WHERE {{
                ?concept skos:inScheme ?scheme ;
                         rdfs:label ?label .
                OPTIONAL {{ ?concept skos:definition ?definition }}
                OPTIONAL {{
                    ?concept skos:broader ?broader .
                    OPTIONAL {{ ?broader rdfs:label ?broaderLabel }}
                    FILTER(!isBlank(?broader))
                }}
                FILTER(!isBlank(?concept))
                FILTER(?scheme IN ({in_clause}))
            }}
        """)
        rows = list(results)
        Server.log_out(f"  graph-data thesaurus: {len(rows)} rows, schemes={scheme_uris}")
        for row in rows:
            concept = str(row.concept)
            label   = str(row.label)
            if concept not in nodes:
                nodes[concept] = {"id": concept, "label": label, "type": "concept",
                                  "href": _href(concept),
                                  "comment": str(row.definition) if row.definition else ""}
            if row.broader:
                broader = str(row.broader)
                if broader not in nodes:
                    b_label = str(row.broaderLabel) if row.broaderLabel else _short(broader)
                    nodes[broader] = {"id": broader, "label": b_label, "type": "concept",
                                      "href": _href(broader), "comment": ""}
                edges.append({"source": concept, "target": broader})

    Server.log_out(f"  graph-data result: {len(nodes)} nodes, {len(edges)} edges")
    return {"nodes": list(nodes.values()), "edges": edges}


# ---------------------------------------------------------------------------
# Data-visualization model-viewer items
# ---------------------------------------------------------------------------

def _load_viz_items(srv: Server, qs: QuerySupport) -> list:
    """Query the 3 GLB work items for the Data Visualizations section."""
    items = []
    for guid in _VIZ_GUIDS:
        uri = str(WORK) + guid
        rows = sparql_select(qs.graph, f"""
            SELECT ?label ?img ?tagImg WHERE {{
                <{uri}> rdfs:label ?label ;
                         schema:image ?img .
                OPTIONAL {{
                    <{uri}> the:tag ?tag .
                    ?tag schema:image ?tagImg .
                }}
            }} LIMIT 1
        """)
        if rows:
            row = rows[0]
            img     = Server.rehost(row.get("img", ""))
            tag_img = Server.rehost(row.get("tagImg", "")) if row.get("tagImg") else ""
            items.append({
                "label":   row.get("label", guid),
                "src":     urlparse(img).path if img else "",
                "bg":      urlparse(tag_img).path if tag_img else "",
                "uri":     uri,
            })
    return items


# ---------------------------------------------------------------------------
# HTML fragments
# ---------------------------------------------------------------------------

_CSS = """\
<style>
:root {
  --clr-accent: #b8860b;
  --clr-border: #e8e4df;
  --clr-surface: #ffffff;
  --clr-bg: #faf9f7;
  --clr-muted: #6b6560;
  --radius: 8px;
  --shadow: 0 2px 8px rgba(45,41,38,.07);
}
.xp-subtitle { color:var(--clr-muted); margin:-0.5rem 0 1.5rem; font-size:.95rem; }
.xp-card {
  background:var(--clr-surface); border-radius:var(--radius);
  box-shadow:var(--shadow); padding:1.5rem 1.75rem; margin-bottom:1.5rem;
  border:1px solid var(--clr-border);
}
.xp-card h2 {
  font-size:1.1rem; font-weight:600; margin:0 0 1rem;
  padding-bottom:.5rem; border-bottom:2px solid var(--clr-accent);
  display:inline-block;
}
.xp-quick { display:flex; gap:1rem; flex-wrap:wrap; }
.xp-qlink {
  display:inline-flex; align-items:center; gap:.5rem;
  padding:.6rem 1.2rem; background:var(--clr-bg);
  border:1px solid var(--clr-border); border-radius:var(--radius);
  text-decoration:none; color:#2d2926; font-weight:500; font-size:.95rem;
  transition:.2s;
}
.xp-qlink:hover { background:var(--clr-accent); color:#fff; border-color:var(--clr-accent); }
.xp-two { display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:1.5rem; margin-bottom:1.5rem; }
.xp-form-row { display:flex; flex-direction:column; gap:.5rem; margin-bottom:.75rem; }
.xp-form-row label { font-weight:500; font-size:.9rem; }
.xp-form-row select {
  padding:.6rem; border:1px solid var(--clr-border); border-radius:var(--radius);
  font-family:inherit; font-size:.9rem; background:var(--clr-surface); cursor:pointer;
}
.xp-form-row select:focus { outline:none; border-color:var(--clr-accent); }
.xp-hint { font-size:.78rem; color:var(--clr-muted); }
.xp-chk { display:flex; align-items:center; gap:.4rem; margin:.4rem 0 .75rem; font-size:.9rem; }
.xp-chk input { accent-color:var(--clr-accent); width:16px; height:16px; cursor:pointer; }
.xp-actions { display:flex; gap:.75rem; flex-wrap:wrap; }
.btn {
  display:inline-flex; align-items:center; gap:.4rem; padding:.55rem 1.25rem;
  border:none; border-radius:var(--radius); font-family:inherit;
  font-size:.9rem; font-weight:500; cursor:pointer; transition:.2s; text-decoration:none;
}
.btn-primary { background:var(--clr-accent); color:#fff; }
.btn-primary:hover { background:#996f0a; }
.btn-secondary {
  background:var(--clr-bg); color:#2d2926;
  border:1px solid var(--clr-border);
}
.btn-secondary:hover { color:var(--clr-accent); background:var(--clr-border); }
/* graph area */
#graph-container { display:none; margin-bottom:1.5rem; }
#graph-container.visible { display:block; }
#graph-title { font-size:.95rem; font-weight:600; color:var(--clr-muted); margin-bottom:.5rem; }
#graph-spinner { text-align:center; padding:2rem; font-style:italic; color:var(--clr-muted); }
#cy { width:100%; height:520px; border:1px solid var(--clr-border); border-radius:var(--radius);
      background:#fff; }
#graph-reset { margin-top:.5rem; }
/* collections */
.xp-coll-grid {
  display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:.6rem;
}
.xp-coll-link {
  display:block; padding:.6rem .9rem; background:var(--clr-bg);
  border-radius:6px; text-decoration:none; color:#2d2926; font-size:.88rem;
  border:1px solid transparent; transition:.2s;
}
.xp-coll-link:hover { border-color:var(--clr-accent); color:var(--clr-accent); padding-left:1.1rem; }
/* data viz */
.xp-viz-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:1.5rem; }
.xp-viz-label { font-size:.9rem; font-weight:500; margin-bottom:.4rem; }
/* model-viewer fullscreen */
.mv-wrap {
  position:relative; width:300px; height:300px;
  overflow:hidden; resize:both; border:1px solid #ccc;
  transition:transform .2s; background:#e8e8e8;
}
.mv-wrap.fullscreen {
  position:fixed; top:0; left:0;
  width:100vw !important; height:100vh !important;
  z-index:10000; resize:none;
}
.mv-btn {
  position:absolute; top:10px; right:10px; z-index:101;
  width:40px; height:40px; background:rgba(255,255,255,.9);
  border:1px solid #999; border-radius:50%; cursor:pointer;
  display:flex; align-items:center; justify-content:center;
  font-size:18px; box-shadow:0 2px 5px rgba(0,0,0,.2);
}
</style>"""

_CDN_SCRIPTS = """\
<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dagre/0.8.5/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.js"></script>
<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.0.0/model-viewer.min.js"></script>"""


def _quick_access(has_gcp: bool) -> str:
    metrics_link = (
        '\n    <a href="/metricTables" class="xp-qlink">&#128202; Metrics Dashboard</a>'
        if has_gcp else ""
    )
    return f"""\
<div class="xp-card">
  <h2>Quick Access</h2>
  <div class="xp-quick">
    <a href="/sparql" class="xp-qlink">&#128269; SPARQL Browser</a>{metrics_link}
  </div>
</div>
"""


def _scheme_options(schemes: list, first_selected: bool = True) -> str:
    """Build <option> elements from [{uri, label}] list."""
    parts = []
    for i, s in enumerate(schemes):
        sel = 'selected' if first_selected and i == 0 else ''
        uri_esc = html_mod.escape(s["uri"])
        lbl_esc = html_mod.escape(s["label"])
        parts.append(f'<option value="{uri_esc}" {sel}>{lbl_esc}</option>')
    return "\n".join(parts)


def _graph_section(ont_schemes: list, the_schemes: list) -> str:
    ont_opts = _scheme_options(ont_schemes)
    the_opts = _scheme_options(the_schemes)
    ont_size = max(4, min(len(ont_schemes), 8))
    the_size = max(4, min(len(the_schemes), 8))

    return f"""\
<div class="xp-two">

  <div class="xp-card">
    <h2>Ontology Graph</h2>
    <p style="font-size:.9rem;color:var(--clr-muted);margin:0 0 .75rem">
      Visualize class hierarchies within concept schemes.</p>
    <div class="xp-form-row">
      <label for="ontSelect">Select concept schemes:</label>
      <select id="ontSelect" multiple size="{ont_size}">
{ont_opts}
      </select>
      <span class="xp-hint">Ctrl / Cmd to select multiple</span>
    </div>
    <div class="xp-chk">
      <input type="checkbox" id="ontComments"><label for="ontComments">Include comments on tooltip</label>
    </div>
    <div class="xp-actions">
      <button class="btn btn-primary" onclick="generateGraph('ontology')">Generate Graph</button>
    </div>
  </div>

  <div class="xp-card">
    <h2>Thesaurus Graph</h2>
    <p style="font-size:.9rem;color:var(--clr-muted);margin:0 0 .75rem">
      Explore vocabulary hierarchies and term relationships.</p>
    <div class="xp-form-row">
      <label for="theSelect">Select concept schemes:</label>
      <select id="theSelect" multiple size="{the_size}">
{the_opts}
      </select>
      <span class="xp-hint">Ctrl / Cmd to select multiple</span>
    </div>
    <div class="xp-chk">
      <input type="checkbox" id="theDefinitions"><label for="theDefinitions">Include definitions on tooltip</label>
    </div>
    <div class="xp-actions">
      <button class="btn btn-primary" onclick="generateGraph('thesaurus')">Generate Graph</button>
    </div>
  </div>

</div>
"""


def _graph_container() -> str:
    return """\
<div id="graph-container">
  <div id="graph-title"></div>
  <div id="graph-spinner" style="display:none">Generating graph&#8230;</div>
  <div id="cy"></div>
  <button id="graph-reset" class="btn btn-secondary" onclick="resetGraph()" style="display:none">
    &#8982; Reset View
  </button>
</div>
<div id="node-tip" style="position:fixed;display:none;background:#fff;border:1px solid #ccc;
  padding:6px 10px;border-radius:4px;font-size:.82rem;max-width:320px;line-height:1.4;
  box-shadow:0 2px 8px rgba(0,0,0,.15);pointer-events:none;z-index:1000;"></div>
"""


def _collections_section(collections: list) -> str:
    if not collections:
        return ""
    links = "\n".join(
        f'  <a href="{html_mod.escape(_href(c["uri"]))}" class="xp-coll-link">'
        f'{html_mod.escape(c["label"])}</a>'
        for c in collections
    )
    return f"""\
<div class="xp-card">
  <h2>Concept Collections</h2>
  <p style="font-size:.9rem;color:var(--clr-muted);margin:0 0 .75rem">
    Browse curated collections of related concepts.</p>
  <div class="xp-coll-grid">
{links}
  </div>
</div>
"""


def _mv_widget(item: dict, idx: int) -> str:
    """Render a model-viewer widget with fullscreen toggle (port of Groovy do3d)."""
    src  = html_mod.escape(item["src"])
    bg   = item.get("bg", "")
    lbl  = html_mod.escape(item["label"])
    bg_attrs = ""
    if bg:
        bg_esc = html_mod.escape(bg)
        bg_attrs = f'environment-image="{bg_esc}" skybox-image="{bg_esc}"'

    return f"""\
<div>
  <div class="xp-viz-label">{lbl}</div>
  <div id="mv-wrap-{idx}" class="mv-wrap">
    <button id="mv-btn-{idx}" class="mv-btn" onclick="mvToggle{idx}(true)" title="Fullscreen">&#x26F6;</button>
    <model-viewer src="{src}" camera-controls tone-mapping="neutral"
      shadow-intensity="0" min-camera-orbit="auto auto 4m"
      {bg_attrs}
      style="width:100%;height:100%;display:block;">
    </model-viewer>
  </div>
</div>
<script>
(function(){{
  const wrap = document.getElementById('mv-wrap-{idx}');
  const btn  = document.getElementById('mv-btn-{idx}');
  const KEY  = 'fs-{idx}';
  window.mvToggle = window.mvToggle || {{}};
  window['mvToggle{idx}'] = function(manual) {{
    const isFs = wrap.classList.contains('fullscreen');
    if (!isFs) {{
      wrap.classList.add('fullscreen');
      btn.innerHTML = '&#x2715;';
      document.body.style.overflow = 'hidden';
      history.pushState({{view: KEY}}, '');
    }} else {{
      wrap.classList.remove('fullscreen');
      btn.innerHTML = '&#x26F6;';
      document.body.style.overflow = '';
      if (manual && history.state && history.state.view === KEY) history.back();
    }}
    const mv = wrap.querySelector('model-viewer');
    if (mv) mv.dispatchEvent(new Event('resize'));
  }};
  window.addEventListener('popstate', function() {{
    if (wrap.classList.contains('fullscreen')) window['mvToggle{idx}'](false);
  }});
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape' && wrap.classList.contains('fullscreen')) window['mvToggle{idx}'](true);
  }});
}})();
</script>"""


def _viz_section(viz_items: list) -> str:
    if not viz_items:
        return ""
    widgets = "\n".join(_mv_widget(item, i + 1) for i, item in enumerate(viz_items))
    controls = """\
<p style="font-size:.82rem;color:var(--clr-muted);margin-top:.75rem">
  <img src="/images/left-click.png"  width="18" style="vertical-align:middle"> drag &nbsp;
  <img src="/images/right-click.png" width="18" style="vertical-align:middle"> pan &nbsp;
  <img src="/images/scroll.png"      width="18" style="vertical-align:middle"> zoom
</p>"""
    return f"""\
<div class="xp-card">
  <h2>Data Visualizations</h2>
  <p style="font-size:.9rem;color:var(--clr-muted);margin:0 0 1rem">
    Interactive 3D graphics — drag, pan, zoom.</p>
  <div class="xp-viz-grid">
{widgets}
  </div>
  {controls}
</div>
"""


def _js(ont_schemes: list, the_schemes: list) -> str:
    """Cytoscape graph rendering + generate/viewAll helpers."""
    # Pass scheme-to-label lookup to JS for the graph title
    import json
    scheme_labels = {s["uri"]: s["label"] for s in ont_schemes + the_schemes}
    scheme_labels_json = json.dumps(scheme_labels)

    return f"""\
<script>
const _schemeLabels = {scheme_labels_json};
let _cy = null;

function generateGraph(type) {{
  const sel    = document.getElementById(type === 'ontology' ? 'ontSelect' : 'theSelect');
  const schemes = Array.from(sel.selectedOptions).map(o => o.value);
  if (!schemes.length) {{ alert('Please select at least one scheme.'); return; }}
  const chkId  = type === 'ontology' ? 'ontComments' : 'theDefinitions';
  const inclText = document.getElementById(chkId).checked;
  _fetchAndRender(type, schemes, inclText);
}}

function resetGraph() {{
  if (_cy) _cy.fit();
}}

function _fetchAndRender(type, schemes, inclText) {{
  const container = document.getElementById('graph-container');
  const spinner   = document.getElementById('graph-spinner');
  const cyDiv     = document.getElementById('cy');
  const resetBtn  = document.getElementById('graph-reset');
  const title     = document.getElementById('graph-title');

  container.classList.add('visible');
  spinner.style.display = 'block';
  cyDiv.style.visibility = 'hidden';
  resetBtn.style.display = 'none';
  document.getElementById('node-tip').style.display = 'none';

  const labels = schemes.map(s => _schemeLabels[s] || s).join(', ');
  title.textContent = (type === 'ontology' ? 'Ontology' : 'Thesaurus') + ': ' + labels;

  const url = '/explore/graph-data?type=' + encodeURIComponent(type)
            + '&schemes=' + encodeURIComponent(schemes.join(','));

  fetch(url)
    .then(r => r.json())
    .then(data => {{
      spinner.style.display = 'none';
      cyDiv.style.visibility = 'visible';
      _renderCytoscape(data, cyDiv, inclText);
      resetBtn.style.display = 'inline-flex';
    }})
    .catch(err => {{
      spinner.style.display = 'none';
      cyDiv.style.visibility = 'visible';
      cyDiv.textContent = 'Error loading graph: ' + err;
    }});
}}

function _renderCytoscape(data, container, inclText) {{
  if (_cy) {{ _cy.destroy(); _cy = null; }}

  const large = data.nodes.length > 50;
  if (large) {{
    const spin = document.getElementById('graph-spinner');
    spin.style.display = 'block';
    spin.textContent = 'Laying out ' + data.nodes.length + ' nodes…';
  }}

  _cy = cytoscape({{
    container: container,
    elements: {{
      nodes: data.nodes.map(n => ({{ data: {{ id: n.id, label: n.label, type: n.type, href: n.href, comment: n.comment || '' }} }})),
      edges: data.edges.map(e => ({{ data: {{ source: e.source, target: e.target }} }}))
    }},
    style: [
      {{ selector: 'node',
         style: {{ shape: 'ellipse', 'background-color': '#40E0D0',
                  label: 'data(label)', 'font-size': '10px', color: '#000',
                  'text-valign': 'center', 'text-halign': 'center',
                  'text-wrap': 'wrap', 'text-max-width': '80px' }} }},
      {{ selector: 'node[type="external"]',
         style: {{ 'background-color': '#FFA500' }} }},
      {{ selector: 'edge',
         style: {{ 'line-color': '#aaa', 'target-arrow-color': '#aaa',
                  'target-arrow-shape': 'triangle', 'curve-style': 'bezier',
                  width: 1 }} }}
    ],
    layout: {{ name: 'dagre', rankDir: 'TB', nodeSep: 20, rankSep: 40, animate: false }}
  }});

  _cy.ready(function() {{
    if (large) document.getElementById('graph-spinner').style.display = 'none';
    _cy.fit();
  }});

  _cy.on('tap', 'node', function(e) {{
    const href = e.target.data('href');
    if (href) window.location = href;
  }});

  const tip = document.getElementById('node-tip');
  if (inclText) {{
    _cy.on('mouseover', 'node', function(e) {{
      const comment = e.target.data('comment');
      if (!comment) return;
      tip.textContent = comment;
      tip.style.display = 'block';
    }});
    _cy.on('mousemove', 'node', function(e) {{
      if (tip.style.display === 'none') return;
      tip.style.left = (e.originalEvent.clientX + 14) + 'px';
      tip.style.top  = (e.originalEvent.clientY + 14) + 'px';
    }});
    _cy.on('mouseout', 'node', function() {{
      tip.style.display = 'none';
    }});
  }} else {{
    tip.style.display = 'none';
  }}
}}
</script>"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get(srv: Server) -> str:
    """Build and return the full Explore page HTML."""
    graph = srv.dbm.rdfs
    qs    = QuerySupport(graph)

    ont_schemes = qs.query_concept_schemes(str(THE.forOntology))
    the_schemes = [
        s for s in qs.query_concept_schemes(str(THE.forThesaurus))
        if not any(s["uri"].endswith(sfx) for sfx in _EXCLUDED_SCHEMES)
    ]
    collections = qs.query_collections()
    viz_items   = _load_viz_items(srv, qs)

    page  = head(srv.cfg["host"], bg_color="#faf9f7", server=srv)
    page += _CDN_SCRIPTS + "\n"
    page += _CSS + "\n"
    page += "<h3>Explore</h3>\n"
    page += "<p class='xp-subtitle'>Ontologies, thesauri, collections, and data visualizations</p>\n"
    page += _quick_access(has_gcp=bool(os.environ.get("GCP_BUCKET")))
    page += _graph_section(ont_schemes, the_schemes)
    page += _graph_container()
    page += _collections_section(collections)
    page += _viz_section(viz_items)
    page += _js(ont_schemes, the_schemes)
    page += tail()
    return page
