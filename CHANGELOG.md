# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`sparql.query(terms=True)`**: SELECT values as N-Triples terms (`<iri>`, `"text"^^<datatype>`, `_:label`), the same on rdflib, oxigraph and qlever (read from their SPARQL JSON results) — the term kind a lexical string loses.
- **`VALUE_KIND` and `ID_KIND` columns on violations frames** where no schema entry gives the term kind: `VALUE_KIND` on `sh:sparql` findings (the `?value` binding's kind), `ID_KIND` when a focus node is a literal. Values are `triplets.iri.TERM_KINDS` — `class`, `enum`, `reference`, `literal` (`VALUE_KINDS` plus the `value_types` literal kind); the scalar `triplets.iri.absolute_value(key, value, …, kind=)` takes one in place of the KEY's schema entry.
- **`string_to_number=` on `export_to_excel` and `export_to_csv`** (default `False`: every
  VALUE is written as its text). `True` writes the columns that hold only numbers as
  numbers — number cells in Excel, for sums and charts — and does not keep the text form
  (`1.0` → `1`, `007` → `7`).
- **One SARIF log for several validation runs** — `export_to_sarif` and `build_sarif` take
  a list of violations frames and write one run per frame, in order, each with the tool
  name, rules and run metadata of its own frame:
  `triplets.validation.export_to_sarif([schema_violations, shacl_violations], data=data)`
  is one file with a `triplets-rdfs` run and a `triplets-shacl` run. The other arguments
  apply to every frame; a single frame gives the same log as before. Runs of one
  constraint language would share a tool name: two frames of one language raise
  `ValueError`, so join those with `concat_violations` first. An empty frame without run
  metadata in the list is left out.
- **`stereotype` and `rdf:type` on every bundled schema entry** — the RDFS source types
  of the entry's object, as lists in source order, deduplicated, and left out when the
  object has none. Covers classes, attributes, associations, enumeration attributes,
  enum values and datatypes. Stereotypes are written as text, and an IRI stereotype
  (`…/UML#attribute`) as its local name. `rdf:type` values are prefixed names
  (`rdfs:Class`, `rdf:Property`, `cim:UnitSymbol`) from the section's prefixes, and an
  IRI in an unbound namespace stays absolute. All 8 bundles are regenerated, and are
  about 25 % larger.
- **File name and zip name in the Distribution rows** — two more `Distribution` rows,
  Nepomuk File Ontology terms (`nfo:`): `fileName` on every parsed file, the file name with
  its extension (`label` after its last `/` or `\`; no row for a source without a name),
  and `belongsToContainer` on a file read from a zip, the zip's file name by the same rule
  (of the zip path, the zip file object's `.name`, or a nested zip's member name).
  Both record the names as parsed, not updated when `label` is later rewritten.
  `label` is unchanged. Row order: `Type`, `label`, `fileName`, `belongsToContainer`. All
  parser engines write them (also `rdf_parser.load_RDF_to_list`); zip members from
  `iter_all_xml` / `find_all_xml` carry the zip's file name as `.container` next to `.name`. Like
  `label`, they stay out of CIM XML (unless `export_undefined=True`) and of `cim-diff` /
  `cim-spreadsheet` (unless `--keep-metadata`)
  ([#229](https://github.com/Haigutus/triplets/issues/229)).
- **`validate_schema(..., severity=)` / `validate_schema_references(..., severity=)`** (and
  `compile_schema` / `compile_references`) — report schema findings as `"Warning"` or `"Info"`
  instead of `"Violation"` (SARIF `warning` / `note`, `sh:resultSeverity`); other values raise
  `ValueError`, `triplets:lexicalForm` findings stay `Warning`; `cgmes_tools.validate_by_header` has the same
  `severity=` parameter, for both of its runs
  ([#150](https://github.com/Haigutus/triplets/issues/150)).
- **SARIF `rules[].help`** (text + markdown) — the rule help GitHub shows under an alert:
  the shape's `sh:message`, path and expected value, the schema description with
  multiplicity and the class description (frames enriched with the `rdf_map`), and the
  source: shape IRI or profiles, the shapes file, a link to the constraint's
  definition (SHACL spec section for `sh:*`)
  ([#113](https://github.com/Haigutus/triplets/issues/113),
  [#150](https://github.com/Haigutus/triplets/issues/150)).
- **SARIF rule tags** — `rules[].properties.tags`: the constraint language (`shacl` /
  `rdfs`), the constraint (`sh:minCount`, `triplets:range`, …) and each profile of the
  rule's findings, distinct, at most 10 (what GitHub keeps: the profiles after the first
  8 of a rule get no tag); a `PROFILE` cell is a list of section keys or the text a csv /
  Excel export writes (`EQ, OP`), with the same tags; GitHub code scanning filters
  alerts by tag ([#94](https://github.com/Haigutus/triplets/issues/94)).
- **SARIF configuration notifications for what a run did not evaluate** — one
  `invocations[0].toolConfigurationNotifications` entry (level `warning`) per
  `skipped_shapes` item (`descriptor.id` `triplets/skippedShape`) and per
  `skipped_components` item (`triplets/skippedComponent`), the reason or component name
  as the message; each kind the run has is declared in `tool.driver.notifications`
  with a `shortDescription`; the run property bag keeps both lists. A run without timestamps but
  with skipped entries gets an invocation too. GitHub code scanning does not display
  invocation notifications; SARIF viewers do
  ([#94](https://github.com/Haigutus/triplets/issues/94)).
- **`source_root=` on `export_to_sarif` / `to_sarif` and `export_to_shacl_report` /
  `to_shacl_report`** (also `build_sarif`, `violations_to_report_graph`) — a file name
  that is an absolute path under that directory is written relative to it, with forward
  slashes. Every other name is written as given: a relative path, a path outside the
  directory, a file in a zip (`grid.zip/models/grid.xml`). The rule covers every file name of the export: `SOURCE_URI`
  (SARIF `artifactLocation.uri`, the SHACL report's `[context_location]` message), the
  `INSTANCE_LABEL` fallback location and the run metadata's `source` / `references`
  (SARIF run `properties`, whole-file fallback locations, rule help; SHACL
  `dcterms:source` / `dcterms:references`). `references` of a validation run are file
  names without directories, so they stay as they are; an absolute `report_references=`
  is written relative. No name is joined with the working directory. Symlinks are
  resolved in the directory and in the names before they compare. Absolute names outside the directory are named in
  one logged warning. A `source_root` that is not a directory raises `ValueError`.
  Default `None` writes the names as given
  ([#94](https://github.com/Haigutus/triplets/issues/94)).
- **The referenced object as a SARIF related location** — for a reference finding
  (`sh:class`, reference `rdfs:range` / `triplets:range`, `triplets:missing`) whose referenced
  object is in the data and located in the sources, the SARIF result carries `relatedLocations[{id,
  message, logicalLocations, physicalLocation}]` (the `TARGET` text as the message, the
  definition line — the first `rdf:ID` / `rdf:about` element with its id in read order:
  the plain files of `sources=` as given, then the members of the zips — `source_root=`
  applies) and the `[context_message]` line links it as
  `referenced object [<id>](1) found — …` (SARIF 2.1.0 §3.11.6; an id holding `[`, `]` or `\`
  stays unlinked); the logical location is `Type/id`; grouped results carry one per
  referenced object their `[context_message]` line shows (three at most), each linked.
  A related location needs a located referenced object — `sources=` on the export, or `locate`
  after `enrich`: GitHub code scanning rejects an upload with a related location that has no
  file position. A dangling reference gets none. `data_scope=` on `export_to_sarif` /
  `to_sarif` keeps the export's enrichment to the instances the run saw, as on `enrich`. New
  enrichment columns `TARGET_ID` and `TARGET_TYPE` (`enrich` with `data=`: the referenced
  object's id and Type when it is in the data) and
  location columns `TARGET_URI`, `TARGET_LINE`, `TARGET_SNIPPET` (`locate_violations`, the same
  single pass; only referenced objects in the data are searched). The `TARGET` text in the
  frame, csv / Excel and the SHACL report is unchanged
  ([#94](https://github.com/Haigutus/triplets/issues/94)).
- **Fixed language tag per schema entry** — `"language": "en"` on an Attribute entry writes
  `"text"@en` in N-Quads (pandas, polars, streaming; hence the rdflib / pyshacl loader) and
  `xml:lang="en"` on the literal element in both CIM XML engines, with or without
  `datatypes=`; the tagged literal gets no datatype annotation. `language` on a KEY whose
  `xsd:type` is other than `""` / `xsd:string` / `rdf:langString`, or a tag that is not
  well-formed BCP 47, raises `ValueError` naming the KEY, in the exports and in
  `validate_schema` (which checks no datatype on a tagged KEY). New flat map
  `iri.languages(rdf_map)`, scalar `iri.language_tag(key, tag, declared, prefixes)` and
  constant `iri.XML_NS`. The shipped bundles carry no tag: set
  it on a downstream schema or the loaded dict
  (`rdf_map["CO"]["description"]["language"] = "en"`, see `docs/export_schemas.md`)
  ([#146](https://github.com/Haigutus/triplets/issues/146)).
- **`cython_pugixml` CIM XML export writes `rdf:datatype`** with `datatypes=True`: the
  same attributes as `python_lxml`, from the same `iri.datatypes(rdf_map)` map, undefined
  keys (`export_undefined=True`) included; polars input stays on the cython engine. Before,
  the engine raised `NotImplementedError` and `engine="auto"` switched to `python_lxml`
  ([#158](https://github.com/Haigutus/triplets/issues/158)).
- **`validate_shacl(..., abort_on_first=False)`** — stop at the first Violation for
  a yes/no gate (polars / duckdb before the pandas-delegated components, pandas between
  rules, pyshacl after its first failing constraint); `attrs["validation"]["aborted"]`:
  True when `abort_on_first` met a Violation (the report may then be partial). Warnings and `triplets:invalidSparql` never stop the run
  ([#175](https://github.com/Haigutus/triplets/issues/175)).
- **Date and dateTime range bounds in the vectorized SHACL engines** — `sh:minInclusive` …
  `sh:maxExclusive` with an `xsd:date`, `xsd:dateTime` or `xsd:dateTimeStamp` bound compare
  the values as dates or instants on pandas, polars and duckdb, nested `sh:or` / `sh:and` /
  `sh:not` members included: a date's zone is ignored, zoned dateTimes compare across
  offsets and fractions of a second, an unzoned value compares only with an unzoned bound.
  A value that does not parse as the bound's type violates. The bound's value space comes
  from the new flat map `validation.value_space.SPACES`; the IR `params` of such a bound is
  `(space, lexical form)`, a number bound stays a float
  ([#215](https://github.com/Haigutus/triplets/issues/215)).
- **Date and dateTime pairs in the vectorized SHACL engines** — `sh:lessThan` /
  `sh:lessThanOrEquals` compare in the value space of their two operands on pandas, polars
  and duckdb, nested members included: each side's space comes from a `sh:datatype` on the
  property shape (path side), else the KEY's `xsd:type` in the `rdf_map` (a non-XSD type by
  its XSD base type, the new `iri.xsd_base` that `validate_schema` uses too), else the other
  side; untyped operands compare as numbers, as before. Operands in different spaces, a value
  that does not parse and a zoned dateTime against an unzoned one violate (the SHACL rule;
  pyshacl reports nothing for some of them). The IR `params` of a pair beside a `sh:datatype`
  is `(KEY, space)`, otherwise the KEY as before
  ([#216](https://github.com/Haigutus/triplets/issues/216)).
- **Linux aarch64 wheels** (manylinux), built on GitHub's native arm64 runner;
  also what Docker runs on Apple Silicon
  ([#130](https://github.com/Haigutus/triplets/issues/130)).
- **SPARQL targets in the vectorized engines** (`sh:target [ a sh:SPARQLTarget ; sh:select … ]`,
  IR `target_by="SPARQL"`): each target query runs once per validation on `triplets.sparql`
  and its `?this` nodes are the focus. Same findings as pyshacl on pandas, polars and duckdb.
  A target query no engine can run flags the shape as a `triplets:invalidSparql` Violation;
  a `sh:target` without `sh:select` stays in `skipped_shapes`
  ([#161](https://github.com/Haigutus/triplets/issues/161)).
- **`sh:xone` in the vectorized engines** — exactly one alternative must hold,
  per focus node on a node shape and per value node under a path (`VALUE` = the
  value node); an empty (switched-off) alternative holds. pandas implements it,
  polars and duckdb hand it to pandas like the other logical operators. Same
  findings as pyshacl.
- **Vectorized engines walk `sh:targetObjectsOf` and `sh:targetNode`** (IR
  `target_by="VALUE"` — the VALUEs under the KEY, referenced objects whether
  described in the data or not — and `target_by="ID"` — one ID, in the data or
  not, so the ENTSO-E `sh:targetNode cim:IDuniqueness` idiom gives a dataset-wide
  `sh:sparql` its focus). Same findings as pyshacl on pandas, polars and duckdb.
  Only `sh:targetClass` and `sh:class` follow the schema inheritance. `sh:rule`
  shapes are listed in `skipped_shapes` (pyshacl applies rules, the vectorized
  engines never do).
- **`cim-diff` report options**: `--stat` (counts per type only), `--include TYPE`
  (only these classes, also in `cim-spreadsheet`), `--context KEY` (unchanged
  values of KEY shown in each changed object, e.g. `IdentifiedObject.name`).
  `print_triplets_diff(include_objects=, context_keys=, stat=)` in every
  engine, next to `exclude_objects`. `--version` in both CLIs.
- `filter_triplets_by_type` takes a list or array of types too (all engines);
  duckdb takes `type_key` like pandas and polars.
- **Validation API — three generic validations, scoped by the caller:**
  every input has a scope named after it, `data` + `data_scope` (INSTANCE_IDs;
  rows outside are not loaded) and `rdf_map` + `rdf_map_scope` (profiles of the
  export schema: section keys and/or declared profile IRIs, mixed; `"*"` every
  section; None the root entries; an empty scope raises). Listed
  profiles are merged for the run (`schema_scope.scoped_rdf_map`): parameter,
  inheritance and enumeration lists united, cardinality relaxed to the
  loosest (a missing bound is no bound), a key whose datatype differs (or is
  missing in one profile) left untyped and unchecked (listed in
  `skipped_shapes`), the loosened checks listed in `relaxed_constraints`,
  each note quoting every profile's own value; the merged ProfileMetadata keeps every
  profile's identity (section keys, each field as a list in scope order).
  `rdf_map_entries` (and the flat maps on it) skip ProfileMetadata. Nothing in `triplets.validation` reads
  instance headers.
  - `validate_shacl(data, shapes, rdf_map, data_scope, rdf_map_scope, inheritance=False)`
    — SHACL; the scoped profiles give typed literals, nodeKind and, with
    `inheritance=True`, class inheritance. The `inheritance` flag stays on `validate_shacl` and
    `validate_schema_references`; `attrs["validation"]["inheritance"]` says
    whether it was applied.
  - `validate_schema(data, rdf_map, data_scope, rdf_map_scope, report_undefined_keys=False)`
    — cardinality, datatypes and enumerations, each instance on its own,
    against the merged profiles; PROFILE and `attrs["validation"]["profiles"]`
    list the scoped section keys (`["EQ", "OP", "SC"]`), the reports format
    the list for display.
  - `validate_schema_references(data, rdf_map, data_scope, rdf_map_scope,
    target_data_scope, target_rdf_map_scope, report_missing_targets=True, inheritance=True)`
    — the reference types of each source instance's associations, against the
    objects in the source and target instances (both data scopes default to
    every instance); valid classes from the source
    and target profiles; a reference to an object not in the data is
    `triplets:missing`; a range class no given profile defines is listed in
    `skipped_shapes`. `compile_references` compiles the checks.
  - `cgmes_tools.declared_profiles` (header → profiles, legacy 2.4.15 URLs)
    and `cgmes_tools.validate_by_header` (each instance against its declared
    profiles, optionally its references against every loaded instance;
    `data_scope` selects the validated instances only)
    hold the CIM header logic.
  - `concat_violations(frames)` joins the frames of several runs, merging their
    run metadata (shape and constraint counts once per rule set: `compiled_hash`
    + `deactivated_shapes`).
- **Class inheritance follows the scoped profiles, the same on every engine:**
  `schema_scope.class_ancestors(rdf_map, rdf_map_scope)` unions same-named
  Class entries' `inheritance` lists and closes the relation only through
  classes with their own entry in the scope. The vectorized engines widen
  `sh:targetClass` / `sh:class` through one class-membership index built from
  its pairs (the IR is not fanned out); pyshacl gets the same edges as
  `rdfs:subClassOf` in `ont_graph` (recorded parent IRIs as is,
  `https://cim.ucaiug.io/ns#IdentifiedObject` for NC). `inheritance=False`
  (the `validate_shacl` default) matches the exact `rdf:type`. Schema class rules never widen: each Class
  entry already lists its inherited attributes. Identical findings are
  reported once on every engine (a property shape on both a parent and a
  child node shape, the same triple in two instance files).
- **Property-level `sh:or` / `sh:not` judge value nodes:** a nested shape
  without its own `sh:path` now constrains the property's values. Before, it
  compiled with no path: `sh:or ( [ sh:datatype … ] [ sh:in … ] )` reported
  nothing and `sh:not [ sh:in (…) ]` flagged every focus node.
- **NCP 2.5.0 export schemas** (`ENTSOE_NC_2.5.0_552_ED1/ED2.json`,
  `schemas.ENTSOE_NC_2_5_0_552_ED1/ED2`) generated from
  [application-profiles-library](https://github.com/entsoe/application-profiles-library)
  release branch `ncp-v2-5-0` (`NCP/RDFS`, tag `ncp-2.5.0`). DatasetMetadata header attributes bind again via `schema:domainIncludes`
  ([#99](https://github.com/entsoe/application-profiles-library/pull/99) / [#92](https://github.com/entsoe/application-profiles-library/issues/92)).
- **`sh:deactivated` honoured; `validate_shacl(deactivated_shapes=)`** — every
  shape compiles once; `compiled.deactivated` lists the shapes the graph
  declares `sh:deactivated`, and the run's rule set is `compiled.active(...)`:
  the declared set switched off, or the caller's set instead of it (None =
  declared, `[]` = run everything) before any engine runs (node, property and
  standalone property shapes, `sh:node` targets, logical members; paths stay
  declared under `sh:closed`; truthiness as pyshacl, a plain `"false"`
  deactivates, an ill-typed literal does not, a shape with a non-literal or
  second value is skipped as malformed). pyshacl and the SHACL report's embedded
  shapes receive the same set; the run metadata lists it as `deactivated_shapes` next to the
  shape files' `compiled_hash`. One compiled base serves the NC validation
  aggregates that switch different shapes off.
  IR rows carry their lineage in a `shapes` column.
- Tests: the CGMES 2.4 Enhanced SHACL shapes come from the new
  `test_data/application-profiles-library-cgmes-v2-4` submodule
  (entsoe/application-profiles-library, branch `cgmes-v2-4`). Every file
  compiles and loads in pyshacl; the RealGrid EQ / SSH / TP / SV instances are
  validated against them (exact types, `inheritance=False`) with the same
  findings on pandas, polars, duckdb and pyshacl (pyshacl on SV by default,
  the rest under `-m performance`).

### Changed
- **`cgmes_tools.get_loaded_model_parts` lists one row per header of each instance**, with
  a new `INSTANCE_ID` column on every result flavor (pandas and duckdb input: header IDs
  stay the index, which is not unique when two files share a header ID; arrow input: an
  `INSTANCE_ID` column next to `ID`; polars: `ID` and `INSTANCE_ID` columns). Two files
  that share a header ID were one row with the first file's values.
- **`cgmes_tools.get_loaded_models` keys each model by its root's `INSTANCE_ID`**, was the
  root header ID. The dependency walk runs over instances: two files that share a header
  ID are two parts (two roots when nothing depends on them), and a part that depends on
  that ID depends on both. They were merged into one model. A twin that depends on the
  shared ID (its own header's) is no dependency of itself: it stays a root.
- **`INSTANCE_ID` is part of the triplet frame contract**: functions may raise on a frame
  without the column. A null `INSTANCE_ID` is one instance, matched null-safe, the same on
  pandas and polars (`get_metadata_from_FullModel`, `get_loaded_model_parts`,
  `get_loaded_profiles`, `get_loaded_models`).
- **`validate_shacl` defaults to `inheritance=False`** — `sh:targetClass` and `sh:class`
  match the exact `rdf:type`, as plain SHACL does on a data graph without
  `rdfs:subClassOf` (CIM files carry none, and the ENTSO-E shapes list concrete classes).
  The default was `True`: a run with an `rdf_map` also matched instances of subclasses
  from the scoped profiles' `inheritance` lists. Pass `inheritance=True` for that
  behaviour; a caller that relied on the old default gets fewer findings on abstract
  class targets and more `sh:class` findings on subclass references.
  `validate_schema_references` keeps `inheritance=True`: a reference to a subclass of
  the range class conforms.
- **`export_to_excel` and `export_to_csv` (pandas) write every VALUE as its text**, so
  `cim-spreadsheet` CIM XML → Excel → CIM XML keeps the lexical form of numbers. A column
  that held only numbers was converted to numbers first: `1.0` came back as `1`,
  `522000000.0` as `522000000`, `0.10` as `0.1`, `007` as `7` (716 values of the emtiop
  IEEE39 case through `.xlsx`). Number cells in the Excel file are now text cells. The
  polars CSV export already wrote the text. `triplets_to_tableviews` (pandas, polars)
  takes `string_to_number=` like the DuckDB one; its default stays `True`. It is a keyword
  on all three, after `multivalue` (DuckDB had `string_to_number, multivalue` by position).
- **`SOURCE_SHAPE` and the SARIF ruleId of schema findings, against 0.2.0.** `SOURCE_SHAPE`
  carries the scope in its namespace: `urn:triplets:schema:EMTIOP#ACLineSegment.ACLineSegment.r`
  (0.2.0: `urn:triplets:schema#EMTIOP:ACLineSegment.ACLineSegment.r`), every profile of a
  merged scope in order (`urn:triplets:schema:EQ:OP:SC#Breaker.IdentifiedObject.name`), no
  profile without a scope (`urn:triplets:schema#Breaker.IdentifiedObject.name`). The ruleId
  is the scope, the shape's local name and the constraint:
  `EMTIOP:ACLineSegment.ACLineSegment.r/xsd:type`, `EQ:OP:SC:Breaker.IdentifiedObject.name/xsd:minOccurs`,
  `Breaker.IdentifiedObject.name/xsd:minOccurs`. It is the same for the same scope in every
  run, in any order of `rdf_map_scope` (the ruleId sorts the profile names; `SOURCE_SHAPE`
  keeps the given order), and one class and key checked under two scopes are two rules.
  `:` and `#` separate the parts of the shape id, so a section name is percent-encoded in
  it (`EQ#1` → `EQ%231`); only an id that starts with `urn:triplets:schema:` or
  `urn:triplets:schema#` is a schema shape. A one-profile scope
  keeps its 0.2.0 ruleId; a scope of several profiles had one rule per profile in 0.2.0, so
  its GitHub code scanning alerts close and re-open once on upgrade. A consumer that
  parses `SOURCE_SHAPE` or `properties.sourceShape` reads the new form.
- **`rdf:resource="#x"` references `rdf:ID="x"`** — `iri.local_id` strips one of
  `urn:uuid:`, `#_`, `#`, `_` (longest first), where a bare `#` stayed: a file whose IDs
  have no underscore (`rdf:ID="SS1"`, `rdf:resource="#SS1"`) parsed to the ID `SS1` and
  the reference VALUE `#SS1`, which matched nothing and was exported as `#_#SS1`. All
  parser engines, `read_nquads`, the pandas / polars `local_id` and the pandas / polars /
  DuckDB `local_value` follow the rule. A lone `#` (the document itself) stays `#`. An
  `rdf:about="#Name"` ID parses to `Name` too: RDFS vocabularies, whose IDs are `#Name`,
  keep their IRIs with `local_resources=False`, as `rdfs_tools` parses them.
- **`triplets.rdfs_tools.cim_rdfs_to_json_deprecated` is removed** — a script that ran on
  import; use `triplets.rdfs_tools.cim_rdfs_to_json`.
- **`triplets.validation.schema_name(rdf_map)`** — the name a schema run reports in
  `references`: the file name for a path, `"rdf_map"` for a dict.
- **`export_to_cimxml` raises `ValueError` for an unsupported `export_type`**, before any
  work; it logged "Not supported option" and returned an empty list.
- **A file in a zip is named `<zip file name>/<member name>` in validation results**
  (`grid.zip/models/grid.xml`; the zip's name without directories, the inner zip's for a
  zip in a zip): `SOURCE_URI` / `TARGET_URI` from `locate` and from `sources=` on the
  SARIF and SHACL report exports, `INSTANCE_LABEL` from `enrich`, and the run's `source`
  (`attrs["validation"]`, SARIF run `properties`, `dcterms:source`) — one name per file
  in one report. It was the member name alone, which reads as a path of the repository
  and, with `source_root=`, could name another file there. The `label` row of the
  parsed data is unchanged; the zip's name is read from its `belongsToContainer` row.
  Two zips with the same file name and member give the same name.
- **`iri.key_types(rdf_map)` returns name → dict**: `{"type": …}` plus `stereotype` /
  `rdf:type` where the entry has them (previously name → type string). For example,
  `key_types(rdf_map)["DateTimeStamp"]` is
  `{"type": "Primitive", "stereotype": ["Primitive", "eumd"], "rdf:type": ["rdfs:Class"]}`.
- **`validate_schema` / `validate_schema_references` parse an `rdf_map` path once per call**
  and hand the loaded dict down. `compile_schema` / `compile_references` cache on the loaded
  schema's content and the given file name (`"rdf_map"` for a dict); the run metadata's
  `references` is the `rdf_map` file name given to the validation call, or to
  `compile_schema` when its result goes to `validate_shacl`.
- **`data_unchanged=True` also asserts the `rdf_map` dict is unchanged** (`sparql.query`,
  `load_dataset`, the engines): passed again with the same data object, the same dict object
  reuses the schema serialization stored with the data digest instead of a `json.dumps` per
  call (a path is still read per call). The sh:sparql phase of `validate_shacl` serializes its
  scoped schema once per run instead of once per query (~1 ms each): warm `engine="polars"`
  on Belgovia / Svedala EQ with the CGMES 3.0 shapes ~0.45 → ~0.35 s / ~0.82 → ~0.72 s.
- **SARIF rule titles name the constraint language**: `SHACL …` / `RDFS …` on every
  rule, `sh:name` titles included (`SHACL Line completeness (8×)`)
  ([#150](https://github.com/Haigutus/triplets/issues/150)).
- **SARIF tool name per constraint language**: `tool.driver.name` is `triplets-rdfs` for
  schema runs (`validate_schema` / `validate_schema_references` / `validate_by_header`);
  SHACL runs and bare frames stay `triplets-shacl`. On GitHub code scanning the alerts of a
  schema category uploaded earlier as `triplets-shacl` keep their numbers and are matched by
  the `triplets-rdfs` upload, but they stay open even after the finding is fixed, held by the
  old `triplets-shacl` analysis. Upload one empty `triplets-shacl` log to each such category
  and ref (or delete its old analyses) to release them
  ([#94](https://github.com/Haigutus/triplets/issues/94)).
- **CIM XML export loads the schema and builds its datatype / language maps once per
  export** instead of per instance (sequential `cython_pugixml`, Svedala: ~76 → ~55 ms min);
  with `max_workers` on pandas input each worker gets the loaded schema once, so an in-memory
  `rdf_map` dict is as fast as a path (`cython_pugixml`, Svedala: ~105 → ~79 ms median)
  ([#206](https://github.com/Haigutus/triplets/issues/206)).
- **oxigraph SPARQL engine skips the default-graph copy for a single-instance store**
  (one named graph, no rows without INSTANCE_ID): unscoped queries run against that
  instance's graph, which — as with `data_scope` — takes precedence over a FROM in the
  query. Other stores still get the union copied into the default graph at load.
  RealGrid 2.4 EQ (892 k rows) cold load + query 3.8 s → 2.5 s, peak RSS ~200 MB less;
  Svedala EQ 0.22 s → 0.16 s. Per-instance `validate_shacl` runs (sh:sparql) take this
  path.
- **polars SHACL batch plans join the class membership before the rules** (on ID, then
  on KEY + class), instead of pairing every row with every rule on its KEY first.
  RealGrid 2.4 EQ (892 k rows), `collect_all` 0.78 s → 0.51 s, peak RSS 3.7 → 2.1 GB
  (with inheritance 0.89 s → 0.59 s, 3.8 → 2.2 GB); all four instances (1.15 M rows)
  1.14 s → 0.67 s, 4.0 → 2.8 GB; Svedala EQ 0.42 s → 0.37 s, 0.89 → 0.68 GB. Same
  findings.
- **sh:sparql binds the focus nodes first**: the `VALUES ?this { … }` block opens the
  WHERE group instead of closing it, so the engine starts the authored pattern (e.g. a
  `FILTER NOT EXISTS`) from the focus nodes. Svedala EQ against the CGMES 3.0
  Equipment-AP shapes on oxigraph, sh:sparql phase 3.5 s → 0.4 s sequential and
  2.3 s → 0.25 s at 4 workers (`validate_shacl` warm 4.3 s → 1.2 s / 3.1 s → 1.1 s).
  Same findings on Svedala EQ. Fixes missed violations when the query starts with
  `BIND (EXISTS { $this … } AS ?x)` (40 shipped NC queries), on every SPARQL engine,
  or with `OPTIONAL { $this p ?v } FILTER (!bound(?v))`, on oxigraph and qlever
  (rdflib's left join still drops the unmatched focus node): the pattern ran before
  `$this` was bound, so one matching node anywhere cleared every focus node. Findings
  of such queries (e.g. CGMES SV / ShortCircuit, NC) gain those violations. The rdflib
  SPARQL engine is slower now — it re-runs the authored pattern per focus node
  (Belgovia 2.2 s → 4.0 s).
- **polars SHACL batch plans take the inverse-path and `( assoc rdf:type )` rules too**
  (every batched component — `sh:minCount`, `sh:maxCount`, `sh:in`, `sh:datatype`,
  `sh:nodeKind` — on a class target), instead of one plan per rule: 610 → 163 plans on
  the CGMES 3.0 EQ shapes, 330 → 250 on the 2.4 EQ shapes. `collect_all`, polars 2.0:
  Belgovia EQ 0.29 s → 0.06 s, Svedala EQ 0.36 s → 0.08 s, RealGrid 2.4 EQ 0.50 s → 0.40 s
  (polars 1.44: 0.07 → 0.02 s, 0.18 → 0.06 s, 0.83 → 0.59 s). Same findings.
- CI: `build-wheels.yml` runs on a pull request only when it touches the build
  (setup/packaging files, cython/C++ sources, the pugixml submodule, the wheel tests);
  releases and manual dispatch always build.
- CI: macOS arm64 wheels build on `macos-15` (GitHub retired the `macos-14` runner image).
- **pyshacl engine `data_scope` matches INSTANCE_ID values as given**, like the pandas /
  polars / duckdb SHACL engines: a `urn:uuid:`-prefixed scope entry no longer matches the
  bare INSTANCE_ID.
- **Breaking — IR columns renamed**: `target_kind` → `target_by` (`"Type"` for
  `sh:targetClass`, `"KEY"` for `sh:targetSubjectsOf`) and `target_class` →
  `target_on` (the Type name or the KEY). The IR speaks the repo's own
  vocabulary (ID, KEY, VALUE, Type), not SHACL's.
- **Breaking — `sparql.query(scope=)` → `data_scope=`**, matching the validations
  (also `_rdflib_loader.scoped_graph(dataset, data_scope=)`).
- **Breaking — validation entry points:** `validate` is `validate_shacl` and its
  `scope` is `data_scope`; `validate_schema` takes `data_scope` /
  `rdf_map_scope` and `report_undefined_keys` (was `closed`) instead of
  resolving instance headers (`profiles=` removed — `cgmes_tools.validate_by_header`
  reads headers); listed profiles are merged instead of run one by one; its
  `rdfs:range` reference findings move to `validate_schema_references`.
  `compile_schema(rdf_map, rdf_map_scope, report_undefined_keys)` returns one
  `CompiledShapes` (`CompiledSchema` removed). The engines take `data_scope`
  and an `ancestors` map. A sectioned `rdf_map` needs `rdf_map_scope`.
- **Breaking — enrichment is its own call:** validation adds EXPECTED and
  MESSAGE_SOURCE (from the rules); TARGET (the referenced object found, or not;
  the duplicate values of a maximum-count finding) is `enrich`'s first
  column. `context=True` is removed: call `enrich(violations, data=, shapes=,
  rdf_map=, rdf_map_scope=, data_scope=)` — `data_scope` reads only the
  instances the run saw.
- **NC inheritance:** `AutomationFunction`, `BaseCaseCurrentLimit`,
  `DirectCurrentEquipmentController` and `PowerTransferCorridor` no longer
  reach `PowerSystemResource` / `IdentifiedObject` (their own lists stop
  earlier, and no other class's list is read as their parents). CGMES
  inheritance is unchanged.
- **`triplets.iri`** — one public package for the triplet ↔ IRI contract:
  `local_id` / `local_key(iri, type_key)` / `local_value(iri, kind)` (local forms,
  all on one split, `split_iri` → namespace + local name; `TYPE_KEY`,
  `DESCRIPTION_TYPE`),
  `absolute_id` / `absolute_name` / `absolute_key` / `absolute_value` (absolute IRIs, taking
  the flat maps `namespaces` / `value_types` / `datatypes` built from `rdf_map` — one
  comprehension each, no cache), the namespace constants, and
  `iri_pandas` / `iri_polars` / `iri_duckdb` flavors with the same names. The parsers, N-Quads
  export/read-back, the qlever ingest bridge, SPARQL result decoding, SHACL
  reports, SARIF and the validation engines all use it; the six private
  local-name helpers and the duplicated `"urn:uuid:"` / namespace literals are
  gone. `tests/test_iri.py` is the case table every flavor is held to.
- `triplets.parser.clean_ID` → `triplets.parser.local_id` (also
  `triplets.iri.local_id`). `rdf_parser.clean_ID` stays as a deprecation
  wrapper. ID prefixes (`urn:uuid:`, `#_`, `_`) are stripped **once**, longest
  first — the python engines stripped cumulatively (`urn:uuid:_x` → `x`) while
  the cython engine stripped one (`_x`); every engine now agrees.
- `export.nquads_utils.build_key_metadata` → `iri.namespaces` / `iri.value_types` /
  `iri.datatypes`; `make_object` takes them as keyword maps. The pandas N-Quads
  exporter is vectorized (no per-row `apply`).
- **The schema entry type decides the serialisation form** (N-Quads, qlever
  ingest, SHACL `sh:nodeKind`), never `xsd:type` and never the shape of the
  text. An Attribute value is a literal even when it reads `https://…` or
  looks like a UUID — `Model.modelingAuthoritySet` is now
  `"http://…"^^<xsd:anyURI>` and a string attribute holding an IRI stays a
  string (before, both became IRI nodes and `read_nquads` shortened them). A
  SPARQL query matching such a value as `<http://…>` must match the literal
  instead. CIM XML `datatypes=True` annotates anyURI attributes. An
  Association value is `absolute_id(value)`: an absolute IRI passes through,
  anything else becomes `urn:uuid:<value>` (before, a non-canonical reference
  silently became a string literal). The absolute-IRI / canonical-UUID
  heuristic now applies only to a KEY the schema does not declare. SHACL
  `sh:nodeKind` follows the same rule both ways: an Association or
  Enumeration path violates `sh:Literal` on every row and never `sh:IRI`.
- **IRI-unsafe text in an IRI is percent-encoded**, so a schema reference or
  enum value stays a real IRI whatever its text: `just some text` under an
  Association → `<urn:uuid:just%20some%20text>` (controls, space and
  ``<>"{}|^`\`` — `iri.encode_iri`, in every flavor and the qlever ingest).
  Before, such a value wrote an invalid N-Quads line that rdflib and oxigraph
  rejected and qlever accepted. `read_nquads`, CONSTRUCT and SHACL result
  decoding reverse exactly those escapes (`iri.decode_iri`). Attribute text,
  `xsd:anyURI` included, stays a verbatim literal.
- **Undefined names** — a class the schema does not list (abstract CIM classes
  such as `Equipment` included; conformant data with concrete `Type` values is
  unchanged), a KEY or an enum value it does not list, and every name when no
  `rdf_map` is given — are written under `undefined_namespace`, default
  `http://triplets#`, in N-Quads and both CIM XML engines. Before: CIM100 in
  N-Quads / qlever / the SHACL report, `http://triplets#` in the pandas CIM XML
  engine, unprefixed elements in the cython one, a bare value for an unknown
  enum value. `export_to_nquads` gains `export_undefined` (default `True`:
  lossless interchange keeps every row) and `undefined_namespace`;
  `export_to_cimxml` gains `undefined_namespace` (its `export_undefined`
  default stays `False`; an unknown enum value now follows the same switch
  instead of being written bare; an `undefined_namespace` the document already
  binds, such as CIM100, keeps its own prefix instead of adding `triplets:`). Schema-less `export_to_nquads` therefore
  changes from CIM100 to `http://triplets#`. A schema-less query does not:
  `sparql.query`, `validate_shacl` and `export_to_shacl_report`
  keep every row and default `undefined_namespace` to CIM100, so `cim:`
  queries keep working; all of them take the parameter.
- **Performance** (RealGrid, 1.15M rows): `read_nquads` 12.2 s → 2.9 s (line
  splitting and graph detection in Arrow compute, slice-based term unwrap;
  columns come back arrow-backed), pandas `export_to_nquads` 3.9 s → 2.2 s
  (mask-chain classification, lines joined in Arrow), polars export unchanged
  at 0.6 s. Regex replaces stay in the flavors — measured 2-8x faster than
  split/list ops in pandas and 1.5-2x in polars; the scalar `local_id` uses a
  prefix loop (faster per call for the python parsers).
- **`max_workers` threads the sh:sparql constraints on oxigraph and qlever**
  (pandas / polars / duckdb SHACL engines): the embedded engines release the GIL
  while evaluating over one shared store, so the queries run on a thread pool
  instead of sequentially; rdflib keeps the forked process pool. Svedala EQ
  against the CGMES 3.0 Equipment-AP shapes (92 constraint queries), sh:sparql
  phase on oxigraph 6.8 s → 2.4 s at 4 workers, 1.8 s at 8 (`validate_shacl`
  12.6 s → 7.9 s); on qlever 0.84 s → 0.35 s at 4. Same findings as sequential.
- `read_nquads` / CONSTRUCT decoding shorten per column: `ID` / `INSTANCE_ID`
  via `local_id`, `KEY` via `local_key`, `VALUE` via `local_value` — a
  URI-shaped `INSTANCE_ID` now survives the round trip. A VALUE IRI that is a
  subject of the same data shortens like its `ID` (`local_id`), so a reference
  to `<http://…#b>` still joins the object `http://…#b` instead of becoming `b`.
  KEY and `Type` VALUE IRIs shorten to the `iri.split_iri` local name — after the last `#`
  **or `/`** (a URN after its last `:`), as an XML element local name holds neither — so `dcterms:` header
  keys (552 Ed2, NC) read back as `issued` / `identifier`, the same KEY the CIM
  XML parser gives (before: the whole `http://purl.org/dc/terms/issued`), and a
  class in a `/` namespace as its name. References still split on `#` only.
- **`xsd:anyURI` values are lexically checked** by `sh:datatype` on every engine
  (pyshacl through the lexical supplement): an RFC 3987 IRI reference, relative
  and non-ASCII allowed; controls, space, DEL, ``<>"{}|^`\`` and a `%` not
  followed by two hex digits violate. Before, anyURI was never checked.
- **`parse(shorten_resources=)` is renamed `local_resources=`**, in the
  `triplets.iri` local / absolute vocabulary. No alias. `True` (default) is
  unchanged. `False` is now the absolute form: IDs and references are resolved
  against the document's declared absolute `xml:base` at parse time — without one
  against `parse(default_base=…)` (`http://triplets#`), never the file location
  (the NamespaceMap `xml_base` row, which the schema generator copies to
  `ProfileXMLBase`, now holds only a declared absolute base on every engine —
  the cython engine wrote the file name, the python ones the file path) —
  (`iri.resolve_iri`; `rdf:about="#X"` → `base#X`, `rdf:ID="X"` → `base#X`)
  instead of `local_id` only, which left `#Name` relative and made `#_x` local.
  The export schema generator runs on it and takes absolute IRIs only:
  `rdfs_tools.get_namespace_and_name` is built on `iri.split_iri`: an absolute
  IRI (any URI scheme) splits as is, only a bare or `#name` takes the
  `default_namespace`, and every other relative form (or no default) raises
  instead of guessing. The regenerated bundles hold the same entries, fields and
  values (checked), only CGMES 2.4 / 3.0 entry and `parameters` order changes —
  now sorted by absolute IRI, before by how the RDFS text happened to write each
  reference (`#X` or `http://…#X`).
- **Schema generation is filesystem-independent:** `rdfs_tools.list_of_files`
  returns sorted paths. NC 2.5 on `main` had been generated in `os.listdir`
  order (`SSI, RA, SIS, …`); it is now in file-name order like every other
  bundle. Each profile section and the `iri` flat maps (namespaces, entry types,
  value types, datatypes) are unchanged — a test now pins that no name has a
  conflicting namespace / entry type across sections (primitives `DateTime` /
  `URI` excepted), so first-wins merging is order-independent where the
  exporters rely on it. What depended on order was the merged human context.
- **Violation enrichment reads the schema per profile:** `SCHEMA_DESCRIPTION` /
  `SCHEMA_MULTIPLICITY` come from the profiles the violation was checked
  against (the `PROFILE` column, else `enrich(rdf_map_scope=)` — one scope, or
  one per instance — else the run's recorded profiles), merged like the run;
  rows without profiles read the root entries, and another profile's text is
  never borrowed. Before, a multiplicity that differs between profiles (NC 2.5
  `BaseTimeSeries.timeSeriesKind`: SIS `0..1`, AS `1..1`) showed whichever
  profile came first.
- **Importers share two rule sources.** The python XML engines and the legacy
  `rdf_parser.load_RDF_to_list` run one row loop (`parser.utils.iter_rdf_rows`);
  the RDF-term readers (`read_nquads`, CONSTRUCT on every engine, `sh:sparql`
  results, the pyshacl report) decode once, then use `local_id` / `local_key` /
  `local_value`. `read_nquads` takes `rdf_map=` (reference vs enum VALUE exact from
  the schema instead of guessed) and `type_key=` (the KEY for `rdf:type`, default
  `"Type"`). SHACL terms compile with the rule of the column they land on:
  `sh:path rdf:type` targets the `Type` KEY (was a KEY `type` that never matched),
  and an `sh:in` / `sh:hasValue` member with a `/`-only IRI stays whole like the
  data VALUE (was cut to its last segment).
  Visible changes: the pyshacl report VALUE is shortened like the other engines
  (an enum outside `sh:in` reports `SwitchKind.x`, not its full IRI);
  `load_RDF_to_list` gives the tag name for an element without a namespace
  (was `""`), `""` for a missing ID (was `None`) and for an empty element (was
  `None`); the cython parser finds `rdf:ID` / `rdf:about` / `rdf:resource` by
  the prefix the document binds to the RDF namespace, not only `rdf:`.
- **`Type` = `Description` is `rdf:Description`, not a class:** N-Quads and the
  qlever ingest state no `rdf:type` for it (was `http://triplets#Description`),
  and both CIM XML engines write an `rdf:Description` element (was
  `triplets:Description`). `export_to_cimxml(export_undefined=True)` on an
  instance without a model header no longer raises on the missing namespace map.
- Known import / export deviations that stay (CIM XML without percent-encoding,
  `http(s)` IDs, blank nodes, …) are listed in `docs/parsers.md` → *Known
  limitations*.
- `violations_to_report_graph` / `export_to_shacl_report` accept `rdf_map`;
  `sh:resultPath` then keeps the profile namespace instead of CIM100.
- SPARQL `scope` / validation `data_scope` build graph IRIs with `absolute_id`: a URI-shaped
  `INSTANCE_ID` scopes correctly (was silently empty), and an `https:` one is
  no longer prefixed with `urn:uuid:` by the N-Quads graph term.
- **Export-schema `xsd:type` comes from the RDFS.** `cim_rdfs_to_json`
  follows each CIMDatatype `.value` to an XSD `rdfs:range` (IEC 61970-501 Ed2)
  or to a Primitive (`Area.value` → `Float`); `cgmes_data_types_map` now lists
  only Primitives (17 names, was 38): a consumer that looked up a CIMDatatype in it
  (`Voltage`, `Susceptance`, `PU`, …) gets no type and no error, so its schema entries
  lose `xsd:type` and their datatype checks without a message. All bundles regenerated and checked against the published
  SHACL `sh:datatype`; changed types:
  - `Decimal`, `Money` → `xsd:decimal` (was `xsd:float`)
  - `MonthDay` → `xsd:gMonthDay` (was `xsd:integer`)
  - `Area`, `RealEnergy`, `VolumeFlowRate`, `Speed`, `Irradiance`, `Impedance`,
    `Pressure`, `Hours`, … → `xsd:float`; `Duration`, `Time`, `DateTimeStamp`,
    `UUID`, `Version`, `StringIRI`, `StringFixedLanguage` typed (were `""`)

  Schema validation in the pandas / polars / duckdb engines is stricter for
  `xsd:decimal`: `Money` / `Decimal` values in exponent form (`1e3`) or
  `INF` / `NaN` now fail the datatype check (they passed as `xsd:float`).

### Fixed
- **Schema findings name the instance they were found in.** `validate_schema`,
  `validate_schema_references` and `validate_by_header` add an `INSTANCE_ID` column, and
  `enrich` keeps it: `INSTANCE_LABEL` and the duplicate values of a maximum-count finding
  are that instance's. Two files that share an object ID (a header ID reused across
  files) were both attributed to the first file. A row without an `INSTANCE_ID` (SHACL
  over the merged graph) still goes to the first instance holding its ID.
- **`locate` puts a finding in its instance's file.** A row whose `INSTANCE_LABEL` names
  one of the sources (also a member of a zip) is located at its KEY's element or its
  object's definition in that file; a KEY only another file holds is still located
  there. Two files that share an object ID (a header ID reused across files) were both
  located in the first file read. `locate()` takes `files=` (`{ID: source names}`) and
  returns the positions within those files under `"files"`.
- **`cgmes_tools.get_metadata_from_FullModel` reads the first FullModel within its own
  instance.** On data with several instances, a second file sharing the header ID blended
  its values in (the last value of each KEY won).
- **`locate` puts a finding of an absent property at its object's definition** (`sh:minCount`,
  `xsd:minOccurs`), also when another finding on the same ID searched that KEY: it took
  that KEY's element, possibly in another file.
- **`enrich` keeps the cells a frame already fills** and fills only the empty ones:
  enriching twice equals enriching once. A second call recomputed every column, and a
  SHACL maximum-count finding on an ID two instances share then listed one instance's
  values in TARGET instead of every instance's.
- **`cgmes_tools.get_loaded_profiles` names each declaration's own header class**: HEADER
  is the Type of the header in the declaration's instance. Two files sharing a header ID
  (a FullModel and a dcat:Dataset) both showed the first file's class.
- **SHACL `sh:in` / `sh:hasValue` compare IRIs, not local names** (pandas / polars / duckdb): a class, enumeration value or node of the same name in another namespace no longer passes (`( assoc rdf:type ) sh:in ( cim16:ACLineSegment cim17:ACLineSegment )` on data in a third CIM namespace), as pyshacl reports. A class the schema lacks (every class without an `rdf_map`) matches by its name, as `sh:targetClass` and `sh:class` do. A string literal member stays a literal, also when its text reads as an IRI (`sh:in ( "http://iec.ch/TC57/CIM100#Breaker" )` on `rdf:type` matches no class).
- **`locate` (SARIF / SHACL report locations) points a finding on a KEY at the file holding that KEY's element** (an SSH property of an object the EQ file defines), not at the first file defining the ID; the definition stays the fallback. A finding of an absent property (`sh:minCount`, `xsd:minOccurs`) is located at the definition and its KEY is not searched. `locate()`'s `keyLines` entries are `(uri, line, snippet)`, were `(line, snippet)`.
- **A row without a VALUE counts for no cardinality** (`sh:minCount` / `sh:maxCount`, `xsd:minOccurs` / `xsd:maxOccurs`, pandas / polars / duckdb): it states no triple, as pyshacl sees it.
- **SHACL count constraints count a triple stated in several instances once** (`sh:minCount` / `sh:maxCount`, every path form, pandas / polars / duckdb): value nodes are a set, as in the RDF merge pyshacl validates, and `enrich` lists each of them once in TARGET ("found 2 values: 'A', 'B'"). `validate_schema` counts every element, as XSD does: a property written twice with the same value breaks `xsd:maxOccurs` 1.
- **SHACL report `sh:resultPath` is the shape's own path** (sequence, inverse, alternative, …) as pyshacl writes it, not the path's first predicate.
- **SHACL report `sh:value` of a `sh:sparql` result has the `?value` binding's term kind**: a node on an `rdf:type` path is `urn:uuid:…`, not a class IRI; an inverse path's value is the node; a literal `?value` keeps its full text and stays a literal, also when it reads as a node or an IRI (`"urn:uuid:b1"`).
- **SHACL report `sh:focusNode` of a literal focus node** is the literal, not `urn:uuid:<text>`: `ID_KIND` is decided per row — an object of a `sh:targetObjectsOf` KEY has the term kind the export writes for it (an Attribute, or without a schema entry a text that is no IRI or UUID, is a literal), and of a shape with other targets too, an ID that is a subject in the data is a node; engines and pyshacl agree, the rows of the lexical supplement next to pyshacl are nodes.
- **`engine="pyshacl"` reports a reference or class of another namespace whole**, as the
  other engines do: VALUE `http://example.org/ns#vl1`, where the bridge read `vl1`. With
  an `rdf_map` it reads `sh:value` back exactly (`report_to_violations(rdf_map=…)`);
  without one the form is still guessed.
- **`engine="pyshacl"` no longer crashes on a shape pyshacl gives up on.** pyshacl returns
  its `ValidationFailure` in place of the report (a `sh:sparql` query it does not accept),
  and the run ended in `AttributeError: 'ValidationFailure' object has no attribute
  'objects'`. The run now returns one `triplets:validationFailure` Violation row with
  pyshacl's reason (a type of its own: pyshacl reports other causes than a query this
  way) and lists the reason in the run metadata's `skipped_shapes`, so the reports say
  the run is partial; pyshacl names no shape, so the row has no `SOURCE_SHAPE`. Like
  `triplets:invalidSparql`, the row is no data Violation for `has_violation`.
- **`sh:targetObjectsOf rdf:type` findings carry the class IRI as `ID`** on pandas, polars
  and duckdb, as pyshacl reports them (`http://iec.ch/TC57/CIM100#Bay`; it was the local
  `Bay`), and so do the findings of `sh:targetObjectsOf` on an enumeration KEY. A class
  or enumeration VALUE met as a focus node is the IRI it stands for, from every target
  and under every inverse path.
- **`cim-spreadsheet` keeps the values pandas reads as missing, and texts that start with
  `=`.** A VALUE `NaN` (a legal `xsd:float`), `NA`, `null`, `None`, `N/A` or `nan` was
  dropped on the way back from CSV and Excel, and a VALUE `=1+2` was written to Excel as
  a formula and lost. Only an empty cell is no value now (the CLI reads with
  `keep_default_na=False, na_values=[""]`), and `export_to_excel` writes a text that
  starts with `=` and the Excel error words (`#N/A`, `#REF!`, `#DIV/0!`) as text cells.
- **Every SARIF ruleId of a SHACL finding changes: it is now a function of the shape
  alone.** The ruleId was the shape's local name and the constraint, with a counter in
  order of appearance for shapes of one local name in several namespaces
  (`AllowedClasses-property/sh:in-11`: a fixed alert handed its ruleId to the next
  shape), and the blank-node label for a shape without an IRI (`n3f…b1/sh:minCount`, new
  at every parse). It is now `<local name>-<8 hex digits of the SHA-1 of the shape
  IRI>/<constraint>` (`AllowedClasses-property-1f0c9a2e/sh:in`) for every named shape,
  and `<nearest named shape>.<path>-<8 hex digits of the SHA-1 of that IRI and the shape's
  canonical definition>/<constraint>` for a shape without an IRI
  (`BreakerShape.IdentifiedObject.name-5c1d07aa/sh:minCount`;
  `attrs["validation"]["anonymous_shapes"]` maps the labels). The same in every run,
  parse, engine and row order, with or without findings of other shapes, for joined runs
  and frames without metadata (a blank-node shape needs the metadata). Every
  `triplets-shacl` alert on GitHub code scanning closes and re-opens once. Schema ruleIds
  (`triplets-rdfs`) are unchanged; `SOURCE_SHAPE` and `properties.sourceShape` are
  unchanged.
- **Every `sh:sparql` solution is a finding, with its `sh:message` filled in.**
  Solutions that differed only in a variable other than `?value` were reported once,
  and `{?name}` / `{$name}` in the message stayed as written (APL `ClassCount`: 9
  classes, 3 rows with the text `The class {?class} appears {?value} times …`). The
  placeholders are now replaced by the solution's values, as pyshacl writes them (an
  IRI whole, a literal's text; `{?value}` of a node shape's solution without `?value` is
  the focus node), on every engine, and identical findings of a
  `sh:sparql` constraint are told apart by the message too. `MESSAGE_SOURCE` of a
  filled message is `shacl`.
- **`sh:targetNode <class IRI>` with `sh:path [ sh:inversePath rdf:type ]` finds the
  class's instances** on pandas, polars and duckdb, as pyshacl does. The engines compared
  the class IRI with the local `Type` VALUE, so a `sh:minCount 1` on that path ("no
  TopologicalIsland instantiated") always failed. The node is matched when it is the IRI
  the VALUE stands for (the name's schema namespace, else `undefined_namespace`); the
  same holds for an enumeration value IRI under the inverse of an enumeration KEY.
- **The parser's `Distribution` and `NamespaceMap` objects are no longer validated as
  data.** Every engine, pyshacl included, checked the two objects the parsers add to each
  file: `sh:targetSubjectsOf rdf:type` shapes reported them as classes outside the
  profile, and dangling-reference queries reported the namespace IRIs of `NamespaceMap`.
  Their IDs are new at every parse, so those findings never matched the previous run.
  They are now left out of the validated rows (focus nodes, value nodes, the graph of
  `sh:sparql`, SPARQL targets and pyshacl) on every engine. The classes are
  `triplets.iri.METADATA_TYPES`, the ones `cim-diff` and `cim-spreadsheet` use
  (`triplets.cli.METADATA_TYPES` is the same object); validation leaves out only the
  objects the parsers wrote — unreferenced, a `Distribution` with a `label` and no KEY but
  `label` / `fileName` / `belongsToContainer` — so a `dcat:Distribution` of the data is
  validated. The schema validations never
  reported them: no schema defines the two classes.
- **`concat_violations` raises on a schema frame without run metadata joined with a SHACL
  frame.** Only the metadata was compared, so the result was one `triplets-shacl` run
  holding schema rules. The language of a frame without metadata is the one its rows show,
  as in the exports.
- **`concat_violations` joins a frame it already joined.** A joined frame of runs with
  two rule sets (so also the frame of `validate_by_header`) raised
  `TypeError: unhashable type: 'list'` when joined again. The result is now the same
  however the runs are grouped: `concat_violations([concat_violations([a, b]), c])`
  equals `concat_violations([a, b, c])`, rows and metadata. A joined frame keeps its
  runs' rule sets with their counts in `attrs["validation"]["rule_sets"]` (not
  exported); a single value meeting a list of distinct values joins the list, and
  `None` is no value (`profiles` of a run without `rdf_map_scope` no longer nests as
  `[None, [...]]`).
- **The run metadata is plain values: operations on a violations frame no longer
  deep-copy rdflib graphs.** `attrs["validation"]["source_shapes"]` held one
  `rdflib.Graph` per violated shape, which pandas deep-copies with `attrs` at every
  frame and column operation (55 to 115 ms each on a run with 82 violated shapes). It
  now holds the same definitions as N-Triples text per shape id
  (`triplets.validation.shacl_report.shape_triples(text)` reads one back, blank-node
  labels kept); the SHACL report embeds the same triples as before.
- **SHACL report: `sh:value` has the term kind of the data**, by the schema entry type of
  the KEY (`rdf_map=` on `export_to_shacl_report` / `violations_to_report_graph`): the
  referenced object's IRI for an Association (`<urn:uuid:…>`, an absolute IRI as it is),
  the enumeration value IRI for an Enumeration, the class IRI for a `Type` VALUE (also
  without `rdf_map`: in `undefined_namespace`, CIM100 by default) and for the class a
  `( association rdf:type )` path reports (told by the shape's definition in the run
  metadata). Every one was a string literal (`sh:value "00000000-…"`). An Attribute
  value, a value of a KEY the schema does not define, and every value but `Type` without
  `rdf_map` stay plain literals with the VALUE text, whatever the text looks like. The
  terms are the ones `export_to_nquads` writes: a reference VALUE that reads like a
  schema name is still `urn:uuid:<text>`, a KEY given as its IRI has the entry of its
  local name, a VALUE that is no text is written as its text.
  `report_to_violations(graph, rdf_map=…, undefined_namespace=…)` reads the values back
  exactly: an IRI becomes a local name only when it is the IRI that name stands for, so
  a reference or a class in another namespace comes back whole, with a `#` too
  (`http://www.w3.org/ns/dcat#Dataset`). Without `rdf_map` the reader guesses, and an
  `http` IRI with a `#` loses its namespace.
- **A violations frame without run metadata that holds schema findings is exported as an
  RDFS run**, in SARIF (`triplets-rdfs`, `RDFS …` titles, `[rdfs_…]` message tags) and in
  the SHACL report: the language is `rdfs` when every `SOURCE_SHAPE` is a schema shape
  (`urn:triplets:schema…`), where every such frame was labelled SHACL. `pandas.concat`
  drops the metadata of frames from different runs. A frame that mixes schema and SHACL
  rows stays one SHACL run: pass the frames as a list for one run each.
- **`parse()` reads a `.owl` file as RDF/XML**, as a path and as a zip member, like `.xml` and
  `.rdf`; it logged "Not supported file" and returned no rows. `cim-spreadsheet` takes a
  `.owl` input as CIM XML too. A file is told by the suffix of its file name
  (`.xml` / `.rdf` / `.owl`, any case), not by those letters anywhere in its path: a zip in
  a directory `j.owlett/` is a zip, `model.xml.zip` is a zip, and `model.xml.tmp` is no
  longer read. `.owl` is read as RDF/XML only; a `.owl` zip member that is not XML with a
  namespaced root element (an ontology in Turtle) is skipped with a warning, in any
  encoding XML declares.
- **CIM XML export: no "matches none of the schema profiles … nothing is exported" warning
  for a schema with root entries.** A flat schema that also holds a nested dict without a
  `type` (read as a profile section) exported every object and logged the warning once per
  file. The warning is for a schema with sections whose root entries define no class of
  the instance (a bundled schema with one added root attribute still warns).
- **`validate_schema_references` on the pandas engine no longer costs by the number of typed
  objects in every rule.** `Series.isin` on the pandas 3 `str` dtype converts every member
  of the ID list one by one at each call (about 40 ms for 15 000 IDs), and the reference
  rules made that call once or twice each with the ID of every typed object. The pandas
  engine now compares IDs as objects in every membership test (focus, `sh:class`,
  `sh:node`, `sh:closed`, `sh:nodeKind`, `rdfs:range`, `triplets:missing`), with or
  without pyarrow, and the pandas and polars engines build the typed-ID list once per
  run. 78 786 rows, 14 900 objects, 238 rules: pandas 5.0 s → 0.4 s, polars
  0.72 s → 0.26 s; findings unchanged.
- **CIM XML export: a reference VALUE that is an absolute IRI is written as it is**, as
  `export_to_nquads` writes it. For a reference key with an empty `value_prefix` the VALUE
  was only looked up as an enumeration name, so `rdf:resource="http://www.w3.org/ns/dcat#Dataset"`
  was dropped, or written as `http://triplets#http://…` with `export_undefined=True`; under
  a `value_prefix` the prefix was put before the IRI. An object ID that is an absolute
  IRI is written as `rdf:about="<IRI>"`, where it took the class's ID attribute and
  prefix (`rdf:about="urn:uuid:http://…"`). Both engines.
- **CIM XML export: a `Type` VALUE that names a schema entry which is not a `Class`** (an
  enumeration value used as `rdf:type`) is written in that entry's namespace, the
  `rdf:type` `export_to_nquads` writes, with `rdf:about="urn:uuid:<ID>"`. `python_lxml`
  raised `KeyError: 'attrib'`; `cython_pugixml` dropped it, or wrote it under
  `undefined_namespace` with `export_undefined=True`. An entry without a namespace is an
  undefined class. A KEY that names a `Class` entry, or an entry with an empty namespace,
  is an undefined key in both engines; `python_lxml` raised `ValueError: Empty tag name`
  for the empty namespace.
- **The CIM XML file name is the `label` of the instance's `Distribution` object** (the
  source file name the parsers write). It was the first `label` row of any object, so data
  with its own `label` key (`rdfs:label`) could name the file. An instance without a
  `Distribution` label, or with a null one (it named the file `nan` or raised `TypeError`),
  is written as `<uuid>.xml`. The other readers of the file name follow the same rule:
  the `label` column of `cgmes_tools.get_loaded_profiles`,
  `cgmes_tools.update_FullModel_from_filename`, `cgmes_tools.update_filename_from_FullModel`
  (it rewrote every `label` row of the instance, a data `rdfs:label` too) and the per-file
  names of `export_to_csv` (pandas and polars).
- **CIM XML export: a `Class` entry with `"namespace": ""` under a namespace map with a
  default namespace** stays in no namespace: the default namespace is then written under
  a generated prefix instead of `xmlns="…"`, where the element fell into it. Both engines.
- **`export_to_nquads(engine="pandas")` runs without pyarrow** (the core install: pandas and
  lxml). It imported pyarrow unconditionally; the lines are still joined in Arrow when
  pyarrow is installed, else in pandas.
- **`cython_pugixml` CIM XML export: a schema namespace the file's namespace map does not
  bind.** Elements of that namespace were written without a prefix, so in no namespace —
  e.g. every `cim:` class and key of a file binding `cim:` to CIM16 exported against a
  CIM100 schema, or one `entsoe:` key in a file that does not declare the prefix. The
  engine now writes them under a generated prefix (`ns0`, `ns1`, …, numbered in order of
  first use) declared on the `rdf:RDF` root, only when used; the names equal those of the
  `python_lxml` engine. The
  root element and the `rdf:` attributes take the prefix the map binds to the RDF
  namespace, where the root was always written as `rdf:RDF`; a map without the RDF
  namespace gets `xmlns:rdf` declared, where the `rdf:` prefix was left undeclared.
- **CIM XML export with a default namespace** (a namespace map prefix `""`, as parsed from
  a file with `xmlns="…"`, or `None`). `cython_pugixml` wrote `xmlns:=` / `xmlns:None=`,
  not well-formed or with the elements in no namespace; it writes `xmlns="…"` and
  unprefixed elements. `python_lxml` raised `ValueError` on the prefix `""`; it reads it as
  the default namespace. An attribute never takes the default namespace: with the RDF
  namespace as default, `rdf:about` / `rdf:ID` / `rdf:resource` get a generated prefix
  (both engines).
- **`python_lxml` CIM XML export: the root is `RDF` in the RDF namespace** whatever the
  namespace map binds the `rdf` prefix to; it was written in the namespace of that prefix.
  Both engines bind the RDF namespace to a prefix of its own when the map has none for it
  (`rdf`, or the first free `ns0`, `ns1`, … when `rdf` is taken).
- **`sh:in` in the pandas, polars and DuckDB engines compares a VALUE that is not an IRI as
  it is.** The VALUE was shortened like an ID first, so a literal `_2` — or `#1` — did not
  match the member `"_2"` / `"#1"` and was reported; pyshacl accepts it.
- **`cgmes_tools.get_filename_from_metadata` accepts a metadata key without a prefix**
  (`keyword` on a FullModel header extended with the Dataset keys): it raised
  `IndexError`, also through `update_filename_from_FullModel` (pandas and polars). Such a
  key keeps its name and can be used in the `filename_mask`.
- **`cgmes_tools.validate_by_header` reports the schema file name.** The run metadata
  `references` held `"rdf_map"` for a schema given as a path; they hold the file name, as
  with `validate_schema`, and `"rdf_map"` for a dict. A run where no instance resolves a
  profile has `references` too. In SARIF, a tool finding without a location takes that
  file name as its artifact, where it took `rdf_map`.
- **The order of a run's shape file names does not depend on earlier runs.** A second
  `compile` / `validate_shacl` of the same shape files given in another order returned the
  first call's order in `references` (`CompiledShapes.sources`). The names are sorted, each
  name once: two files with one base name in different directories report one name. In
  SARIF, a tool finding without a location takes the alphabetically first shape file as its
  artifact, where it took the first one given.
- **An open file handle in `sources=` is read from its start by every locate pass**
  (`locate`, `to_sarif`, `to_shacl_report`) — a seekable handle is rewound before it is
  read. Before, the second pass over one handle read nothing: a second export had no line
  positions, without an error. A file-like source that gives no bytes (a stream that cannot
  be rewound, in its second pass) is named in a logged warning.
- **SARIF / SHACL report / CSV / Excel: a `PROFILE` cell holding a numpy array** (e.g. a
  list column after a polars → pandas conversion) is read as its profiles — before, an
  array of length 0 or ≥ 2 raised `ValueError` and a 1-element array was written as
  `['EQ']`; null and empty-string profiles are dropped.
- **Shape files with the same content report their own names.** `compile` caches on the
  shapes' content hash and their file names, so a run's `references` (SARIF
  `properties.references` and whole-file locations, `dcterms:references` in the SHACL
  report) are the files it was given; `compiled_hash` stays the content hash. A schema
  compile follows the same rule: the `rdf_map` file name is in the cache key, not in
  `compiled_hash`, so `concat_violations` counts one schema under two names once.
- **`export_to_cimxml` without an `rdf_map` exports the whole dataset.** With no schema
  every class and key is undefined, so the default `export_undefined=False` wrote files
  holding only an empty `rdf:RDF` root, with no warning. Without a schema,
  `export_undefined` is now treated as `True`: everything is written under
  `undefined_namespace`, both engines, and one warning says so. With a schema, both
  `export_undefined` values behave as before. `cim-spreadsheet to-cim` without
  `--rdf-map` relies on this and no longer logs its own warning.
- **A datatype entry's `type` is its datatype stereotype, not one of its other
  stereotypes.** The generator kept a single `cims:stereotype` row per object, so a
  datatype with two stereotypes could get the wrong one as its `type`:
  `DateTimeStamp` had `"eumd"`, and other datatypes had `profcim`, `European`, `rdf` or
  `new`. `type` is now the first of `Primitive`, `CIMDatatype`, `Compound`,
  `ConstrainedPrimitive` and `Enumeration` among the stereotypes. In the bundles this
  sets `DateTimeStamp`, `IRI`, `URI`, `StringIRI`, `StringFixedLanguage` and
  `LangString` to `Primitive`, `Statement` / `Statements` to `Compound`, and
  `CountPerArea` / `Irradiance` to `CIMDatatype`. If a datatype has none of these
  stereotypes, its `type` is `""` and a warning is logged.
- **A range bound that is not a number no longer fails the SHACL compile**: a
  `sh:minInclusive` … `sh:maxExclusive` bound such as `"2020-01-01"^^xsd:date` raised
  `ValueError` in `compile()`, so no shape was validated. pandas, polars and duckdb
  compare a date or dateTime bound (see Added), checked as rdflib reads it: an ill-typed
  bound rdflib can read (`"2020-06-01"^^xsd:dateTime`) is compared as its value; a bound in
  no value space (a string, an `xsd:time`, a number, date or dateTime rdflib cannot read)
  skips that one constraint, named in `skipped_shapes`; `engine="pyshacl"` evaluates it.
  A non-literal range bound, and a `sh:minCount` / `sh:maxCount` /
  `sh:minLength` / `sh:maxLength` value that is not a non-negative `xsd:integer`, make the
  shape malformed (skipped with the reason, also for pyshacl) instead of failing the compile
  or pyshacl's run ([#215](https://github.com/Haigutus/triplets/issues/215)).
- **`references_from` / `references_to` end on reference cycles** (pandas, polars and
  duckdb): each reference (`ID_FROM` → `ID_TO`) is followed once, at the lowest level it is
  reached, and the walk stops when a level adds nothing new, so the result is bounded by
  the data for any `levels`. Before, pandas and polars doubled the rows per level on a
  cycle and duckdb walked it up to `levels`, so `draw_references_from` /
  `draw_references_to` (`levels=99`) ran out of memory. All three engines now return the
  same rows: an object referenced by several keys of one object appears once (pandas and
  polars repeated it per key), an object referring to two reached objects appears once
  per reference (pandas and polars kept one), and the referrers of an ID without triplets
  of its own are found (duckdb did, pandas and polars returned none)
  ([#221](https://github.com/Haigutus/triplets/issues/221)).
- **A value that is not a number fails every range component** (`sh:minInclusive`,
  `sh:maxInclusive`, `sh:minExclusive`, `sh:maxExclusive`) in the pandas, polars and duckdb
  SHACL engines: a value that does not cast to a double (`"abc"`, `"1,5"`, `""`) or is `NaN`
  is reported, as SHACL's comparison is not true; before, it passed unless the shape also
  declared `sh:datatype`. The polars engine strips surrounding whitespace before the cast
  (`" 5.0"` is a number), like pandas and duckdb
  ([#186](https://github.com/Haigutus/triplets/issues/186)).
- **`sh:alternativePath` over different properties is skipped** (pandas, polars and duckdb
  SHACL engines) with an "unsupported sh:path form" `skipped_shapes` entry; before, it was
  reduced to its first inverse member and the other alternatives were dropped. Members that
  name one property in several namespaces still compile to that one KEY
  ([#178](https://github.com/Haigutus/triplets/issues/178)).
- **`sh:flags` on `sh:pattern` in the pandas, polars and duckdb SHACL engines**: the flags
  fold into the compiled regex (`q` quotes the pattern, `i` `m` `s` an inline prefix); a flag
  outside `i m s q` skips the shape (`skipped_shapes`). Before, the flags were ignored
  ([#177](https://github.com/Haigutus/triplets/issues/177)).
- **Several `sh:message` values: MESSAGE carries all of them**, sorted and joined by a
  newline, in the pandas, polars and duckdb SHACL engines and in pyshacl results; before,
  the engines reported one of them
  ([#185](https://github.com/Haigutus/triplets/issues/185)).
- **`sh:datatype` checks `xsd:dateTimeStamp`, `xsd:duration`, `xsd:time` and `xsd:gMonthDay`**
  (pandas, polars and duckdb SHACL engines): their values were never checked
  ([#149](https://github.com/Haigutus/triplets/issues/149)).
- **Any `xsd:type` is written as the literal's datatype** (`iri.datatypes`): a prefixed
  name (`cim:ActivePower`, prefixes from the schema's `ProfileNamespaceMap`, `xsd` / `rdf` /
  `rdfs` built in) or an absolute IRI is expanded and written by the N-Quads export (pandas,
  polars, streaming), hence the rdflib / pyshacl loader and the qlever ingest, and by the
  `python_lxml` CIM XML export with `datatypes=True`; before, only `xsd:*` values were
  written and any other was silently dropped. A prefix no `ProfileNamespaceMap` binds raises
  `ValueError` naming the KEY. New flat map `iri.prefixes(rdf_map)` and scalar
  `iri.absolute_datatype(key, text, prefixes)`; the scoped validation schema keeps the
  root's and every scoped profile's prefixes, bound as in the full schema
  ([#158](https://github.com/Haigutus/triplets/issues/158)).
- **`validate_schema` checks a non-xsd `xsd:type` by its xsd base type**: `cim:ActivePower`
  or a full IRI (an XSD one too) was compiled as is and checked nothing. The type is now
  expanded by the schema's prefixes; a non-XSD one takes the `xsd:type` of the schema's own
  datatype entry (`dataType`, else the IRI's local name), and without one is listed in
  `skipped_shapes`; a plain-literal type (`rdf:langString`) is neither checked nor listed.
  Profile merges compare the expanded types, so `xsd:float` and its full IRI are no
  conflict, nor are `""` and a missing `xsd:type`
  ([#158](https://github.com/Haigutus/triplets/issues/158)).
- **An object typed in several instances counts once on an `( assoc rdf:type )` path**
  (pandas, polars and duckdb SHACL engines): its Type rows are a set, as in pyshacl; before, each
  instance's Type row added the type again, a false `sh:maxCount`.
- **pyshacl engine `data_scope` loads only the scoped rows**: the frame is filtered to the
  scoped INSTANCE_IDs before the N-Quads export, as the vectorized engines do; before, the
  whole dataset was exported and parsed and the scoped named graphs copied out of it.
  CGMES 2.4 RealGrid (1.15M triplets), one SV instance (69k rows): 30.2 s / 3.3 GB peak
  → 2.6 s / 0.9 GB; the EQ instance (892k rows): 58.8 s / 4.3 GB → 37.2 s / 3.1 GB — the
  same as validating the pre-filtered frame. A DuckDB connection is filtered SQL-side.
  Each scope is its own entry in the in-process rdflib dataset cache; with
  `store="oxigraph"` a scoped run loads its own store of the scoped rows.
- **`validate_shacl(..., lexical=False)` skips the `triplets:lexicalForm` check on every
  engine**: pandas, polars and duckdb no longer emit the narrower-form Warnings (nested
  datatype checks included) — the datatype check skips the second pattern; values outside
  the lexical space stay `sh:datatype` violations. pyshacl gets no supplement, as before.
- **pyshacl engine report**: the nested `sh:detail` results of a `sh:node` finding are no
  longer listed as findings of their own; a blank-node `sh:resultPath` (inverse, sequence or
  alternative path) reads as the KEY it resolves to, not the blank-node label; the
  qualified-value, `sh:uniqueLang` and `sh:languageIn` components read as `sh:qualifiedMinCount`
  etc.
- **Every value of a repeatable SHACL parameter is evaluated** on polars, pandas and
  duckdb: a second `sh:node` shape, `sh:or` / `sh:and` / `sh:xone` list, `sh:not`
  shape or `sh:pattern` / `sh:class` / `sh:hasValue` value was dropped without a
  warning; each value is now a constraint of its own, as in pyshacl
  ([#163](https://github.com/Haigutus/triplets/issues/163)).
- **`sh:deactivated` on a `sh:sparql` constraint** switches it off on polars, pandas and
  duckdb, as in pyshacl; the constraint node is in the row's lineage, so
  `deactivated_shapes=` can name it too
  ([#184](https://github.com/Haigutus/triplets/issues/184)).
- **A malformed shape is skipped, the rest validates** on polars, pandas, duckdb and pyshacl:
  a shape that fails one of these load checks — a parameter taken once (`sh:path`,
  `sh:minCount`, `sh:maxCount`, `sh:datatype`, `sh:nodeKind`, `sh:in`, `sh:minLength`,
  `sh:maxLength`, `sh:closed`, `sh:deactivated`, `sh:uniqueLang`, `sh:languageIn`,
  `sh:qualifiedMinCount`, `sh:qualifiedMaxCount`) given twice, an empty `sh:or` /
  `sh:and` / `sh:xone` list, a non-literal `sh:deactivated`, `sh:ignoredProperties`
  without `sh:closed`, a declared `sh:PropertyShape` without `sh:path` — is named in
  `skipped_shapes` with the reason (an untargeted one too) and not evaluated; the vectorized engines evaluated
  it (polars merged two `sh:in` lists into one), pyshacl refused the whole shapes graph.
  A logical operator over a malformed shape is dropped and named, as over any skipped
  part; a `sh:node` row whose target has nothing left but skipped parts is too (it
  conformed on every node).
- **A nested `sh:datatype` decides on the lexical space only** on polars, pandas and
  duckdb: inside `sh:and` / `sh:or` / `sh:xone` / `sh:not` / `sh:node` a narrower form
  (`"1"` for `xsd:float`) counted as non-conforming — `sh:and` / `sh:or` / `sh:xone` /
  `sh:node` reported it, `sh:not` lost the finding pyshacl reports. The `triplets:lexicalForm` Warning
  stays on a property shape's own `sh:datatype`
  ([#172](https://github.com/Haigutus/triplets/issues/172)).
- **`sh:closed` allows the properties of every `sh:ignoredProperties` list** on
  polars, pandas and duckdb, as pyshacl; one of two lists was read and the
  properties of the other were reported. A property shape skipped for two
  `sh:path` values keeps both paths declared.
- **`sh:and` reports one row per non-conforming focus node** (node shape, no `KEY`)
  **or value node** (property shape), with the operator's shape, severity and
  message — it listed every nested finding with the nested `KEY` and shape
  ([#164](https://github.com/Haigutus/triplets/issues/164)).
- **A `sh:or` / `sh:and` / `sh:xone` / `sh:not` member with its own `sh:path`
  inside a property shape is checked on the value nodes**, not on the focus node:
  the engines flagged the wrong focus ([#166](https://github.com/Haigutus/triplets/issues/166)).
- **`sh:property` on a property shape reports the inner constraint on the value
  node** (SHACL §4.7.2), as pyshacl: it was one `sh:node` row on the referring
  focus. Under a `sh:node` target or a logical member the property shape is still
  judged as a whole (one `sh:node` row per value node). New IR component
  `sh:property`.
- `sh:node` on a node shape is evaluated by the polars, pandas and duckdb engines: one
  `sh:node` finding per non-conforming focus node, as pyshacl
  ([#160](https://github.com/Haigutus/triplets/issues/160)).
- **CIM XML export uses every profile the instance header declares**
  ([#128](https://github.com/Haigutus/triplets/issues/128)), merged like `rdf_map_scope`
  (both engines). A CGMES 3.0 EQ file declaring EQ, OP and SC was exported against EQ
  only: the OP and SC keys (`ACLineSegment.r0`, …) were dropped, or written as
  `triplets:` with `export_undefined=True`. A class concrete in any declared profile
  is instantiated (`rdf:ID` in ED1), whatever the order of the header rows.
  `resolve_instance_config` returns the merged schema (its `ProfileMetadata` fields are
  lists). An instance that matches no profile of a sectioned schema now warns, naming the
  file and the schema's profiles, also without a header (it exported an empty file silently).
- **A flat schema (entries at the root, no profile sections) fills the IRI maps**
  ([#128](https://github.com/Haigutus/triplets/issues/128)). `export_to_nquads` wrote
  every name under `http://triplets#` without datatypes, and `sparql.query`,
  `read_nquads(rdf_map=)` and the SHACL report got no namespaces. A root dict with a
  `type` string is an entry, one without is a section; a name both define takes the root entry.
- **Node-level `sh:or` / `sh:and` / `sh:not` on a NodeShape are compiled** (they
  were silently skipped, not even listed in `skipped_shapes`); a logical member
  or `sh:node` shape carrying its own `sh:property` (or `sh:closed`) is
  expanded instead of compiling to an empty, always-satisfied alternative. One
  walker now compiles targeted, referenced and member shapes alike.
- **`sh:qualifiedValueShape` / `sh:qualifiedMinCount` / `sh:qualifiedMaxCount`
  (and `sh:uniqueLang`, `sh:languageIn`) are reported in `skipped_components`**;
  a logical operator over such a member, over a bare IRI with no shape, or over
  a member with a part already skipped (a dropped operator, an inexpressible
  `sh:path`) is skipped and named in
  `skipped_shapes` instead of being judged on its expressible part (which fired
  on every focus node). A value constraint on a node shape itself (`sh:class`,
  `sh:in`, … judging the focus node) is named in `skipped_shapes`; it was
  silently ignored. `sh:and ( )` no longer raises.
- pyshacl findings without a property path leave `VALUE` empty, as the other
  engines do (pyshacl's `sh:value` there is the focus node, already in `ID`).
- SHACL report `sh:resultPath` takes the schema namespace with `rdf_map=`:
  regression test ([#133](https://github.com/Haigutus/triplets/issues/133)).
- **polars tableviews return rows in a fixed order**, sorted by ID like pandas
  (`type_tableview`, `key_tableview`, `id_tableview`). Row order changed from call to
  call, so float sums over a view, such as `scale_load`, differed in the last digits.
- **DuckDB 2.0 support** (1.5.3 still works). Results are fetched in full before
  the next call on the connection, `tableview_to_triplets(multivalue=True)` uses
  `lambda x:` instead of the removed `x ->`, and Arrow exports read relations
  (no extra copy).
- **duckdb `references` / `references_simple`:** an object that references
  itself (e.g. by `IdentifiedObject.mRID`) keeps level 0, like pandas.
- **NC instances resolve their profile from `dcterms:conformsTo`**, not only from
  `dcat:keyword` ([#142](https://github.com/Haigutus/triplets/issues/142)). Every
  bundle section now has `ProfileMetadata.versionIRI` (the RDFS vocabulary IRI; CGMES
  2.4.15: its ENTSO-E profile URI constant, `entsoeURI` / `entsoeURIcore` / `europeanProfileURI`, was missing) and `profileVersionIRI` (the profile IRI headers
  declare: from the optional PROF descriptors in `rdfs/<bundle>/PROF/`, else the
  `versionIRI`), and profile resolution matches both. NC: `https://ap.cim4.eu/<Profile>/<ver>`
  vs `https://ap-voc.cim4.eu/...`; CGMES: the same IRI. A dataset whose keyword names its content
  (ReliCapGrid `CommonData`, `FAP`) was exported against no profile and lost all rows.
- **`scale_load`** works without `NonConformLoad` objects (was `TypeError`).
- **`get_metadata_from_filename`** parses the file name, not the whole
  `label` path: a directory with `_` broke the split and
  `update_FullModel_from_filename` raised `KeyError: 'ID'`.
- Tests: the CGMES SHACL shapes come from the new `test_data/CGMES-AP`
  submodule (was a hard-coded local path, so those tests always skipped); CI
  runs the suite once more with the cython extensions built.
- **A namespace IRI VALUE is kept whole.** `local_value` (all parsers, `iri_pandas`,
  `iri_polars`, `iri_duckdb`) turned `<dcterms:conformsTo rdf:resource="http://iec.ch/TC57/CIM100#"/>`
  into `""`: an `http` IRI with `#` and nothing after it has no local name.
- Tests use the ReliCapGrid `cgmes-3.0_ncp-2.5_tc-2.0` release (was a February
  commit); the NC round trip runs against the NCP 2.5.0 bundles. Instances that cannot
  round-trip yet are strict xfails linked to their issue, not skipped.
- **`cim-spreadsheet` leaves out the parser metadata** (`NamespaceMap`,
  `Distribution`, which holds the source path) like `cim-diff`, with the same
  options from one shared definition in
  `triplets.cli` ([#132](https://github.com/Haigutus/triplets/issues/132)).
  Such a spreadsheet converts back to `<uuid>.xml` with the schema profile's
  namespaces.
- **Same exclusion options in both CLIs.** `-ex TYPE` leaves out more classes
  (repeat or comma-separate); `--keep-metadata` keeps `NamespaceMap` and
  `Distribution`. `--no-default-exclusions` is removed. `cim-spreadsheet` applies `-ex` to
  to-cim too. `cim_to_spreadsheet()` leaves out nothing by default; pass
  `exclude_objects=triplets.cli.METADATA_TYPES` for the CLI behaviour.
- **`cim-diff` exit code**: 0 when equal, 1 when different, 2 on error.
  `print_triplets_diff` returns the number of differing triplets.
- **One diff report for all engines.** pandas, polars and duckdb
  `print_triplets_diff` print the same output (they had three formats; duckdb
  ignored `exclude_objects`). Hunks and keys are sorted, `-` before `+`.
  polars `diff_triplets` casts `ID` / `KEY` / `VALUE` to String when one side
  is Categorical.
- **CLI logging at WARNING**; `cim-spreadsheet` no longer prints INFO lines.
- **to-cim without `--rdf-map`** warns and writes the classes in the triplets
  namespace; it wrote nothing and still reported success.
- **CIM XML export creates missing folders** for a relative file name (round
  trip with `--keep-metadata` crashed with `FileNotFoundError`).
- **`print_triplets_diff` `exclude_objects` leaves out changed objects too**:
  it matched only removed and added ones. File names are read before
  filtering. `file_id_object=None` keeps `Distribution` in the diff. The
  unpackaged `tools/cim-diff.py` script is removed (use `cim-diff`).
- **Missing VALUE is `""` in `diff_triplets`** (all engines): it matches a
  missing or `""` VALUE and is returned as `""`. duckdb dropped such rows,
  polars reported them as removed and added.
- **`filter_triplets_by_type` returns each row once** (pandas, polars): a type
  declared in several files repeated its objects' rows. pandas is about 3x
  faster.
- **`cim-diff` runs again**: it called `rdf_parser.print_triplets_diff`, which
  does not exist; it now uses `parse` and `tools.print_triplets_diff`.
- **`comment=` is written by the `python_lxml` CIM XML exporter** too: it
  serialized the root element only, so the comment before it was dropped
  ([#131](https://github.com/Haigutus/triplets/issues/131)).
- **Export schemas record inheritance and ranges as absolute IRIs.** The
  generator resolves a relative ``rdf:resource="#Name"`` against the profile's
  ``xml:base`` (RDF/XML semantics) instead of copying it, so a parent or range
  in another namespace is stated (`https://cim.ucaiug.io/ns#IdentifiedObject`
  for an NC class) rather than reconstructed by each consumer from the child's
  namespace. Enumeration ranges were mangled by a ``replace("#", "")``
  (`https://cim4.eu/ns/ncBalancingReserveKind`, 107 entries in NC 2.5); all
  bundles regenerated — only `inheritance` and `range` values change.
- **`cim-spreadsheet` back to CIM XML** ([#125](https://github.com/Haigutus/triplets/issues/125)):
  the to-cim direction failed for Excel and CSV (`rdf_parser.tableviews_to_triplets`
  does not exist); it now uses `triplets.tools.tableviews_to_triplets`. Spreadsheets
  are read as text, so `false` no longer comes back as `False`.
- **Exclude pandas 2.3.3**: `pivot()` on ArrowDtype dictionary columns still
  crashes with `'Series' object has no attribute '_pa_array'` (same bug as
  2.2.x, which is already excluded). Constraint is now
  `pandas>=2.0,!=2.2.*,!=2.3.3`. Fixed upstream in pandas 3.0.
- N-Quads / SPARQL ingest expand class and enum IRIs from the schema namespace
  when `rdf_map` is passed (`Class.namespace` / `EnumerationValue.namespace`);
  CIM100 is only the no-schema fallback. The inverse (`read_nquads`, CONSTRUCT,
  SHACL report) shortens any http(s) `#fragment`, not just CIM100, so CGMES 2.4
  CIM16 round-trips to the same short names as 3.0 (#116).
- The qlever and cython extensions are built against the uv venv's pyarrow
  (`uv run --no-sync` inside the pixi task; pixi supplies only the C++
  toolchain), so one Arrow runtime is loaded. A qlever extension linked against
  conda's libarrow segfaulted on the first index build under a different wheel
  pyarrow. The pixi test tasks and the conda pyarrow/test environments are gone:
  tests run in the uv venv only.
- Source builds in a conda/pixi environment: the cython extensions link
  `libarrow_python` behind `--no-as-needed`, so the module imports instead of
  failing with undefined `arrow::` symbols (conda toolchains default to
  `--as-needed`; pip-wheel builds never hit this).

## [0.2.0] - 2026-08-26

First stable release of the 0.2 line — identical in content to 0.2.0rc6.
Everything new since 0.1.0 is in the 0.2.0rc1–rc6 sections below: one engine
registry with shared flavor conversion, first-class DuckDB and Arrow support,
SHACL and schema validation with SARIF reporting, and the loaded-profiles
model inventory API.

## [0.2.0rc6] - 2026-08-26

### Added
- `cgmes_tools.get_loaded_profiles(data, profile_keys=...)` —
  tidy inventory of the profiles each loaded instance declares
  (`INSTANCE_ID | label | HEADER | HEADER_ID | KEY | VALUE`),
  key-driven across old (FullModel) and new (dcat:Dataset) headers: the
  header class is reported verbatim, never matched.
- `cgmes_tools.get_model_relations(data)` — dependency edges between
  model-part headers (`Model.DependentOn` / `requires`), resolved across
  header generations (a dcat:Dataset requiring a FullModel resolves like
  any other); `INSTANCE_ID_TO` is NA when the referenced part is not
  loaded.
- The shared header vocabulary is public: `cgmes_tools.PROFILE_KEYS`,
  `REFERENCE_KEYS`, `HEADER_TYPES` (one definition, also used by
  `validate_schema` and the cimxml export resolver).

### Fixed
- `validate_schema` on a DuckDB connection with a non-default table/schema
  (`connect(table=...)`, `set_triplets_table`, `table=`/`schema=` kwargs):
  instance discovery and the describe helpers now resolve the relation the
  same way the duckdb engine does instead of assuming `triplets`.
- `validate_schema(..., context=True)` kept discarding the enrichment
  columns; enrichment now runs once on the merged result and its columns
  survive.
- Legacy CGMES 2.4 profile-URL fallback now also resolves DL, DY, GL, FH
  instances and EQ files declaring only EquipmentOperation /
  EquipmentShortCircuit.
- Profile-identity resolution ignores identifiers shared by several schema
  sections (e.g. the IEC document URN every CGMES 3.0 section carries as
  `conformsTo`) — a shared identifier identifies no profile, so it can no
  longer match the wrong section.

### Changed
- `cgmes_tools.get_loaded_models` works across both header generations:
  models anchor on graph roots — loaded headers nothing loaded depends on
  (the SV part of a power-flow set, the process instance of a network-code
  set) — instead of the hardcoded CGMES 2.4 StateVariables URI (which
  returned {} for 3.0 and dcat:Dataset data). Optional `root=` restricts
  roots by profile identity ("SV" also matches the 2.4/3.0 URIs via the
  legacy section map); dependency cycles terminate.
- `type_tableview` accepts a sequence of types as well as a single string
  (a string is treated as a one-item list); several types pivot into one
  table with the union of columns.
- `cgmes_tools.get_loaded_model_parts` covers both header generations: it
  selects `header_types=("FullModel", "Dataset")` (parameterizable) and
  pivots the union of their keys. Header metadata now stays text
  (`string_to_number=False` default — `Model.version` "1" is no longer
  converted to 1.0). Repeated keys (`Model.DependentOn`, `requires`) keep
  the first value; `multivalue=True` returns them as lists (pandas/polars
  input — list cells cannot convert back to arrow).
- `validate_schema` runs one raw engine pass per (instance, profile) and
  builds the presentation (TARGET/EXPECTED/enrichment) once on the merged
  result instead of per pair.
- The 0.1 deprecation shims (`rdf_parser`, old `rdfs_tools`/`cgmes_tools`
  names) stay in 0.2; they are under consideration for removal in a future
  version (0.1.0 announced removal in 0.2 — superseded).

## [0.2.0rc5] - 2026-08-17

Schema-validation semantics: per instance, per declared profile —
profiles are never merged; mRID validated per each profile's own rule.

### Changed
- **Schema validation runs per instance, per declared profile — profiles are
  never merged**: `compile_schema(rdf_map)` compiles every profile contained
  in the schema JSON separately (a `CompiledSchema` set, addressable by each
  profile's declared identity: versionIRI / conformsTo URI / keyword /
  section key); `validate_schema` resolves each INSTANCE_ID's declared
  profiles from its header (`conformsTo`, `Model.profile`,
  `Model.messageType`, `keyword`; legacy 2.4 URL fallback) and validates the
  instance against each declared profile on its own. One instance may
  declare several profiles — each runs separately. `profiles=` overrides
  resolution (header-less/legacy data); unresolved instances are skipped and
  reported in the coverage metadata. Violations carry a `PROFILE` column,
  reports a `[rdfs_profile]` entry, SARIF titles read
  `RDFS <profile> <Class> <attr> (N×)`.
- **mRID cardinality is validated per schema again** (reverts part of
  0.2.0rc4's #101 fix): the schemas are profile-accurate — CGMES 2.4
  declares `IdentifiedObject.mRID` 0..1 (not serialized), CGMES 3.0 and NCP
  1..1 (element expected) — and per-instance runs make it correct on model
  sets: each instance's mRID is counted against its own profile's rule, so
  no cross-profile duplication (the union-frame maxOccurs false-positive)
  and no cross-profile satisfaction (#101's minOccurs false-positives were
  per-file scoping, now the defined semantics).

## [0.2.0rc4] - 2026-08-17

Validation correctness (issues #99-#101) and self-contained errors:
every finding is fixable from the report alone.

### Fixed
- **Identical shape re-declarations behave like one shape** (#99): the
  ENTSO-E Simple files re-declare shared property-shape URIs per profile
  file — the duplicated IR rules double-counted in the polars batch path
  (`sh:maxCount` false positives on merged shape sets). The IR is deduped at
  compile.
- **`validate_schema` range checks accept multi-typed targets** (#100): a
  referenced object conforms when ANY of its rdf:type values is in the
  expanded range set (RDF types are cumulative — SSH/TP re-type EQ objects).
  New vectorized `triplets:range` component in all three engines; dangling
  references stay silent (cross-profile sets); `sh:value` is now the
  reference itself, the found type stays in `TARGET`/`[context_message]`.
- **`validate_schema` skips the mRID minOccurs check** (#101): IEC 61970-552
  maps `IdentifiedObject.mRID` to the rdf:ID/about attribute (stated in the
  profile RDFS) — every parsed object carries it as the ID column.
  maxOccurs/datatype still apply to explicit elements.
- **Errors are self-contained** — understandable and fixable from the report
  alone: the offending value is in the message text (`[context_value]`; SARIF
  grouped examples pair each object with its bad value), `sh:maxCount`
  findings list the actual duplicate values, and reference findings name the
  target's id, Type and IdentifiedObject.name (or that it does not exist).
  Long grouped observation lines are capped at 3 distinct + count.
- **SARIF alert titles are always human-readable**: rules without a shape
  `sh:name` get a synthesized title instead of GitHub dumping the raw
  message text — schema runs: `RDFS <Class> <attr> (N×)` (per-property rule
  granularity); unnamed SHACL shapes: `<attr> <violation type> (N×)`.

## [0.2.0rc3] - 2026-08-17

Field-feedback round (relicapgrid PROF validation) plus schema-based
validation: validate data straight against the export schema.

### Added
- **Schema-based validation** (`triplets.validation.validate_schema(data,
  rdf_map)`, `compile_schema(rdf_map, closed=False)`,
  `data.shacl.validate_schema(...)`): cardinality, datatype lexical checks,
  enumeration membership and association ranges straight from the export
  schema — the same vectorized engines (polars/pandas/duckdb, precompiled
  plans) as SHACL, without rdflib. Results are presented in the schema's own
  vocabulary, not fake SHACL: `xsd:minOccurs`/`xsd:maxOccurs`/`xsd:type`,
  `rdfs:range` (enums + association targets, abstract ranges expanded to
  concrete subclasses via inheritance), optional `schema:domainIncludes`
  (closed=True — the APL rdfs:domain/domainIncludes convention: external
  properties attach to classes non-exclusively);
  message tags follow the constraint language (`[rdfs_expected]`, …).

### Fixed
- **SARIF: every result gets a displayable location** (GitHub rejects
  location-less results): null-ID rows keep their stamped
  `SOURCE_URI`/`SOURCE_LINE`; rows with nothing fall back to a whole-file
  artifact location (`region.startLine 1`) — the shapes file for
  `triplets:*` tool findings, else the first data file.
- **Exports skip phantom empty instance groups**: pandas groupby on the
  parser's ArrowDtype dictionary `INSTANCE_ID` yields every dictionary value
  on filtered frames (`observed=True` is Categorical-only) — documented in
  docs/parsers.md with recipes; upstream pandas issue drafted.
- **pyarrow ABI pairing pinned**: build-system pins `pyarrow==25.0.1`,
  runtime extras require `>=25.0.1,<26` (floor = build version, cap = next
  libarrow soname family), and wheels bake the build version — the cython
  engines raise a clear upgrade error instead of crashing when the runtime
  pyarrow is older than the build.
- **`scope=` documented truthfully**: it is a named-graph (instance) filter —
  out-of-scope data is not loaded, references into unscoped instances count
  as absent; include dependency instances in the scope (or validate the full
  union) for cross-instance checks. (Scoping focus nodes instead was
  considered and declined — scope means named-graph filtering.) The pandas
  scope filter stays `isin` — measured 3x faster than a merge join on
  dictionary columns.

### Changed
- **Per-file shape-graph cache**: shape files are parsed once per content;
  overlapping shape unions (per-area NC sets) reuse the parsed graphs, and
  the compile-cache key is now order/duplicate-insensitive. Unions always get
  a fresh graph (cached graphs are never aliased — pyshacl may mutate the
  shapes graph it receives). `CompiledShapes.merge()` / per-file IR caching
  was considered and declined: `sh:node`/`sh:or` targets and `sh:closed`'s
  resolved KEY list are whole-graph computations, so merging per-file IRs is
  unsound.

## [0.2.0rc2] - 2026-08-13

Validation-report quality release: reports carry everything needed to fix
the model from the report alone, in both formats.

### Changed
- **Report messages: one self-describing `[source_kind]` entry per fact,
  raw text verbatim**: `[shacl_message]` (authored `sh:message`) or
  `[engine_message]` (engine-worded — exactly one of the two per result),
  `[shacl_expected]` (the constraint's requirement worded from the IR
  parameter — present even on minimal runs), `[shacl_description]`,
  `[context_message]` (the referenced object's actual type / dangling
  reference — the post-validate observation), `[context_object]`,
  `[context_location]`/`[context_snippet]` (file + line and the located
  line's text — SARIF carries the snippet natively as
  `region.snippet.text`), `[schema_property]`/`[schema_class]` (rdf_map
  definitions), plus SARIF-only `[shacl_path]`/`[context_count]`/`[context_examples]`. The SHACL
  report embeds the violated shapes' defining triples (CBD), so
  `sh:sourceShape` is never an empty node and constraint parameters are
  machine-recoverable from the report alone; `dcterms:creator` names the
  engine. `validate()` stamps `TARGET`/`EXPECTED`/`MESSAGE_SOURCE` columns;
  the enrich pass never rewrites messages (fixed: the IR stored unauthored
  `sh:message` as truthy NaN, so engines emitted NaN instead of default
  text). All computed outside the engine hot path.
- **Association type-check messages say what the reference points at**:
  `sh:class` findings name the target's actual Type — or state that no such
  object exists in the data (dangling reference) — and valueType findings
  (`sh:path ( assoc rdf:type )`) name the found type next to the shape's
  authored message. One shared pass in `validate()`, all engines.
- **`triplets:invalidSparql` reports say what actually happened**: a query
  rejected by a strict engine notes it WAS still evaluated via the rdflib
  fallback; a query no engine can run is a Violation naming the shape and its
  target/path and stating the constraint went unchecked (a shapes bug, not a
  data finding). A defective query no longer crashes `validate()` when rdflib
  is the auto SPARQL engine. SARIF drops the misleading "N object(s)
  affected" wording for such shape-level notes.

## [0.2.0rc1] - 2026-08-10

Everything below was developed across the 0.2.0a1/a2 alphas and this
release candidate; 0.2.0rc1 is the first API-frozen cut of the 0.2 line.

### Changed
- **Shared flavor conversion** (`triplets._engine_detect`): `to_pandas` /
  `to_arrow` / `to_polars` / `as_frame` / `match_flavor` are the single choke
  point for materializing pandas/polars/pyarrow/DuckDB inputs (rdflib loader,
  SHACL, SPARQL, cgmes_tools, export, DuckDB export wrappers). Prefer these
  over local if-flavor blocks.
- **One engine registry everywhere** (`triplets._registry.EngineRegistry`):
  parser, sparql, validation, nquads, cimxml, csv and tools dispatch now share
  a single mechanism — eager `find_spec` availability probing at import, lazy
  module import on first use. The hand-rolled cimxml engine map and the
  ad-hoc nquads/csv dispatch in `triplets.export` are folded in; all public
  names and behavior unchanged.
- **CIM XML export consumes polars input directly** (cython engine): the
  compiled extension reads Arrow `utf8` / `large_utf8` / `string_view` /
  dictionary string columns through the shared accessor `triplets/_arrow/string_column.h`
  (lifted from the qlever Arrow ingest), so polars frames export without a
  pandas hop; per-instance splitting runs in the input's own flavor. Output is
  byte-identical across input flavors. The `python_lxml` engine remains
  pandas-only (auto-picked for `datatypes=True`).

### Fixed
- **Out-of-core DuckDB N-Quads export**: `con.export_to_nquads` streams the
  table through duckdb's record-batch reader into the polars formatter, one
  ~1M-row batch at a time — peak memory stays flat regardless of table size
  (measured: ~350-400 MB delta from 1.1M to 4.6M rows vs linear growth
  before), same speed. `export_to_nquads` also accepts a
  `pyarrow.RecordBatchReader` directly (polars engine required). Line order
  follows the table scan; N-Quads consumers are order-independent.
- **DuckDB exports fetch arrow, not pandas**: `con.export_to_nquads/csv/
  cimxml/excel` used to materialize the table as a pandas DataFrame
  (~383 ms/1.14M rows) before exporting — they now use duckdb's native arrow
  result (~101 ms), and the exporters accept pyarrow input directly (the
  nquads polars engine adopts it in ~9 ms). Exports are still whole-table
  in-memory; chunked export remains a recorded follow-up.
- **DuckDB `regex=True` semantics**: `filter_triplets` /
  `filter_triplets_by_value` used SQL `SIMILAR TO` (full-match); they now use
  `regexp_matches()` — search semantics anywhere in the value, matching the
  pandas/polars `str.contains` engines. Covered by new regex parity tests.
- **DuckDB type-name quoting**: `filter_triplets_by_type` interpolated the
  type name unescaped into SQL; it now goes through the shared literal quoting
  (a name containing `'` filters correctly instead of breaking the query).
- **Empty-parse schema**: `parse([], return_type="arrow"/"polars")` now
  returns the same string / dictionary-encoded column schema as a non-empty
  parse, so empty results concatenate cleanly with real ones.
- **Tools dispatcher engine mismatches**: `triplets.tools.<fn>(df,
  engine="duckdb")` and other input/engine mismatches now raise a clear
  `TypeError` at the boundary (previously they silently ran the pandas engine
  and failed deep inside); unknown engine names raise `ValueError`. Likewise
  `cgmes_tools` data functions reject `engine=` outside pandas/polars with a
  `ValueError` (duckdb/arrow input still runs via the pandas boundary).
- **`parse()` rejects unknown keyword arguments**: the unused `**kwargs`
  swallowed typos silently (a misspelled option looked like it worked while
  doing nothing); mistakes now raise `TypeError`.
- **DuckDB mutators preserve extra columns**: the five mutating helpers run
  in-place DML (UPDATE/DELETE/INSERT) instead of full-table rewrites — user
  columns beyond ID/KEY/VALUE/INSTANCE_ID were previously dropped silently
  on the first mutation, and load order is now stable across mutations.
- **File-handle leaks in zip parsing**: `find_all_xml`/`iter_all_xml` close
  the zip handles they open; the test suite runs with `always::ResourceWarning`
  and zero warnings (rdflib 7.6.0's internal Dataset deprecation noise is
  filtered as a documented upstream issue).

### Added
- **Streaming out-of-core DuckDB ingest** (`parser.parse_batches(...)`,
  `con.read_rdf(paths, append=...)`): one Arrow RecordBatch per XML file
  flows straight into DuckDB — the dataset is never materialized in Python.
  `append=True` adds to the existing table (created if missing); the default
  replaces. Zip members are read lazily, one at a time; `max_workers`
  parses up to that many files ahead (bounded, in-order prefetch — memory
  stays bounded while multi-file ingest parallelizes). This path requires an
  arrow parser engine; `string_type` and `categorical_columns` no longer
  apply to `con.read_rdf` and raise instead of being accepted.
- **DuckDB config persists in the database**: explicitly configured
  table/schema (via `connect(table=/schema=)`, `set_triplets_table`,
  `read_rdf(table=/schema=)`) is stored in a tiny `main."_triplets_config"`
  table, so reopening a persisted file resolves it automatically (cursors
  too). A bare `connect()` writes nothing; read-only opens resolve stored
  config without writing.
- **DuckDB multivalue tableviews**: `type/key/id_tableview` and
  `triplets_to_tableviews` gain `multivalue=` (renders `['a', 'b']` cells,
  matching pandas/polars) and `string_to_number=` (accepted for signature
  parity; `True` raises — VARCHAR views, TRY_CAST is a follow-up);
  `tableview_to_triplets(multivalue=True)` decodes the list encoding.
- **DuckDB tools on views and registered frames**: tableviews, references
  and order-invariant `content_hash` now work when `table=` targets a VIEW
  or a registered frame (`row_number()` fallback where `rowid` doesn't
  exist); `content_hash(order_sensitive=True)` on such targets raises a
  clear error. Tableview views are created in the resolved schema.
- **Configurable Arrow string layout** (`parse(..., string_type=...)`): the
  ID/VALUE columns can be produced as `utf8` (32-bit offsets, default),
  `large_utf8` (64-bit) or `string_view` (polars'/duckdb's native layout,
  adopted zero-copy by polars; pyarrow >= 16). `"auto"` picks per
  return_type: string_view for polars, utf8 otherwise. The cython engine
  builds the layout natively (no measured hot-loop cost); the lxml arrow
  engine casts once at finalize. The shared Arrow accessor handles all
  layouts, so CIM XML export and qlever ingest accept every variant.
- **`triplets.engines()` / `triplets.set_engine(...)`**: inspect what
  `engine="auto"` resolves to per subsystem (selected engine, availability,
  aliases) and override it globally (`set_engine(parser_cimxml="python_lxml_arrow")`;
  `"auto"`/`None` restores). Precedence: per-call `engine=` > `set_engine` >
  auto. Input-bound subsystems (tools, csv) follow the input flavor and cannot
  be overridden.
- **DuckDB connections in `triplets.tools` module functions**:
  `triplets.tools.type_tableview(con, ...)` and friends now route to the
  duckdb engine (previously only bound methods `con.type_tableview(...)`
  worked; the module dispatcher fell through to pandas and raised
  `AttributeError`).
- **DuckDB per-connection table/schema** (`duckdb.connect(table=..., schema=...)`,
  `con.set_triplets_table(...)`, `con.read_rdf(..., table=..., schema=...)`):
  each connection stores the default triplets relation; tools/export/SHACL use
  it when call kwargs omit `table`/`schema` (legacy `table_name=` still works).
  Identifiers are always double-quoted in generated SQL.
- **SHACL validation** (`df.shacl.validate(shapes)`, `triplets.validation`):
  shapes compile once into a constraint IR (content-hash cached), executed by
  four engines — `pyshacl` (spec reference), `pandas` (complete constraint
  registry incl. sh:sparql and sh:node), `polars` (lazy plans, the auto
  performance path — real Equipment profiles in ~2 s vs pyshacl minutes) and
  `duckdb` (larger-than-memory, explicit). Targets: `sh:targetClass` and
  `sh:targetSubjectsOf` (other target kinds warn at compile; pyshacl covers
  them). One deliberate deviation: datatype checks judge the raw lexical form
  (`lexical=True`). See docs/validation.md.
- **SPARQL querying** (`df.sparql.query(q)`, `triplets.sparql`): SELECT/ASK/
  CONSTRUCT over triplet data with three engines — `qlever` (embedded C++,
  local source build), `oxigraph` (embedded Rust, `pip install
  triplets[oxigraph]` — the portable performance path, ~3x faster import and
  2–5x faster warm queries than rdflib) and `rdflib` (reference). One result
  contract across engines: all SELECT values are lexical strings (consumers
  cast) and `return_type` is honored everywhere. Engine state is
  content-hash cached; `scope=` restricts to instances'
  named graphs; `data_unchanged=True` skips re-hashing in hot loops.
  sh:sparql constraints in the SHACL engines ride the same auto engine
  (38.5 s → 74 ms on a constraint-heavy shape). See docs/sparql.md.
- **Engine-state lifecycle** (`triplets.clear_caches()`,
  `triplets.cache_scope()`): the in-process engine caches (rdflib datasets,
  oxigraph stores, qlever index handles, compiled SHACL shapes) never evict on
  their own; long-running processes drop them explicitly, or scope them to a
  `with` block. Only in-memory state is dropped — qlever's on-disk indexes
  stay (reload ~4 ms).
- **SARIF 2.1.0 export** (`violations.shacl.to_sarif()`,
  `triplets.validation.export_to_sarif`): violations → SARIF log for GitHub /
  SonarQube / any SARIF viewer. Grouped by default — one result per rule with
  `occurrenceCount` and first-3/last-3 sample instances (`group=False` for
  one result per violation). `sources=` locates the reported instances in
  the original XML (grep-style pass at export time) and emits exact
  `region.startLine` annotations for GitHub code scanning.
- **Context enrichment** (`validate(..., context=True)`,
  `violations.shacl.enrich(...)`): optional slower pass adding instance/file,
  object type/name, shape name/description (sh:name/sh:description, inherited
  from the node shape) and export-schema definitions (attribute description,
  multiplicity, class description) to the violations report.
- **`read_nquads`** (`triplets.read_nquads`, `pandas.read_nquads`,
  `polars.read_nquads`): N-Quads/N-Triples → triplet DataFrame, the vectorized
  inverse of `export_to_nquads`.
- The pyshacl engine accepts `store="oxigraph"` to load its data graph through
  the oxigraph engine's cached store (identical results; Memory remains the
  measured-faster default — see docs/sparql.md caveats).
- **Versioned NCP export schemas** generated from the ENTSO-E
  [application-profiles-library](https://github.com/entsoe/application-profiles-library):
  `schemas.ENTSOE_NC_2_4_1_552_*` from release branch `ncp-v2-4-1` — the most recent
  official NCP publication (upstream commit pinned in `rdfs/<bundle>/SOURCE.json`;
  2.4.2 and the 2.5 draft are not onboarded due to their DatasetMetadata rdfs:domain
  defect, [entsoe/application-profiles-library#92](https://github.com/entsoe/application-profiles-library/issues/92)).
  Snapshots are fetched with `python -m triplets.rdfs_tools.fetch_profiles`; bundles
  regenerate with `python -m triplets.rdfs_tools.cim_rdfs_to_json` (registry-driven,
  replaces the three per-bundle scripts). ReliCapGrid NC roundtrip tests cover every
  (TSO, profile) example instance.
- `parse(..., shorten_resources=False)`: lossless resource URIs for schema-grade
  parsing (python engines; the RDFS tools use it).
- **`sh:ValidationReport` export** (`violations.shacl.to_shacl_report(...)`,
  `triplets.validation.export_to_shacl_report`): violations frame → standard
  SHACL report (any rdflib format — default from path suffix, or `format=`);
  report metadata: `prov:generatedAtTime`, `dcterms:creator`, optional
  `dcterms:source` / `dcterms:references` via `report_source=` / `report_references=`.
- **Shared validation-run metadata**: `validate()` stamps
  `violations.attrs["validation"]` (start/end timestamps + duration, engine,
  creator, data file names from the Distribution meta rows, shape file names,
  shape/constraint counts, and coverage: `skipped_shapes` /
  `skipped_components` — what the run did NOT evaluate, empty for pyshacl) —
  read by every report exporter, so SARIF (`invocations`/`properties`), the
  sh:ValidationReport and the new `violations.shacl.to_csv()` (writes a
  `<name>_meta.<ext>` sidecar) / `to_excel()` (metadata sheet) all carry the
  same facts. Explicit `report_source=` / `report_references=` still override.
  Internal engine API: `shacl_ir.split_rules` now returns a 3-tuple
  `(vectorized, fallback, skipped components)`.
- `examples/shacl_reports.py`: uv-runnable (PEP 723) end-to-end demo — Svedala
  EQ with three deliberately introduced issues validated against the official
  ENTSO-E Equipment SHACL (Simple + Complex, downloaded on first run), report
  exported as sh:ValidationReport and as SARIF with exact file:line locations.
- **Vectorized `sh:path ( assoc rdf:type )`** — the profile "valueType"
  pattern: the constraint applies to the referenced object's type (dangling
  references yield no value node, per SHACL path semantics). Compiles 428
  previously skipped constraint rows of the ENTSO-E Equipment SHACL; other
  property paths keep the compile warning + pyshacl coverage.
- **Standalone source-location pass** (`violations.shacl.locate(sources=...)`,
  `triplets.validation.locate_violations`): stamps `SOURCE_URI`/`SOURCE_LINE`
  onto the violations frame in one grep-style pass; SARIF and the
  sh:ValidationReport export run it automatically when given `sources=`.
  Positions are whole lines, deliberately: SARIF regions are explicitly
  bounded single lines (`startLine` == `endLine` — a start-only region makes
  GitHub render from the error onward), and columns would add per-format
  anchor rules for no information gain (one statement per line in RDF text
  serializations). The SHACL report carries plain-text "Source: file line N"
  messages, alongside Description/Schema messages from the enrichment
  columns.

### Fixed
- `pathlib.Path` inputs are accepted everywhere: `read_rdf`/`parse` (all
  engines, incl. the compiled Arrow parser — closes #75), `export_to_excel`
  (crashed on `path.endswith`), `export_to_csv`, `export_to_nquads`.
- `export_to_nquads()` without a path no longer crashes — defaults to
  `export.nq` in the working directory.
- The compiled Arrow parser decodes XML entities (`&gt;`, `&amp;`, …) like the
  lxml engines — values containing them now roundtrip through export.
- Export schemas include externally-defined (`Description`-stereotype) classes,
  so instance objects referenced by `rdf:about` are no longer dropped on export
  (NC associations attached to EQ objects; CGMES `SSH.RegulatingCondEq`,
  `DY.EnergyConsumer`).
- Header entries are injected into each profile section only when the profile
  does not define them itself — the header no longer overwrites a profile's own
  definitions (e.g. each profile's `String` datatype namespace, the CGMES 2.4
  `FH` section's `FullModel`/`Model.profile`).
- An ill-typed value under an `rdf_map` (e.g. `"abc"` on an `xsd:float` key) no
  longer cripples sh:sparql validation silently: the qlever ingest error names
  the offending key/value/datatype ("fix the instance data or the schema
  datatype"), the failed engine-state build is cached per content hash instead
  of being re-paid by every rule, and the failure is reported as ONE
  `triplets:invalidSparql` Warning per run while the rules still validate via
  the rdflib fallback. Ill-typed data itself is never silently "fixed".
- N-Quads exporters escape `\r` (was emitted raw — grammar-invalid output that
  broke strict oxigraph ingest).
- The context pass (`enrich()` / `validate(..., context=True)`) appends the
  violated property to generic shape-authored messages — "Missing required
  property (attribute). — IdentifiedObject.name"; messages that already name
  their property stay untouched, and raw engine output keeps the authored
  text verbatim.
- The qlever SPARQL engine accepts a bare pyarrow `Table`/`RecordBatch` as
  input (routed through the shared loader before hashing), matching the
  pandas/polars/duckdb and oxigraph engines.

### Changed
- **Python 3.14 supported**: wheels build for cp314, CI tests it (full suite
  verified incl. the compiled Arrow parser).
- Performance benchmarks are deselected by default (`pytest -m performance`
  runs them); the plain suite runs in minutes.
- The RDFS→JSON schema generator (`rdfs_tools` / `cim_rdfs_to_json`) derives
  attribute→class binding from `schema:domainIncludes` as well as `rdfs:domain`
  — the non-inferential convention for reused external terms (dcterms:, prov:,
  …) that avoids hijacking their meaning (see
  [application-profiles-library#92](https://github.com/entsoe/application-profiles-library/issues/92)).
  Attributes with no class binding are now emitted as top-level schema entries
  (with a warning) instead of being dropped.

### Removed
- `build-qlever.yml` CI workflow — the qlever engine is a local source build
  (decision record in TODO.md; build + fork pinning in docs/building.md).
- **Breaking:** the versionless NC schemas `schemas.ENTSOE_NC_552_ED1/ED2`
  (shipped in 0.1.0; generated from an incoherent mix of profile versions) are
  replaced by the versioned `schemas.ENTSOE_NC_2_4_1_552_*` bundles — no alias.
  The stray `ENTSO-E_Object Registry vocabulary_2.1.1_2022-12-29.json`
  single-profile export is gone as well (the OR profile lives in the NC bundles).

## [0.1.0] - 2026-06-29

First packaged release of the restructured library. The codebase was reorganised
into focused modules (`parser`, `tools`, `cgmes_tools`, `export`) with multi-engine
support (pandas, polars, DuckDB) behind a shared `.triplets` accessor namespace.

See [docs/migration_0.0_to_0.1.md](docs/migration_0.0_to_0.1.md) for full upgrade details.

### Added
- **Multiple parser engines** with automatic fallback to the fastest available:
  `python_lxml_pandas` (pure-Python baseline), `python_lxml_arrow`, and the compiled
  `cython_pugixml_arrow` engine (~12x faster, shipped in published wheels).
- **polars support**: `polars.read_rdf(...)` and the `df.triplets.*` accessor namespace.
- **DuckDB support**: `con.read_rdf(...)`, the `con.triplets.*` namespace, and direct SQL
  over the `triplets` table.
- **Accessor namespace** `df.triplets.*` / `con.triplets.*` shared across pandas, polars
  and DuckDB.
- CLI tools `cim-spreadsheet` and `cim-diff`.
- CIM XML export via `export_to_cimxml`, plus CSV, Excel, n-quads and networkx exporters.
- Compiled wheels for CPython 3.11–3.13 on Linux x86_64, macOS arm64 and Windows AMD64.

### Changed
- **Python 3.11+ required** (3.10 dropped).
- `to_networkx()` renamed to `export_to_networkx()`.
- Visualization helpers renamed from `draw_relations_*` to `draw_references_*`.
- With the arrow engines, `KEY`/`INSTANCE_ID` columns are dictionary-encoded and string
  columns use `string[pyarrow]` dtype (~60% less memory). Use the `python_lxml_pandas`
  engine for plain `str` dtypes.
- Triplet values are always strings (or null).
- `export_to_cimxml` exports schema-defined content only by default.
- Parse shortens http(s) `#fragment` resource values to the local name
  (`ControlAreaTypeKind.Interchange`, not the full CIM URI). This landed in
  0.0.13; filters that compare against the full URI no longer match.

### Deprecated
- All `rdf_parser.py` functions now emit `DeprecationWarning` and delegate to the new
  modules. Several `tools` and `cgmes_tools` functions were renamed; the old names keep
  working with a warning. All of these will be removed in 0.2.

[0.2.0]: https://github.com/Haigutus/triplets/releases/tag/0.2.0
[0.2.0rc6]: https://github.com/Haigutus/triplets/releases/tag/0.2.0rc6
[0.2.0rc5]: https://github.com/Haigutus/triplets/releases/tag/0.2.0rc5
[0.2.0rc4]: https://github.com/Haigutus/triplets/releases/tag/0.2.0rc4
[0.2.0rc3]: https://github.com/Haigutus/triplets/releases/tag/0.2.0rc3
[0.2.0rc2]: https://github.com/Haigutus/triplets/releases/tag/0.2.0rc2
[0.2.0rc1]: https://github.com/Haigutus/triplets/releases/tag/0.2.0rc1
[0.1.0]: https://github.com/Haigutus/triplets/releases/tag/0.1.0
