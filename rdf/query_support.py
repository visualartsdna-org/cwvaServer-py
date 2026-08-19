"""SPARQL query helpers — port of QuerySupport.groovy.

All queries prepend FOR_QUERY prefixes and target the rdfs store.
"""

from rdflib import Graph, URIRef

from rdf.prefixes import FOR_QUERY, NS_MAP, bind_standard_prefixes


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

# Characters that terminate or corrupt an IRI inside <...> in a SPARQL query.
# The detail routes interpolate a path segment straight into a CONSTRUCT, so an
# identifier carrying any of these produced a pyparsing ParseException that
# escaped as a 500.  Production logs showed this firing on a tab-prefixed but
# otherwise valid GUID (`/work/%09<guid>`), which reads as a normal request.
_ILLEGAL_IRI_CHARS = set(' \t\n\r\f\v<>"{}|^`\\')


def _valid_local_name(name: str) -> bool:
    """True when name is safe to interpolate into <namespace + name>."""
    if not name:
        return False
    return not any(c in _ILLEGAL_IRI_CHARS or ord(c) < 0x20 for c in name)


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
    # Bind the canonical prefixes (from FOR_QUERY) so the result serializes
    # with vad:/the:/work:/... instead of auto-generated ns1:/ns2:.
    bind_standard_prefixes(g)
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
        if not _valid_local_name(guid):
            raise ValueError(f"Invalid identifier: {guid!r}")
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

    def query_object_properties(self, pred_list: list) -> set:
        """Return the subset of pred_list declared owl:ObjectProperty.

        One query for every predicate on a page.  Note the ontology's "rdfs
        hardening" block (schemaSupplement.ttl) types the structural RDF/RDFS/
        SKOS predicates as owl:ObjectProperty too, so this set is wider than the
        vad: domain properties alone.
        """
        if not pred_list:
            return set()
        values = " ".join(f"<{p}>" for p in pred_list)
        rows = sparql_select(
            self.graph,
            f"""SELECT DISTINCT ?p WHERE {{
                VALUES ?p {{ {values} }}
                ?p a owl:ObjectProperty .
            }}"""
        )
        return {row["p"] for row in rows}

    def query_labels(self, uri_list: list) -> dict:
        """Return {uri: label} for each URI in uri_list, ordered alphabetically.

        Every URI in uri_list yields exactly one row: the OPTIONALs leave the
        label unbound when absent and COALESCE falls back to the URI string, so
        an unlabeled member is never dropped from the result.  Callers that
        display the label should substitute a short form when it comes back
        equal to the URI.

        Ordering is done here in SPARQL rather than by the caller.  The returned
        dict preserves that order (insertion order) and de-duplicates any URI
        carrying more than one label.

        Three RDFLib-specific constructions here, none of them incidental:

        1. VALUES is wrapped in a subselect.  A bare `VALUES ?uri {...}`
           followed by OPTIONAL is evaluated by RDFLib as an inner join, not a
           left join, which silently drops every URI with no matching triple.
           Wrapping VALUES in a subselect restores left-join semantics.
        2. One OPTIONAL containing a UNION, not two sequential OPTIONALs.  Two
           OPTIONALs lose the second binding, dropping URIs that carry only
           skos:prefLabel.
        3. COALESCE is bound in the WHERE clause rather than aliased in SELECT.
           ORDER BY precedes projection in the SPARQL evaluation sequence, so a
           SELECT-clause alias is not reliably visible to ORDER BY.  LCASE gives
           alphabetical order rather than codepoint order (which would sort all
           uppercase ahead of all lowercase).
        """
        if not uri_list:
            return {}
        values = " ".join(f"<{u}>" for u in uri_list)
        rows = sparql_select(
            self.graph,
            f"""SELECT ?uri ?label WHERE {{
                {{ SELECT ?uri WHERE {{ VALUES ?uri {{ {values} }} }} }}
                OPTIONAL {{
                    {{ ?uri rdfs:label ?l0 }} UNION {{ ?uri skos:prefLabel ?l0 }}
                }}
                BIND(COALESCE(?l0, STR(?uri)) AS ?label)
            }} ORDER BY LCASE(?label)"""
        )
        return {row["uri"]: row["label"] for row in rows}

    # Collection members use the same batched, alphabetically ordered lookup.
    query_collection = query_labels

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

    def query_backgrounds(self, work_uri: str) -> list:
        """Return [{uri, label}] for backgrounds available for work_uri.

        Traverses: work ← the:tag ← series → the:tag → collection
        where collection has the:topic = the:Background and the:tag → image.
        """
        return sparql_select(
            self.graph,
            f"""SELECT ?uri ?label WHERE {{
                ?series the:tag <{work_uri}> ;
                        the:tag ?col .
                ?col a the:Collection ;
                     the:topic the:Background ;
                     the:tag ?uri .
                ?uri a the:Image ;
                     rdfs:label ?label .
            }} ORDER BY ?label"""
        )
