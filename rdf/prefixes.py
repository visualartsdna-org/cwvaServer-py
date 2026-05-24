"""SPARQL prefix declarations and RDFLib namespace objects."""

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
