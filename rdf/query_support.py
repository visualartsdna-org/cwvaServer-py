"""SPARQL query helpers — port of QuerySupport.groovy.

All queries prepend FOR_QUERY prefixes and target the rdfs store.
"""

from rdflib import Graph, URIRef

from rdf.prefixes import FOR_QUERY, NS_MAP


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def sparql_select(graph: Graph, sparql: str) -> list:
    """Execute a SELECT query; return list of {varname: value_string} dicts."""
    results = graph.query(FOR_QUERY + sparql)
    rows = []
    for row in results:
        rows.append({
            str(var): (str(val) if val is not None else "")
            for var, val in zip(results.vars, row)
        })
    return rows


def sparql_construct(graph: Graph, sparql: str) -> Graph:
    """Execute a CONSTRUCT query; return result as a new Graph."""
    result = graph.query(FOR_QUERY + sparql)
    g = Graph()
    for triple in result:
        g.add(triple)
    return g


# ---------------------------------------------------------------------------
# QuerySupport class
# ---------------------------------------------------------------------------

class QuerySupport:
    """Wraps a graph with all application-level SPARQL queries."""

    def __init__(self, graph: Graph):
        self.graph = graph

    def _build_uri(self, ns: str, guid: str) -> URIRef:
        namespace = NS_MAP.get(ns)
        if namespace is None:
            raise ValueError(f"Unknown namespace: {ns!r}")
        return URIRef(str(namespace) + guid)

    def query(self, ns: str, guid: str) -> dict:
        """Main browser-page query.

        Returns:
            {
              f"{ns}:{guid}": rdflib.Graph  — all direct triples + blank-node properties
              "label":        str            — rdfs:label value
              "Tags":         rdflib.Graph   — all triples for linked the:tag resources
            }
        """
        uri = self._build_uri(ns, guid)
        uri_str = str(uri)

        # CONSTRUCT all direct triples plus blank-node (promoteBNData) properties
        main_graph = sparql_construct(
            self.graph,
            f"""CONSTRUCT {{
                <{uri_str}> ?p ?o .
                ?o ?bp ?bo .
            }} WHERE {{
                <{uri_str}> ?p ?o .
                OPTIONAL {{
                    FILTER(isBlank(?o))
                    ?o ?bp ?bo .
                }}
            }}"""
        )

        # rdfs:label or skos:prefLabel
        label = self.query_label(uri_str)

        # CONSTRUCT full descriptions of all linked tags
        tags_graph = sparql_construct(
            self.graph,
            f"""CONSTRUCT {{ ?tag ?p ?o }}
            WHERE {{
                <{uri_str}> the:tag ?tag .
                ?tag ?p ?o .
            }}"""
        )

        return {
            f"{ns}:{guid}": main_graph,
            "label": label,
            "Tags": tags_graph,
        }

    def query_one_property(self, inst: str, prop: str) -> str:
        """Return the first value of prop for inst as a string."""
        rows = sparql_select(
            self.graph,
            f"SELECT ?v WHERE {{ <{inst}> <{prop}> ?v }} LIMIT 1"
        )
        return rows[0]["v"] if rows else ""

    def query_label(self, uri_str: str) -> str:
        """Return rdfs:label or skos:prefLabel for uri_str (whichever exists)."""
        rows = sparql_select(
            self.graph,
            f"""SELECT ?label WHERE {{
                {{ <{uri_str}> rdfs:label ?label }}
                UNION
                {{ <{uri_str}> skos:prefLabel ?label }}
            }} LIMIT 1"""
        )
        return rows[0]["label"] if rows else ""

    def query_collection(self, uri_list: list) -> dict:
        """Return {uri: label} for each URI in uri_list."""
        if not uri_list:
            return {}
        values = " ".join(f"<{u}>" for u in uri_list)
        rows = sparql_select(
            self.graph,
            f"""SELECT ?uri ?label WHERE {{
                VALUES ?uri {{ {values} }}
                {{ ?uri rdfs:label ?label }} UNION {{ ?uri skos:prefLabel ?label }}
            }}"""
        )
        return {row["uri"]: row["label"] for row in rows}

    def get_one_instance_model(self, ns: str, guid: str) -> Graph:
        """Return the RDF description of an entity for format-negotiated endpoints."""
        uri = self._build_uri(ns, guid)
        uri_str = str(uri)
        return sparql_construct(
            self.graph,
            f"""CONSTRUCT {{
                <{uri_str}> ?p ?o .
                ?o ?bp ?bo .
            }} WHERE {{
                <{uri_str}> ?p ?o .
                OPTIONAL {{
                    FILTER(isBlank(?o))
                    ?o ?bp ?bo .
                }}
            }}"""
        )

    def query_tags(self, uri_str: str) -> list:
        """Return [{c, l, d}] for all concepts tagging uri_str, ordered by label.

        Mirrors the Groovy Tags CONSTRUCT: direct the:tag links plus tags
        inherited from any skos:Collection the work belongs to.
        """
        return sparql_select(
            self.graph,
            f"""SELECT DISTINCT ?c ?l ?d WHERE {{
                BIND(<{uri_str}> AS ?s)
                {{
                    ?s the:tag ?c .
                    {{ ?c rdfs:label ?l }} UNION {{ ?c skos:prefLabel ?l }}
                    {{ ?c skos:definition ?d }} UNION {{ ?c schema:description ?d }}
                }} UNION {{
                    ?col skos:member ?s .
                    ?col the:tag ?c .
                    ?c a skos:Concept ;
                       skos:definition ?d .
                    {{ ?c rdfs:label ?l }} UNION {{ ?c skos:prefLabel ?l }}
                }}
            }} ORDER BY ?l"""
        )

    def query_concept_schemes(self, scheme_type: str = "") -> list:
        """Return concept schemes, optionally filtered by the:tag value."""
        if scheme_type:
            return sparql_select(
                self.graph,
                f"""SELECT ?uri ?label WHERE {{
                    ?uri a skos:ConceptScheme ;
                         rdfs:label ?label ;
                         the:tag <{scheme_type}> .
                }} ORDER BY ?label"""
            )
        return sparql_select(
            self.graph,
            """SELECT ?uri ?label WHERE {
                ?uri a skos:ConceptScheme ;
                     rdfs:label ?label .
            } ORDER BY ?label"""
        )

    def query_collections(self) -> list:
        """Return all SKOS collections with their prefLabels."""
        return sparql_select(
            self.graph,
            """SELECT ?uri ?label WHERE {
                ?uri a skos:Collection ;
                     skos:prefLabel ?label .
            } ORDER BY ?label"""
        )
