"""3D model viewer page — port of ModelViewer.groovy."""

import html as html_mod
from urllib.parse import urlparse

from rdf.prefixes import NS_MAP, VAD, SCHEMA
from rdf.query_support import QuerySupport, sparql_select
from server import Server
from util.html_template import head, tail


def _remove_protocol(url: str) -> str:
    return (url.replace("https://", "").replace("http://", "")
               .replace("www.", "").rstrip("/"))


def _work_uri(work_curi: str) -> str:
    if work_curi.startswith("work:"):
        return str(NS_MAP["work"]) + work_curi[len("work:"):]
    return work_curi


def _root_rel(uri: str) -> str:
    """Return root-relative path for internal URIs, unchanged for external ones."""
    if uri and "visualartsdna.org" in uri:
        return urlparse(Server.rehost(uri)).path
    return uri


def get(srv: Server, work_curi: str, bkg: str = "", is_mobile: bool = False) -> str:
    """Build and return the full HTML for the 3D model viewer page."""
    graph = srv.dbm.rdfs
    qs = QuerySupport(graph)
    host = srv.cfg.get("host", "")

    work_uri = _work_uri(work_curi)

    label = qs.query_label(work_uri)
    descr = qs.query_one_property(work_uri, str(SCHEMA.description))
    model_uri = qs.query_one_property(work_uri, str(VAD.image3d))
    bkg_uri = qs.query_one_property(work_uri, str(VAD.background))

    # bkg param overrides the work's default background
    selected = bkg if bkg else bkg_uri
    bkg_image = qs.query_one_property(selected, str(SCHEMA.image)) if selected else ""

    artist_rows = sparql_select(
        graph,
        f"""SELECT ?site WHERE {{
            <{work_uri}> vad:hasArtistProfile/vad:artist/foaf:homepage ?site .
        }} LIMIT 1"""
    )
    site = artist_rows[0]["site"] if artist_rows else ""
    site_name = _remove_protocol(site) if site else ""

    backgrounds = qs.query_backgrounds(work_uri)

    model_path = _root_rel(model_uri) if model_uri else ""
    bkg_path   = _root_rel(bkg_image) if bkg_image else ""
    work_path  = _root_rel(work_uri) if work_uri else "/"

    # Background dropdown
    options = []
    for bg in sorted(backgrounds, key=lambda x: x["label"]):
        sel = "selected" if bg["uri"] == selected else ""
        options.append(
            f'<option value="{html_mod.escape(bg["uri"])}" {sel}>'
            f'{html_mod.escape(bg["label"])}</option>'
        )
    option_str = "\n          ".join(options)

    viewer_w      = "100%" if is_mobile else "680px"
    viewer_h      = "360px" if is_mobile else "520px"
    pad_gallery   = "12px" if is_mobile else "32px 24px 48px"
    pad_desc      = "16px" if is_mobile else "20px 24px"
    title_size    = "20px" if is_mobile else "22px"
    is_mobile_js  = "true" if is_mobile else "false"
    e = html_mod.escape

    bg_attrs = ""
    if bkg_path:
        bg_attrs = (f'\n          environment-image="{bkg_path}"'
                    f'\n          skybox-image="{bkg_path}"')

    mouse_hints = ""
    if not is_mobile:
        mouse_hints = """
      <div class="mouse-hints">
        <span class="hint-item"><img src="/images/left-click.png" width="18" height="18">drag</span>
        <span class="hint-item"><img src="/images/right-click.png" width="18" height="18">pan</span>
        <span class="hint-item"><img src="/images/scroll.png" width="18" height="18">zoom</span>
      </div>"""

    artist_html = ""
    if site:
        artist_html = f'<div class="artist-link">Visit <a href="{e(site)}">{e(site_name)}</a></div>'

    html = head(host, bg_color="#18181b", server=srv)
    html += f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300;1,400&family=Jost:wght@300;400;500&display=swap" rel="stylesheet">

<style>
  :root {{
    --bg:        #18181b;
    --surface:   #1f1f23;
    --surface2:  #27272c;
    --border:    #3a3a42;
    --gold:      #c9a84c;
    --gold-dim:  #8a6e30;
    --text:      #e8e6e0;
    --text-dim:  #9a9893;
    --teal:      #2dd4bf;
    --teal-dark: #0f766e;
    --radius:    12px;
    --viewer-w:  {viewer_w};
    --viewer-h:  {viewer_h};
  }}

  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Jost', sans-serif;
    font-weight: 300;
    min-height: 100vh;
  }}

  /* Nav override for dark background */
  .top-nav {{ background-color: var(--surface2); }}
  .top-nav__item a {{ color: var(--text-dim) !important; }}
  .top-nav__item a:hover {{ color: var(--text) !important; text-decoration: none; }}
  a:link    {{ color: var(--teal); }}
  a:visited {{ color: var(--teal-dark); }}
  a:hover   {{ color: var(--teal); }}

  .gallery-page {{
    max-width: 860px;
    margin: 0 auto;
    padding: {pad_gallery};
  }}

  .viewer-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    box-shadow: 0 8px 40px rgba(0,0,0,0.55), 0 1px 0 rgba(255,255,255,0.04) inset;
    position: relative;
    transition: box-shadow 0.3s ease;
  }}

  .viewer-card:hover {{
    box-shadow: 0 12px 56px rgba(0,0,0,0.7), 0 1px 0 rgba(255,255,255,0.06) inset;
  }}

  .mvDiv {{
    width: var(--viewer-w);
    height: var(--viewer-h);
    resize: both;
    overflow: auto;
    position: relative;
    background: #111113;
  }}

  .mvDiv.is-fullscreen {{
    position: fixed !important;
    inset: 0 !important;
    width: 100vw !important;
    height: 100vh !important;
    z-index: 9999;
    resize: none !important;
    border-radius: 0 !important;
  }}

  model-viewer {{
    width: 100%;
    height: 100%;
    position: relative;
  }}

  .logo-overlay {{
    position: absolute;
    bottom: 12px;
    left: 14px;
    z-index: 10;
    pointer-events: none;
  }}

  .logo-overlay p {{
    color: rgba(255,255,255,0.55);
    font-family: 'Jost', sans-serif;
    font-size: 11px;
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    text-shadow: 0 1px 4px rgba(0,0,0,0.8);
  }}

  .controls-bar {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    background: var(--surface2);
    border-top: 1px solid var(--border);
    flex-wrap: wrap;
  }}

  .bkgnd-select {{
    appearance: none;
    background: var(--surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%239a9893'/%3E%3C/svg%3E") no-repeat right 10px center;
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-family: 'Jost', sans-serif;
    font-size: 13px;
    font-weight: 300;
    padding: 5px 30px 5px 10px;
    cursor: pointer;
    transition: border-color 0.2s;
    min-width: 140px;
  }}

  .bkgnd-select:hover, .bkgnd-select:focus {{
    border-color: var(--gold-dim);
    outline: none;
  }}

  .btn {{
    border: none;
    border-radius: 20px;
    cursor: pointer;
    font-family: 'Jost', sans-serif;
    font-size: 12px;
    font-weight: 400;
    letter-spacing: 0.05em;
    padding: 5px 14px;
    transition: background 0.2s, transform 0.1s;
    white-space: nowrap;
  }}

  .btn:active {{ transform: scale(0.96); }}

  .btn-rotation {{
    background: var(--teal-dark);
    color: #e0faf7;
  }}
  .btn-rotation:hover {{ background: #0d6660; }}
  .btn-rotation.off {{
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text-dim);
  }}
  .btn-rotation.off:hover {{ border-color: var(--text-dim); }}

  .btn-expand {{
    background: #4a4a54;
    border: 1px solid #66666f;
    color: var(--text);
  }}
  .btn-expand:hover {{
    background: #57575f;
    border-color: var(--gold-dim);
    color: var(--gold);
  }}

  .mouse-hints {{
    display: flex;
    align-items: center;
    gap: 6px;
    margin-left: auto;
    color: var(--text-dim);
    font-size: 11px;
    letter-spacing: 0.04em;
  }}

  .mouse-hints img {{
    display: inline-block;
    opacity: 0.85;
    filter: invert(1) brightness(1.8);
    vertical-align: middle;
  }}

  .hint-item {{ display: flex; align-items: center; gap: 3px; }}

  .desc-card {{
    margin-top: 18px;
    padding: {pad_desc};
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    border-left: 3px solid var(--gold-dim);
  }}

  .desc-card .work-title {{
    font-family: 'Cormorant Garamond', serif;
    font-size: {title_size};
    font-weight: 400;
    color: var(--gold);
    letter-spacing: 0.02em;
    margin-bottom: 6px;
  }}

  .desc-card .work-title a {{
    color: inherit;
    text-decoration: none;
    border-bottom: 1px solid var(--gold-dim);
    transition: color 0.2s, border-color 0.2s;
  }}

  .desc-card .work-title a:hover {{
    color: #e8c878;
    border-color: #e8c878;
  }}

  .desc-card .work-descr {{
    font-size: 14px;
    color: var(--text-dim);
    line-height: 1.65;
    margin-bottom: 10px;
  }}

  .desc-card .artist-link a {{
    color: var(--teal);
    font-size: 13px;
    text-decoration: none;
    letter-spacing: 0.03em;
    border-bottom: 1px solid transparent;
    transition: border-color 0.2s;
  }}

  .desc-card .artist-link a:hover {{
    border-color: var(--teal);
  }}

  .esc-hint {{
    display: none;
    position: fixed;
    top: 14px;
    right: 18px;
    z-index: 10001;
    background: rgba(0,0,0,0.75);
    color: rgba(255,255,255,0.9);
    font-family: 'Jost', sans-serif;
    font-size: 13px;
    font-weight: 400;
    padding: 6px 16px;
    border-radius: 20px;
    border: 1px solid rgba(255,255,255,0.25);
    opacity: 0;
    transition: opacity 0.8s ease, background 0.2s;
  }}
  .esc-hint.mobile {{ cursor: pointer; pointer-events: all; }}
  .esc-hint.mobile:hover {{ background: rgba(50,50,50,0.92); }}
  .esc-hint.desktop {{ cursor: default; pointer-events: none; }}
  .esc-hint.visible {{ display: block; opacity: 1; }}
  .esc-hint.fading  {{ display: block; opacity: 0; }}
</style>

<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.0.0/model-viewer.min.js"></script>

<div class="gallery-page">
  <div class="viewer-card">
    <div class="mvDiv" id="mvDiv">
      <model-viewer
          id="myModelViewer"
          src="{model_path}"
          camera-controls
          tone-mapping="neutral"
          shadow-intensity="0"{bg_attrs}
          auto-rotate
          style="width:100%; height:100%;">
      </model-viewer>
      <div class="logo-overlay"><p>{e(site_name)}</p></div>
    </div>

    <div class="controls-bar">
      <select id="bkg-select" class="bkgnd-select">
        {option_str}
      </select>

      <button id="toggleButton" class="btn btn-rotation" onclick="toggleRotation()">Rotation On</button>
      <button id="expandButton" class="btn btn-expand"   onclick="toggleExpand()">[ ] Expand</button>
      {mouse_hints}
    </div>
  </div>

  <div class="desc-card">
    <div class="work-title"><a href="{work_path}">{e(label)}</a></div>
    <p class="work-descr">{e(descr)}</p>
    {artist_html}
  </div>
</div>

<div class="esc-hint" id="escHint"></div>

<script>
  const modelViewer  = document.getElementById('myModelViewer');
  const toggleButton = document.getElementById('toggleButton');
  const expandButton = document.getElementById('expandButton');
  const mvDiv        = document.getElementById('mvDiv');
  const escHint      = document.getElementById('escHint');
  const isTouchDevice = {is_mobile_js};

  function toggleRotation() {{
    const rotating = modelViewer.hasAttribute('auto-rotate');
    if (rotating) {{
      modelViewer.removeAttribute('auto-rotate');
      toggleButton.textContent = 'Rotation Off';
      toggleButton.classList.add('off');
    }} else {{
      modelViewer.setAttribute('auto-rotate', '');
      toggleButton.textContent = 'Rotation On';
      toggleButton.classList.remove('off');
    }}
  }}

  if (isTouchDevice) {{
    escHint.textContent = '[X] Close fullscreen';
    escHint.classList.add('mobile');
    escHint.addEventListener('click', collapse);
  }} else {{
    escHint.textContent = 'Press Esc to close';
    escHint.classList.add('desktop');
  }}

  let savedSize = null;
  let fadeTimer = null;

  function toggleExpand() {{
    const full = mvDiv.classList.contains('is-fullscreen');
    if (!full) {{
      savedSize = {{ w: mvDiv.style.width, h: mvDiv.style.height }};
      mvDiv.classList.add('is-fullscreen');
      expandButton.textContent = '[X] Collapse';
      escHint.classList.remove('fading');
      escHint.classList.add('visible');
      if (!isTouchDevice) {{
        clearTimeout(fadeTimer);
        fadeTimer = setTimeout(() => {{
          escHint.classList.add('fading');
          setTimeout(() => escHint.classList.remove('visible', 'fading'), 800);
        }}, 3000);
      }}
    }} else {{
      collapse();
    }}
  }}

  function collapse() {{
    clearTimeout(fadeTimer);
    mvDiv.classList.remove('is-fullscreen');
    expandButton.textContent = '[ ] Expand';
    escHint.classList.remove('visible', 'fading');
    if (savedSize) {{
      mvDiv.style.width  = savedSize.w;
      mvDiv.style.height = savedSize.h;
    }}
  }}

  document.addEventListener('keydown', e => {{
    if (e.key === 'Escape' && mvDiv.classList.contains('is-fullscreen')) collapse();
  }});

  window.onload = () => {{
    if (!modelViewer.hasAttribute('auto-rotate')) {{
      toggleButton.textContent = 'Rotation Off';
      toggleButton.classList.add('off');
    }}
  }};

  document.getElementById('bkg-select').addEventListener('change', function() {{
    const work = new URLSearchParams(window.location.search).get('work');
    window.location = `/modelviewer?work=${{work}}&bkg=${{this.value}}`;
  }});
</script>
"""
    html += tail()
    return html
