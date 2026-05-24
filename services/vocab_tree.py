"""Concepts / vocabTree page — port of VocabTree.groovy."""

import json
from urllib.parse import urlparse

from rdf.query_support import QuerySupport, sparql_select
from util.html_template import head, TAIL


def _href(uri: str) -> str:
    return urlparse(uri).path


def _build_dict(qs: QuerySupport) -> list:
    rows = sparql_select(
        qs.graph,
        """SELECT ?s ?b ?l ?d {
            ?s a skos:Concept ;
               rdfs:label ?l ;
               skos:definition ?d .
            OPTIONAL { ?s skos:broader ?b }
        } ORDER BY ?l"""
    )
    return sorted(rows, key=lambda r: r["l"].lower())


def _build_node(uri: str, results_map: dict, children_map: dict) -> dict:
    row = results_map[uri]
    children = [
        _build_node(c, results_map, children_map)
        for c in children_map.get(uri, [])
    ]
    return {
        row["l"]: {
            "uri": _href(uri),
            "definition": row["d"],
            "children": children,
        }
    }


def _convert_to_dictionary(rows: list) -> list:
    results_map = {row["s"]: row for row in rows}
    children_map: dict[str, list] = {}
    for row in rows:
        if row["b"]:
            children_map.setdefault(row["b"], []).append(row["s"])
    root_uris = [row["s"] for row in rows if not row["b"]]
    return [_build_node(uri, results_map, children_map) for uri in root_uris]


def get(srv) -> str:
    cfg = srv.cfg
    host = cfg["host"]

    qs = QuerySupport(srv.dbm.vocab)
    rows = _build_dict(qs)
    tree = _convert_to_dictionary(rows)
    json_output = json.dumps(tree)

    # Inject JSON between two plain-string HTML blocks to avoid f-string
    # escaping issues with CSS braces and JS template literals.
    return head(host, server=srv) + _HTML_PRE + json_output + _HTML_POST + TAIL


# ---------------------------------------------------------------------------
# Plain strings — not f-strings — so CSS {} and JS ${} pass through unchanged.
# ---------------------------------------------------------------------------

_HTML_PRE = """
<!-- TREE MARKUP -->
<div class="dictionary-tree">
  <div class="toolbar">
    <button id="toggleAllBtn">Expand All</button>
    <select id="conceptSelect" class="concept-select">
      <option value="" class="placeholder" selected>concepts</option>
    </select>
  </div>
  <ul id="treeRoot" class="tree"></ul>
</div>

<style>
  .dictionary-tree,
  .dictionary-tree * {
    box-sizing: border-box;
  }

  .dictionary-tree {
    margin: 0 !important;
    padding: 0.75rem 0.75rem 1.5rem 0.75rem;
    background: #eee;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    font-size: 14px;
    line-height: 1.25;
  }

  .dictionary-tree ul,
  .dictionary-tree li {
    margin: 0 !important;
    padding: 0;
    list-style: none;
    background: transparent !important;
    float: none !important;
  }

  .dictionary-tree .toolbar {
    margin-bottom: 0.5rem;
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }

  .dictionary-tree button {
    font-size: 0.8rem;
    padding: 0.25rem 0.55rem;
  }

  .dictionary-tree .concept-select {
    font-size: 0.8rem;
    padding: 0.2rem 0.4rem;
  }

  .dictionary-tree ul.tree {
    width: 100%;
  }

  .dictionary-tree .tree-item {
    display: flex;
    gap: 0.3rem;
    align-items: flex-start;
    padding: 2px 0;
    width: 100%;
  }

  .dictionary-tree .toggle {
    width: 1.2rem;
    height: 1.2rem;
    cursor: pointer;
    flex: 0 0 auto;
    position: relative;
  }

  /* collapsed = pointing right */
  .dictionary-tree .tree-item.collapsed > .toggle::before {
    content: "";
    position: absolute;
    top: 50%;
    left: 2px;
    transform: translateY(-50%);
    width: 0;
    height: 0;
    border-style: solid;
    border-width: 5px 0 5px 7px;
    border-color: transparent transparent transparent #333;
  }

  /* expanded = pointing down */
  .dictionary-tree .tree-item:not(.collapsed) > .toggle::before {
    content: "";
    position: absolute;
    top: 40%;
    left: 2px;
    width: 0;
    height: 0;
    border-style: solid;
    border-width: 7px 5px 0 5px;
    border-color: #333 transparent transparent transparent;
  }

  /* force no arrow on leaf nodes */
  .dictionary-tree .tree-item > .toggle.invisible::before {
    content: "" !important;
    border: none !important;
    width: 0;
    height: 0;
  }

  .dictionary-tree .item-body {
    flex: 1 1 auto;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }

  .dictionary-tree .node-content {
    display: flex;
    align-items: baseline;
    gap: 0.35rem;
    flex-wrap: wrap;
    width: 100%;
  }

  .dictionary-tree .node-title a {
    text-decoration: none;
    color: steelblue;
    font-weight: 600;
    white-space: nowrap;
  }

  .dictionary-tree .node-definition {
    flex: 1 1 auto;
  }

  .dictionary-tree ul.children {
    margin: 0.05rem 0 0 0;
    padding-left: 0.9rem;
    border-left: 1px solid #d9d9d9;
  }

  .dictionary-tree .collapsed > .item-body > ul.children {
    display: none;
  }

  .dictionary-tree .highlight {
    background: #fff7c2;
    border-radius: 3px;
    padding: 0 2px;
  }
</style>

<script>
document.addEventListener('DOMContentLoaded', function () {
  const orderedData = """

_HTML_POST = """;

  const treeRoot = document.getElementById('treeRoot');
  const selectEl = document.getElementById('conceptSelect');
  const toggleAllBtn = document.getElementById('toggleAllBtn');

  const termIndex = new Map();
  let expandedAll = false;

  function buildNode(key, dataObj) {
    const li = document.createElement('li');
    const hasChildren = Array.isArray(dataObj.children) && dataObj.children.length > 0;
    li.classList.add('tree-item');
    if (hasChildren) li.classList.add('collapsed');

    const toggle = document.createElement('span');
    toggle.className = 'toggle';
    if (!hasChildren) toggle.classList.add('invisible');
    li.appendChild(toggle);

    const body = document.createElement('div');
    body.className = 'item-body';

    const content = document.createElement('div');
    content.className = 'node-content';

    const title = document.createElement('span');
    title.className = 'node-title';
    const a = document.createElement('a');
    a.href = dataObj.uri || '#';
    a.textContent = key;
    title.appendChild(a);

    const colon = document.createElement('span');
    colon.textContent = ':';

    const def = document.createElement('span');
    def.className = 'node-definition';
    def.textContent = dataObj.definition || '';

    content.append(title, colon, def);
    body.appendChild(content);

    termIndex.set(key, { li, content });

    if (hasChildren) {
      const ul = document.createElement('ul');
      ul.className = 'children';
      dataObj.children.forEach(childObj => {
        const childKey = Object.keys(childObj)[0];
        ul.appendChild(buildNode(childKey, childObj[childKey]));
      });
      body.appendChild(ul);
    }

    li.appendChild(body);

    if (hasChildren) {
      const toggleHandler = () => li.classList.toggle('collapsed');
      toggle.addEventListener('click', toggleHandler);
      content.addEventListener('click', e => {
        if (e.target.tagName.toLowerCase() === 'a') return;
        toggleHandler();
      });
    }

    return li;
  }

  function buildTree(container, dataArr) {
    dataArr.forEach(obj => {
      const key = Object.keys(obj)[0];
      container.appendChild(buildNode(key, obj[key]));
    });
  }

  function setAll(expand) {
    document.querySelectorAll('.dictionary-tree .tree-item').forEach(item => {
      if (!item.querySelector(':scope > .item-body > ul.children')) return;
      item.classList.toggle('collapsed', !expand);
    });
  }

  function expandTo(li) {
    let current = li;
    while (current && current !== treeRoot) {
      if (current.classList && current.classList.contains('tree-item')) {
        current.classList.remove('collapsed');
      }
      current = current.parentElement;
      while (current && current !== treeRoot && current.tagName !== 'LI') {
        current = current.parentElement;
      }
    }
  }

  function clearHighlight() {
    document.querySelectorAll('.dictionary-tree .highlight')
      .forEach(el => el.classList.remove('highlight'));
  }

  buildTree(treeRoot, orderedData);

  const sortedKeys = Array.from(termIndex.keys()).sort((a, b) =>
    a.localeCompare(b, undefined, { sensitivity: 'base' })
  );
  sortedKeys.forEach(key => {
    const opt = document.createElement('option');
    opt.value = key;
    opt.textContent = key;
    selectEl.appendChild(opt);
  });

  toggleAllBtn.addEventListener('click', () => {
    expandedAll = !expandedAll;
    setAll(expandedAll);
    toggleAllBtn.textContent = expandedAll ? 'Collapse All' : 'Expand All';
  });

  selectEl.addEventListener('change', () => {
    const term = selectEl.value;
    if (!term) return;
    const entry = termIndex.get(term);
    if (!entry) return;
    clearHighlight();
    expandTo(entry.li);
    entry.content.classList.add('highlight');
    entry.li.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
});
</script>
"""
