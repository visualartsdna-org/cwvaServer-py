"""HTML page head/tail/nav — port of HtmlTemplate.groovy."""

import datetime
from server import VERSION, Server


def _current_year() -> int:
    return datetime.datetime.now().year


def head(host: str, bg_color: str = "#FFFFFF", *, server=None) -> str:
    """Return full HTML <head> block + open <body> + navigation bar."""

    ask_link = ""
    if server is not None and server.cfg.get("agentUrl"):
        ask_link = '    <li><a class="top-nav__item" href="/agentClient">Ask</a></li>\n'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="An ontology for the visual arts. VisualArtsDNA organizes the details of the visual arts creative process into an information model expressed in OWL.">
<meta name="keywords" content="RDF,OWL,painting,sculpture,drawing,printmaking,ontology,model">
<meta property="og:title" content="VisualArtsDNA">
<title>VisualArtsDNA</title>
<link rel="icon" href="/images/dblHelix.png">
<link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Krub">
<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-0GRY55G849"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-0GRY55G849');
</script>
<style>
body {{ background-color: {bg_color}; font-family: 'Krub'; font-size: 22px; }}
tr:nth-child(even) {{ background-color: #f8f8f8; }}
a:link    {{ color: steelblue;      text-decoration: none; }}
a:visited {{ color: cornflowerblue; text-decoration: none; }}
a:hover   {{ color: navy;           text-decoration: underline; }}
a:active  {{ color: blue;           text-decoration: underline; }}
.top-nav {{ background-color: #eee; font-family: 'Krub', sans-serif; }}
.top-nav__list {{ list-style:none; margin:0; padding:0; display:flex;
  gap:1.5rem; align-items:center; height:3.2rem; }}
.top-nav__item a {{ display:block; font-size:22px; color:steelblue;
  text-decoration:none; padding:0.5rem 0; }}
.top-nav__item a:hover {{ color:navy; text-decoration:underline; }}
</style>
</head>
<body>
<small>
<nav class="top-nav">
  <ul class="top-nav__list">
    <li><a class="top-nav__item" href="/">Home</a></li>
{ask_link}    <li><a class="top-nav__item" href="/vocabTree">Concepts</a></li>
    <li><a class="top-nav__item" href="/explore">Explore</a></li>
    <li><a class="top-nav__item" href="/about">About</a></li>
  </ul>
</nav>
</small>
"""


def get_sparql(host: str, cfg: dict) -> str:
    """Return SPARQL nav link if cfg.sparql is true, else empty string."""
    if cfg.get("sparql"):
        return '<li><a href="/sparql">SPARQL</a></li>\n'
    return ""


def title(uri_long: str, uri_short: str) -> str:
    return f'<h3 id="title">About: <a href="{uri_long}">{uri_short}</a></h3>\n'


def table_head(header1: str, header2: str = None) -> str:
    if header2 is not None:
        return (
            f'<div><table><tbody>\n'
            f'<tr height="50">\n'
            f'  <th class="col-xs-3">{header1}</th>\n'
            f'  <th class="col-xs-3">{header2}</th>\n'
            f'</tr>\n'
        )
    return (
        f'<div><table><tbody>\n'
        f'<tr height="50">\n'
        f'  <th class="col-xs-3">{header1}</th>\n'
        f'</tr>\n'
    )


TABLE_TAIL = "</tbody></table></div>\n"


def tail() -> str:
    """Return page footer HTML. Reads contactEmail and copyrightName from server config if available."""
    srv = Server.get_instance()
    cfg = srv.cfg if srv else {}
    email     = cfg.get("contactEmail",  "visualartsdna@gmail.com")
    copyright_name = cfg.get("copyrightName", "visualartsdna.org")
    return f"""<center><font size="2" color="#666666">
<br/><hr/><br/>
<a href="mailto:{email}">{email}</a><br/>
Copyright &copy; {_current_year()} {copyright_name}. All Rights Reserved.<br/>
v {VERSION}
</font></center>
</body></html>
"""
