"""About page — port of About.groovy."""

from util.html_template import head, TAIL


def get(srv) -> str:
    cfg = srv.cfg
    host = cfg["host"]

    return head(host, server=srv) + f"""
<style>
p {{
  margin-left: 4%;
  font-size: 18px;
}}
ul {{
  font-size: 18px;
}}
table {{
  margin-left: 10%;
}}
</style>
<h2>About VisualArtsDNA</h2>
<p/>
<b>VisualArtsDNA</b> began as a simple question: <i>How can the relationships within a body of artwork be made visible, explicit, and shareable?</i>
What started as a personal experiment in organizing creative work has grown into a comprehensive information model for the visual arts, supported by modern semantic-web technologies.

<h3>Purpose and Vision</h3>
<p/>
The project explores how artworks, materials, processes, themes, and artistic intentions connect to one another. By structuring this information, VisualArtsDNA provides a framework for documenting creative practice, analyzing patterns, and supporting discovery. While the home page foregrounds the artworks themselves, this site also serves as the public home for the underlying model that organizes them.

<h3>Information Model and Ontology</h3>
<p/>
At the core of VisualArtsDNA is an ontology designed to represent the domain of visual arts with precision and flexibility.
Key features include:
<ul>
<li>
<b>OWL (Web Ontology Language):</b> used to define classes, properties, and logical constraints that describe the structure of artistic information.
</li>
<li>
<b>RDF (Resource Description Framework):</b> the data foundation that expresses artworks and their attributes as subject–predicate–object triples.
</li>
<li>
<b>Custom vocabularies and controlled terms:</b> developed to cover materials, techniques, genres, media, compositional features, and other concepts relevant to visual art.
</li>
<li>
<b>Interoperability with existing standards:</b> including SKOS for concept hierarchies and Dublin Core for metadata, ensuring compatibility with broader digital-heritage ecosystems.
</li>
</ul>
<h3>What the Model Represents</h3>
<p/>
The ontology includes:

<ul>
<li>
Artworks and their creators
</li>
<li>
Materials, media, and fabrication processes
</li>
<li>
Compositional structures and visual characteristics
</li>
<li>
Places, times, and contextual information
</li>
<li>
Themes, concepts, and relationships among works
</li>
<li>
Supporting documentation such as reference images, notes, and provenance
</li>
</ul>
<p/>
This structure allows the system to reflect not just what a work is, but how it relates to other works, influences, and artistic choices.

<h3>Instances and Knowledge Graph</h3>
<p/>
Every artwork displayed on the site is represented internally as an RDF instance. Together, these instances form a small but growing <b>knowledge graph</b> of creative works.
This graph enables:
<ul>
<li>
Querying artworks by shared properties
</li>
<li>
Exploring conceptual relationships
</li>
<li>
Tracing recurring motifs and techniques
</li>
<li>
Generating structured summaries and metadata automatically
</li>
</ul>
<h3>Ongoing Development</h3>
<p/>
VisualArtsDNA is an evolving project. The ontology continues to expand as new artworks, concepts, and relationships are added. Future development will refine vocabularies, increase interoperability with other art-information systems, and continue improving the visitor experience by balancing visual presentation with structured knowledge.
<p/>
<p/>
<p/>
<h3>For More Information...</h3>
<p/>
<a href="https://w3id.org/lode/owlapi/https://visualartsdna.org/model/">Ontology Documentation (via LODE server)</a>
<p/>
The ontology is available in
an
<a href="/model">RDF file (text/turtle)</a>.
<p/>
See the <a href="https://archivo.dbpedia.org/info?o=http://visualartsdna.org/model/">ontology on DBpedia Archivo</a>.
[Note: check <a href="https://archivo.dbpedia.org/">DBpedia Archivo</a> for site availability.]
<p/>
A thesaurus of visual arts terms is available in
an
<a href="/vocab">RDF file (text/turtle)</a>.
<p/>
<a href="/html/references.html">References</a>
<p/>
<a href="/thesaurus/OperationalCollection">System Documentation</a>
<p/>
<p/>
<p/>
""" + TAIL
