"""SPARQL prefix declarations and RDFLib namespace objects."""

import re

from rdflib import Graph
from rdflib.namespace import Namespace

FOR_QUERY = """\
prefix dct:    <http://purl.org/dc/terms/>
prefix foaf:   <http://xmlns.com/foaf/0.1/>
prefix owl:    <http://www.w3.org/2002/07/owl#>
prefix rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#>
prefix schema: <https://schema.org/>
prefix skos:   <http://www.w3.org/2004/02/skos/core#>
prefix the:    <http://visualartsdna.org/thesaurus/>
prefix tko:    <http://visualartsdna.org/takeout/>
prefix vad:    <http://visualartsdna.org/model/>
prefix work:   <http://visualartsdna.org/work/>
prefix xs:     <http://www.w3.org/2001/XMLSchema#>
prefix xsd:    <http://www.w3.org/2001/XMLSchema#>
"""

VAD    = Namespace("http://visualartsdna.org/model/")
WORK   = Namespace("http://visualartsdna.org/work/")
THE    = Namespace("http://visualartsdna.org/thesaurus/")
TKO    = Namespace("http://visualartsdna.org/takeout/")
SCHEMA = Namespace("https://schema.org/")

# Maps route namespace name to RDFLib Namespace object
NS_MAP = {
    "work":      WORK,
    "model":     VAD,
    "thesaurus": THE,
}


# Canonical prefix bindings parsed once from FOR_QUERY — the single source
# of truth. Adding or renaming a prefix in FOR_QUERY propagates everywhere
# bind_standard_prefixes() is used. (xs and xsd both map to the XSD
# namespace; RDFLib picks one consistently for serialization — harmless.)
_FOR_QUERY_PREFIXES = {
    m.group(1): m.group(2)
    for m in re.finditer(r"prefix\s+(\w*):\s+<([^>]+)>", FOR_QUERY, re.IGNORECASE)
}


def bind_standard_prefixes(g: Graph, override: bool = False) -> Graph:
    """Bind every prefix from FOR_QUERY onto a graph before serialization.

    Without this, RDFLib auto-generates ns1:, ns2: etc. for unbound
    namespaces. Deriving the set from FOR_QUERY keeps a single source of
    truth across sparql_construct, _serve_graph, and db_mgr.
    """
    for prefix, namespace in _FOR_QUERY_PREFIXES.items():
        g.bind(prefix, namespace, override=override)
    return g
