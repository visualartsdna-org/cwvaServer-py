# Data Guide — cwvaServer-py

## Folder Layout

```
~/cwva/
├── main/               # cwvaServer-py code repo
├── metacontent/        # shared ontology (community governed)
│   ├── model/          # OWL ontology TTL files — populated from referenceModel
│   └── vocab/          # SKOS vocabulary TTL files — populated from referenceModel
├── content/            # your data (user governed, git repo)
│   ├── data/           # one TTL file per artwork (named {guid}.ttl)
│   ├── tags/           # tag and note TTL files linked to works
│   ├── documents/      # Markdown and PDF documents referenced by works
│   ├── images/         # work images — excluded from git (see content/.gitignore)
│   └── provenance/     # placeholder for future expansion
└── thumbnails/         # auto-generated resized images — not in any repo
```

`model/` and `vocab/` are populated automatically from `referenceModel`
(visualartsdna.org) on first startup if empty. You only need to manage files
in `data/`, `tags/`, `images/`, and `documents/`.

---

## Sample Data

Sample data is provided to show your newly installed cwvaServer-py in action.

### Unzip

Find the sample data at `cwvaServer-py/sample-data/cwvaContent-sample.zip`.
Unzip it directly into `~/cwva/` — it expands into the `content/` subtree:

```bash
cd ~/cwva
unzip ~/cwva/main/sample-data/cwvaContent-sample.zip
```

This populates:

| Zip path | Destination |
|---|---|
| `content/data/*.ttl` | `~/cwva/content/data/` |
| `content/tags/*.ttl` | `~/cwva/content/tags/` |
| `content/documents/*` | `~/cwva/content/documents/` |
| `content/images/*` | `~/cwva/content/images/` |
| `content/.gitignore` | `~/cwva/content/.gitignore` |
| `content/provenance/` | `~/cwva/content/provenance/` (empty placeholder) |

> **Note on images in the sample zip:** Images are included in the sample zip
> for convenience so the sample artworks display correctly out of the box.
> The sample zip is a bootstrap archive, not a git repo — it has no git history
> and no `.git` folder. Once you initialize a content git repo, images are
> excluded by `content/.gitignore` and are not committed going forward.

Then restart or refresh your server:

```bash
# If the server is not yet running:
python main.py -cfg config/serverCwva.rson

# If the server is already running:
python tools/cwva_cmd.py refresh -H http://localhost -p 8080
```

You should see six artworks on the Home page. Browse each work to see the
kind of information available — title, materials, dimensions, process notes,
artist profile, and image.

---

## Images and Git

Images are excluded from the content git repo by `content/.gitignore`:

```gitignore
images/
thumbnails/
```

This is intentional — image files are typically too large for standard git
hosting. Options for managing images:

| Option | Best for |
|---|---|
| **GCP bucket** | Production deployments — images sync on demand |
| **Hosted image service** | Simple public hosting (Postimages, Imgur, etc.) |
| **Git LFS** | If your git host supports it and you want images in version control |
| **Local folder only** | Development machines where images are managed manually |

For GCP deployments, images are fetched on demand from the bucket and cached
locally in `content/images/`. Thumbnails are auto-generated into `~/cwva/thumbnails/`
and are never committed to any repo.

---

## Data Development Best Practices

### Namespaces

Use existing namespace prefixes wherever possible:

| Prefix | Namespace | Use for |
|---|---|---|
| `vad:` | `http://visualartsdna.org/model/` | Defined properties and classes |
| `work:` | `http://visualartsdna.org/work/` | Works, artists, profiles, entities |
| `the:` | `http://visualartsdna.org/thesaurus/` | Concepts, notes, thesaurus terms |
| `schema:` | `https://schema.org/` | Standard metadata (image, dateCreated, etc.) |
| `skos:` | `http://www.w3.org/2004/02/skos/core#` | Concept relationships and notes |
| `rdfs:` | `http://www.w3.org/2000/01/rdf-schema#` | Labels and comments |

You can create your own namespace reflecting your domain:

```turtle
@prefix mine: <http://myRegisteredDomain.art/> .

mine:7ca0ed90-8118-461f-8757-9ee35f9fc30f
    a vad:CreativeWork ;
    rdfs:label "My Work" .
```

Ensure any new namespace prefix is declared in the TTL file prolog wherever
it is referenced.

---

### Image and Document URIs

When creating URIs for images or documents use the canonical domain:

```turtle
schema:image <http://visualartsdna.org/images/myImage.jpg> ;
vad:mdDocument <http://visualartsdna.org/documents/myDoc.md> ;
```

At runtime `http://visualartsdna.org` is replaced by your server's host,
pointing back to your local datastore. This gives you freedom to rehost or
rename your server — change your IP or domain in the config and all URIs
update automatically. If you use your actual host directly in the data, you
will need to update every URI when your server address changes.

---

### URI Identifiers

**Always use GUID-based URI identifiers for new data instances.** Your server
provides a GUID generator at:

```
http://localhost:8080/guid
```

Refresh the page to generate a new GUID. Copy it and use it as your URI
local name:

```turtle
work:7ca0ed90-8118-461f-8757-9ee35f9fc30f
    a vad:CreativeWork ;
    rdfs:label "My Work" .
```

You can use camel-cased word or name URIs (`work:MyPainting`) but be aware
they risk conflicts when your data is combined with data from other servers.
A conflict is not destructive — conflicting data is merged into a single
instance — but it can be hard to untangle cleanly. GUIDs eliminate this risk.

---

### Adding New Properties

You can invent new properties for your data:

1. **Check first** — look through the model files (`metacontent/model/`) to
   see if the property already exists
2. **Choose a namespace** — use `vad:` for properties aligned with the core
   ontology, or your own namespace for domain-specific properties
3. **Use it** — just reference your new property and value in the TTL file.
   The browser page will display it automatically with the predicate URI as
   the label
4. **Define it** — create a model TTL file in `metacontent/model/` with an
   `rdfs:label` for your property. This replaces the raw URI with a readable
   label in the browser page

```turtle
# In your model file
vad:myNewProperty
    a rdf:Property ;
    rdfs:label "My New Property" ;
    rdfs:comment "Description of what this property means." .
```

---

### Process Notes and Artist Observations

Use `skos:note` to capture process observations, artist statements, and
informal remarks about a work:

```turtle
work:7ca0ed90-8118-461f-8757-9ee35f9fc30f
    skos:note "I love pushing this paint around with the knife." .
```

Notes are displayed on the browser page and serve as the natural source
for future concept extraction and tag generation. Write freely — the richer
the note, the more useful it becomes as a record of creative practice.

---

### Checking for Errors

When loading new data for the first time, check the server log for syntax
errors. The log identifies the file and the specific failure:

```
ERROR loading content/data/mywork.ttl: ...
```

Other files continue to load normally — a single bad file does not prevent
the rest of the data from being available. Fix the error in the TTL file,
then restart or refresh the server and check the log again.

Common TTL syntax issues:
- Missing trailing `.` at the end of a subject block
- Unclosed triple-quoted strings
- Undeclared namespace prefix
- Invalid datatype URI

---

## See Also

- `README.md` — installation and quick start
- `installOptions.md` — deployment options and configuration reference
- `ROADMAP.md` — planned features including the authoring agent
