"""About page — port of About.groovy."""

from util.html_template import head, tail


def get(srv) -> str:
    cfg = srv.cfg
    host = cfg["host"]

    return head(host, server=srv) + """
<style>
p {
  margin-left: 4%;
  font-size: 18px;
}
ul {
  font-size: 18px;
}
table {
  margin-left: 10%;
}
</style>
<h2>About VisualArtsDNA</h2>
<p>
<b>VisualArtsDNA</b> began as a simple question: <i>How can the relationships
within a body of artwork be made visible, explicit, and shareable?</i>
What started as a personal experiment in organizing creative work has grown into
a comprehensive information model for the visual arts, supported by modern
semantic-web technologies — and now, an open source platform available to any
artist who wants to host their own catalog.
</p>

<h3>Purpose and Vision</h3>
<p>
The project explores how artworks, materials, processes, themes, and artistic
intentions connect to one another. By structuring this information, VisualArtsDNA
provides a framework for documenting creative practice, analyzing patterns, and
supporting discovery. While the home page foregrounds the artworks themselves,
this site also serves as the public home for the underlying model that organizes them.
</p>
<p>
The structured data has a practical benefit beyond the site itself: search engines
index the semantic relationships, not just the page text. The correct information
about each work — dimensions, materials, date, process — is available to any system
that can read linked data.
</p>

<h3>Information Model and Ontology</h3>
<p>
At the core of VisualArtsDNA is an ontology designed to represent the domain of
visual arts with precision and flexibility. Key features include:
</p>
<ul>
<li><b>OWL (Web Ontology Language):</b> used to define classes, properties, and
logical constraints that describe the structure of artistic information.</li>
<li><b>RDF (Resource Description Framework):</b> the data foundation that expresses
artworks and their attributes as subject–predicate–object triples.</li>
<li><b>Custom vocabularies and controlled terms:</b> developed to cover materials,
techniques, genres, media, compositional features, and other concepts relevant
to visual art.</li>
<li><b>Interoperability with existing standards:</b> including SKOS for concept
hierarchies and Dublin Core for metadata, ensuring compatibility with broader
digital-heritage ecosystems.</li>
</ul>

<h3>What the Model Represents</h3>
<p>The ontology includes:</p>
<ul>
<li>Artworks and their creators</li>
<li>Materials, media, and fabrication processes</li>
<li>Compositional structures and visual characteristics</li>
<li>Places, times, and contextual information</li>
<li>Themes, concepts, and relationships among works</li>
<li>Supporting documentation — reference images, notes, criticism, and provenance</li>
</ul>
<p>
This structure allows the system to reflect not just what a work is, but how it
relates to other works, influences, and artistic choices. Process notes and
criticism are linked to the works they concern, creating a record of artistic
thinking as well as artistic output.
</p>

<h3>Instances and Knowledge Graph</h3>
<p>
Every artwork displayed on the site is represented internally as an RDF instance.
Together, these instances form a growing <b>knowledge graph</b> of creative works.
This graph enables:
</p>
<ul>
<li>Querying artworks by shared properties</li>
<li>Exploring conceptual relationships</li>
<li>Tracing recurring motifs and techniques</li>
<li>Generating structured summaries and metadata automatically</li>
<li>Federating with other artists' catalogs that share the same vocabulary</li>
</ul>

<h3>The Server</h3>
<p>
This site is served by <b>cwvaServer-py</b> — a Python/FastAPI semantic web server
ported from the original Groovy/Jetty implementation. The port was developed
collaboratively with <a href="https://claude.ai">Claude</a> (Anthropic's AI
assistant) using Claude Code, Anthropic's agentic coding tool.
</p>
<p>
cwvaServer-py is open source and available to any artist or collective who wants
to host their own structured catalog of creative works. It runs on a laptop, a
home network, or a free-tier cloud instance. No external database required — data lives in standard text files
that are human-readable, version-controllable, and portable.
No platform dependency.
</p>
<p>
For large-scale deployments, Apache Jena Fuseki + TDB2 provides
a persistent indexed triplestore backend. See DATABASE.md in the
repository for details.
</p>
<ul>
<li><a href="https://github.com/visualartsdna-org/cwvaServer-py">cwvaServer-py on GitHub</a></li>
<li><a href="https://github.com/visualartsdna-org/cwvaMetacontent">cwvaMetacontent on GitHub</a></li>
</ul>

<h3>Ongoing Development</h3>
<p>
VisualArtsDNA is an evolving project. The ontology continues to expand as new
artworks, concepts, and relationships are added. Planned development includes:
</p>
<ul>
<li>An authoring agent to help artists create structured data from natural
language descriptions</li>
<li>Federated gallery support — linking catalogs across independently hosted servers</li>
<li>Community governance of the shared ontology via the cwvaMetacontent repository</li>
<li>AR viewing for 3D works on iOS, Android, and Meta Quest</li>
</ul>
<p>
See the <a href="https://github.com/visualartsdna-org/cwvaServer-py/blob/main/ROADMAP.md">project roadmap</a>
for the full picture.
</p>

<h3>For More Information</h3>
<p/>
<a href="https://w3id.org/lode/owlapi/https://visualartsdna.org/model/">Ontology Documentation</a>
via the LODE server
<p/>
<a href="/model">Ontology RDF file</a> — text/turtle
<p/>
<a href="/vocab">Thesaurus RDF file</a> — text/turtle
<p/>
<a href="https://archivo.dbpedia.org/info?o=http://visualartsdna.org/model/">VisualArtsDNA on DBpedia Archivo</a>
— note: check <a href="https://archivo.dbpedia.org/">DBpedia Archivo</a> for availability
<p/>
<a href="/html/references.html">References</a>
<p/>
<a href="/thesaurus/OperationalCollection">System Documentation</a>
<p/>
<p/>
""" + tail()
