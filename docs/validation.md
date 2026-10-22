# Validation Architecture

## Three validations

Validation is generic: the caller decides which instances and which schema
profiles a run uses, and the core never reads instance headers — it works for
any RDF loaded as triplets, CIM or not. Every input has a scope named after it:

| Input | Scope | Values |
|-------|-------|--------|
| `data` | `data_scope` | INSTANCE_IDs — rows outside the scope are not loaded, so references into unscoped instances count as absent; None = every instance, `[]` = none |
| `rdf_map` | `rdf_map_scope` | the export schema's profiles: section keys (`"EQ"`) and/or the IRIs a profile declares (keyword / versionIRI / profileVersionIRI / conformsTo), mixed, in any sequence; `"*"` = every section; None = the rdf_map's root entries (an unsectioned, hand-made schema — a sectioned bundle has none). An unknown identifier, one several profiles share, and an empty scope raise `ValueError`: no entries at all for `validate_shacl`, no Class entries for the schema validations. `rdf_map_scope` without an `rdf_map` raises too |

| Function | Checks | Runs |
|----------|--------|------|
| `validate_shacl(data, shapes, rdf_map, data_scope, rdf_map_scope, inheritance=True, deactivated_shapes=None)` | SHACL shapes; the scoped profiles give typed literals, the `sh:nodeKind` decision and class inheritance | once over the scoped data |
| `validate_schema(data, rdf_map, data_scope, rdf_map_scope, report_undefined_keys=False)` | cardinality, datatypes and enumerations of the scoped profiles (merged); `report_undefined_keys` also reports keys no scoped profile defines for the object's class | per instance in `data_scope` — cardinality never counts another instance's rows |
| `validate_schema_references(data, rdf_map, data_scope, rdf_map_scope, target_data_scope, target_rdf_map_scope=(), report_missing_targets=True, inheritance=True)` — `target_data_scope=None` = every instance | the reference type of each association of the source profiles (merged, or a union of several): valid classes from the source and target profiles, referenced objects from the source and target instances | per source instance; findings on source objects only |

The CIM header logic lives outside the core, in `triplets.cgmes_tools`:
`declared_profiles(data, rdf_map)` maps each instance to the profiles its
header declares, and `validate_by_header(data, rdf_map, references=False)`
validates each instance against them (see *CIM helpers* below).

### Merged schema (`schema_scope.scoped_rdf_map`)

The listed profiles merge into one schema for the run — a CGMES 3.0 EQ file is
`["EQ", "OP", "SC"]`; a CGMES 2.4.15 `EQ` section already holds Core,
Operation and ShortCircuit as published. Same-named entries combine:

- Class `parameters` and `inheritance` lists: union.
- Property cardinality: the lowest `xsd:minOccours`, the highest
  `xsd:maxOccours` (`n` = unbounded; a missing bound is no bound);
  `multiplicity` follows.
- Attribute `xsd:type`: when the profiles differ (a definition without a type
  counts) the intended type is unknown — the key is left untyped and logged as
  an error. `validate_schema` does not check its datatype. Under
  `validate_shacl` its literals stay plain: pyshacl then reports an
  `sh:datatype` shape on such a key for every value, while the vectorized
  engines' lexical check judges the text.
- Enumeration `values`: union.

Datatype conflicts (not checked) are listed in the run's `skipped_shapes`;
relaxed cardinalities and united enumerations (checked, more loosely) in its
`relaxed_constraints`, with one warning per run naming their count; each note
quotes every profile's own value (`cardinality relaxed to 0..2 (DL: 1..1, DY:
0..1, GL: 1..2)`). The merged `ProfileMetadata` keeps every profile's
identity: `sections` (the section keys, in scope order) and each field as a
list in the same order (`keyword: ["EQ", "OP", "SC"]`, `versionIRI: [...]`). A merged run is never stricter than running the
listed profiles one by one: parameters only add, cardinality only relaxes.
The shipped bundles merged whole (`"*"`) differ in cardinality for 1–15 keys
and in datatype only for CGMES 2.4.15 FH `Model.version`.

### Class inheritance (`schema_scope.class_ancestors`)

`class_ancestors(rdf_map, rdf_map_scope)` → `{class: ancestor IRIs}`: the
same-named Class entries of the scoped profiles union their `inheritance`
lists, and the relation is closed only through ancestors that have a Class
entry of their own in the scope — another class's list is never read as the
parents of a class it mentions. Every engine gets this one map:

- the vectorized engines widen `sh:targetClass` / `sh:class` through one
  class-membership index built from its `(Type, ancestor)` pairs per run (the
  IR is not fanned out): `sh:targetClass Equipment` hits `Breaker`;
- pyshacl gets the same edges as `rdfs:subClassOf` in `ont_graph` (instance
  data is not mutated); the map is already closed, so pyshacl's own closure
  adds nothing. Parents are the IRIs the schema records
  (`https://cim.ucaiug.io/ns#IdentifiedObject` for NC); a bare name resolves
  like `rdf:type` in the data graph (schema namespace, else
  `undefined_namespace`).

So the engines agree by construction. Each profile is its own world: NC SSI's
`ACLineSegment` list stops at `Equipment`, and SSI has no IdentifiedObject
attributes — it reaches `IdentifiedObject` only when a scoped profile (ER) has
an `Equipment` entry of its own. `inheritance=False` matches the exact
`rdf:type` on every engine (pyshacl without `ont_graph`). Only `sh:targetClass`
(`target_by="Type"`) and `sh:class` widen; KEY / VALUE / ID targets pick exactly
what they name. Schema class rules
never widen: each Class entry already lists its inherited attributes.
Identical findings are reported once (a property shape on both a parent and a
child node shape, the same triple in two instance files).

## Status & known limitations

Known engine gaps (each reported in the run's coverage metadata,
`skipped_shapes` / `skipped_components`):

- **Vectorized engines walk `sh:targetClass`, `sh:targetSubjectsOf`,
  `sh:targetObjectsOf` and `sh:targetNode`.** Shapes reached solely through
  `sh:target` (or using `sh:xone`) are invisible to polars/pandas/duckdb —
  `compile()` logs a warning naming them; use `engine="pyshacl"` for full
  spec coverage. `sh:rule` (SPARQLRule inference) is never applied by the
  vectorized engines; pyshacl applies it (`advanced=True`), so shapes with
  rules diverge by design — both land in `skipped_shapes`.
- **Property paths**: direct, `sh:inversePath`, and the two-step sequence
  `sh:path ( assoc rdf:type )` (the ENTSO-E "valueType" pattern — the
  constraint applies to the referenced object's type; a dangling reference
  yields no value node per SHACL path semantics). Longer sequences and the
  `*OrMorePath` forms are skipped with a compile warning; `engine="pyshacl"`
  covers them.
- **Lexical datatype checks**: with `lexical=True` (the default) datatype
  checks judge the raw *lexical form* of values — see the dedicated section
  below. `engine="reference"` always gives the pure pyshacl view.
- `sh:nodeKind` BlankNode(+combo) cases are intentionally not implemented in
  the vectorized engines (triplets data has no blank nodes).
- `sh:nodeKind` follows the export schema: an Association / Enumeration path is
  IRI and an Attribute path Literal, whatever the text. For a KEY the schema does
  not declare (or without `rdf_map`) the vectorized engines guess from the text
  (`iri.REFERENCE_LIKE`: any-case UUID, `scheme://…`, `urn:…`, `Name.attr`, or an
  ID in the data), while the exported graph pyshacl sees uses the stricter export
  rule (absolute IRI or lowercase UUID → IRI) — the two can disagree on such keys.
- The duckdb engine streams/spills by design, but a true larger-than-RAM
  validation has not been exercised yet.

## Engines

Registry dispatch (mirroring the parser), so compiled engines plug in without
touching the public API:

| Engine | File | Requires | Role |
|--------|------|----------|------|
| `pyshacl` | `validation/shacl_pyshacl.py` | pyshacl + rdflib (`pip install triplets[validation]`) | **reference** — spec-complete, rdflib-based. `store="oxigraph"` loads the data graph through the oxigraph SPARQL engine's cached store (identical results; opt in only when the store is already loaded for SPARQL) |
| `pandas` | `validation/shacl_pandas.py` | core (+`sparql` extra for sh:sparql rules) | compiled-IR executor for debugging; **complete registry** — `sh:sparql` delegated to `triplets.sparql` (`max_workers` parallelizes those queries), `sh:node` expanded at compile time and run against the referenced value nodes, `sh:nodeKind` decided by the rdf_map schema (value form when schema is silent). Explicit `engine="pandas"` |
| `polars` | `validation/shacl_polars.py` | polars | compiled-IR executor for performance: one LazyFrame plan per constraint, single `polars.collect_all` (parallel, common subplans eliminated). Same semantics as pandas; nested/query components delegate to the pandas implementations. The fast in-memory default |
| `duckdb` | `validation/shacl_duckdb.py` | duckdb | compiled-IR executor for **larger-than-memory** data: one SQL query per constraint against the connection's triplets table (streams/spills via DuckDB's executor). Defaults come from the connection (`duckdb.connect(table=..., schema=...)`); call kwargs `table`/`schema`/`table_name` override. Accepts a connection or registers any frame. Constraints batch 100-per-`UNION ALL` statement — explicit choice (`engine="duckdb"`, not in auto) when the data does not fit in memory |

Auto order: `polars → pandas → pyshacl` (first importable).
`engine="reference"` always gives the pure pyshacl view. Custom
engines via `triplets.validation.register_engine(name, module)`.

## Compile Once (`shacl_ir.py`)

Shapes are parsed by rdflib **exactly once** into `CompiledShapes`, cached by
content hash of the shape sources:

```
shapes.ttl ──rdflib──► CompiledShapes
                        ├── graph  (rdflib shapes graph — what pyshacl consumes)
                        ├── ir     (flat constraint table — what the vectorized engines consume)
                        ├── hash   (content hash, the cache key)
                        ├── plans  {engine name → compiled artifact, filled lazily}
                        ├── deactivated  (shape ids switched off: the graph's sh:deactivated true)
                        └── active(deactivated_shapes) ──► CompiledShapes  (the run's rule set, cached per set)
```

The IR is a pandas DataFrame, one row per shape × target × constraint component.
`compiled.ir` is inspectable (see `examples/shacl_validation.py`); the engine
registries that consume it are internal.

| field | type | meaning |
|-------|------|---------|
| `shape_id` | str | full shape IRI (blank-node id for anonymous property shapes) — becomes `SOURCE_SHAPE` in reports and the join key for `enrich` |
| `shapes` | tuple[str] | lineage: the shape ids whose switch-off removes the row, outermost first — NodeShape → property shape → `sh:node` target / logical member → the defining shape (nested row dicts carry it too) |
| `target_on` | str | what the shape applies to, read by `target_by`: a Type name, a KEY or an ID |
| `target_by` | `"Type"` / `"KEY"` / `"VALUE"` / `"ID"` | how the rows are picked: `Type` = objects of that Type (`sh:targetClass`; subclasses with `inheritance`); `KEY` = the IDs having that KEY (`sh:targetSubjectsOf`); `VALUE` = the VALUEs under that KEY (`sh:targetObjectsOf` — referenced objects, described in the data or not; literal values too); `ID` = that one ID (`sh:targetNode`; `urn:uuid:` stripped, any other IRI verbatim — it need not occur in the data, the ENTSO-E shapes use a class IRI as the focus of a dataset-wide `sh:sparql`) |
| `path` | str \| None | the property as one triplet KEY (local name); None for node-level constraints (`sh:closed`, node-level `sh:sparql`) |
| `inverse` | bool | `sh:inversePath` — engines swap the FOCUS/VALUE direction |
| `via_type` | bool | the `( assoc rdf:type )` sequence path: the value nodes are the referenced objects' *types* |
| `component` | str | the dispatch key (`"sh:minCount"`, …) — see the matrix below |
| `params` | object | component parameter, shape varies (next table) |
| `severity` | str | local name of `sh:severity`, default `"Violation"` applied at compile time |
| `message` | str \| None | `sh:message`; engines substitute their own default text when None |
| `name`, `description` | str \| None | `sh:name`/`sh:description` with parent-NodeShape inheritance — read only by `context.enrich`, never by engines |

`params` per component:

| components | `params` shape |
|------------|----------------|
| `sh:minCount` `sh:maxCount` `sh:minLength` `sh:maxLength` | int |
| `sh:minInclusive` `sh:maxInclusive` `sh:minExclusive` `sh:maxExclusive` | float (integer bounds are coerced) |
| `sh:datatype` (`"xsd:float"`), `sh:class`, `sh:nodeKind`, `sh:pattern`, `sh:hasValue`, `sh:equals` `sh:disjoint` `sh:lessThan` `sh:lessThanOrEquals` (the other path's KEY) | str |
| `sh:in` | list[str] (IRIs shortened to local names) |
| `sh:closed` | list[str] — the **fully resolved** allowed KEY list: `sh:ignoredProperties` + every direct non-inverse `sh:property` path of the shape, resolved at compile time (paths reached through `sh:node` are not included) |
| `sh:or` / `sh:and` | list[list[dict]] — one inner list of nested IR row dicts per alternative |
| `sh:not` | list[dict] — nested IR row dicts |
| `sh:node` | `{"shape": local name, "rows": [nested IR row dicts]}` — the referenced shape expanded at compile time |
| `sh:sparql` | `{"select": SELECT text with `$this`/`$PATH` placeholders, "prefixes": resolved `PREFIX` header, "path": full IRI of the owning `sh:path` or None}` |

In a property shape, a nested `sh:or` / `sh:and` / `sh:not` shape without its
own `sh:path` takes the property's path: it judges each value node, and
`sh:or` / `sh:not` report per value (`VALUE` = the value node).

Compile-time behaviors worth knowing: a NodeShape with several `sh:targetClass`
(the ENTSO-E profiles do this) emits every row once per target class; shape
reference cycles through `sh:node` / `sh:or` / `sh:and` / `sh:not` are detected,
warned about, and dropped; supported `sh:path` forms are a direct IRI,
`sh:inversePath`, the `( assoc rdf:type )` sequence, and `sh:alternativePath`
only when one member carries a nested inverse (any other path form warns and
skips the property shape — pyshacl covers it). Unknown components are kept and
logged — engines skip what they don't implement.

Every shape compiles, `sh:deactivated` or not. `compiled.deactivated` lists the
shapes the graph declares `sh:deactivated`; `compiled.active(deactivated_shapes)`
is the run's rule set with exactly that argument switched off — None: the
declared set, `[]`: nothing (every shape runs, the declared ones included), a
shape IRI or any list: it replaces the declared set. Rows whose lineage meets a
switched-off shape are dropped; a switched-off `sh:or` / `sh:and` member or
`sh:node` target empties and conforms, a switched-off `sh:not` shape empties and
flags every node it judges (as pyshacl); under `sh:closed` a switched-off
property shape's path stays declared. The filtered compile is cached per set on
the one compile: the compile's `hash`, its own `plans`; with nothing declared
and nothing chosen `active()` is the compile itself. Truthiness follows pyshacl
(`bool(literal.value)`): `true`, a non-empty plain literal — so a plain
`"false"` deactivates — or a non-zero number; `"false"^^xsd:boolean` and an
ill-typed literal (no value) do not; a non-literal or a second value raises
ValueError at compile (pyshacl: `ShapeLoadError`). An id that names no shape in
the graph is logged as a warning. pyshacl validates a copy of the shapes graph
carrying exactly the chosen set as `sh:deactivated true` when it differs from
the declared one; the shapes embedded in the SHACL report carry the same flags.
Rows that differ only in lineage (one property shape under two node shapes with
a common target) run once.

`CompiledShapes.plans` is where each engine caches its own compiled artifact
(the polars/duckdb `split_rules` partition) — re-validating new data against
the same shapes never recompiles anything.

### Component coverage per engine

The shared contract lives in `shacl_ir`: `KNOWN_COMPONENTS` (all 24 keys) and
`FALLBACK_COMPONENTS` (`sh:or/and/not/node/sparql` — the nested/query components
every vectorized engine delegates to the pandas implementations via
`split_rules`). A test (`test_shacl_ir.py::test_component_registries_agree`)
pins the registries together:

| engine | registry | coverage |
|--------|----------|----------|
| pandas | `CONSTRAINT_VALIDATORS` | all 24 (the fallback target) |
| polars | `PLAN_BUILDERS` (+ `BATCH_BUILDERS` fast path) | 19 vectorized + 5 delegated |
| duckdb | `SQL_BUILDERS` | 19 vectorized + 5 delegated |
| pyshacl | consumes `compiled.graph`, not the IR | full spec; report vocabulary mapped back via `shacl_report._COMPONENT_MAP` |

The compile cache participates in the shared engine-state lifecycle:
`triplets.clear_caches()` drops cached `CompiledShapes` (with their plans)
together with the SPARQL engines' loaded state, and
`with triplets.cache_scope():` bounds the state created inside the block —
see [sparql.md](sparql.md).

```python
compiled = triplets.validation.compile(["equipment.ttl", "topology.ttl"])
triplets.validation.validate_shacl(data_a, compiled)
triplets.validation.validate_shacl(data_b, compiled)   # shapes parsed once, plans reused
triplets.validation.validate_shacl(data_b, compiled, deactivated_shapes=["http://example.org/OptionalShape"])
```

## Engine Contract

Every engine module implements:

```
validate(data, compiled: CompiledShapes, rdf_map=None, data_scope=None, ancestors=None, **kwargs) → violations DataFrame
```

`rdf_map` is the scoped, merged schema (`scoped_rdf_map`), `ancestors` the
`class_ancestors` map (None = exact `rdf:type`). `validate_shacl` hands the
engines `compiled.active(...)`, the rule set with the switched-off shapes
already removed; an engine called directly with the full compile validates
them too.

- **pyshacl** consumes `compiled.graph` (data goes through `_rdflib_loader`);
  `ancestors` becomes its `ont_graph`.
- **pandas/polars/duckdb** consume `compiled.ir` — they never touch rdflib and
  read the raw string `VALUE`s directly (`rdf_map` decides `sh:nodeKind` and
  types the graph of their sh:sparql delegation); `ancestors` becomes the
  class-membership index.
- **sh:sparql IR rows**: pyshacl evaluates them natively (`advanced=True`).
  The vectorized engines delegate them to `triplets.sparql` (auto order:
  qlever when built, else oxigraph when installed, else rdflib): the data is
  loaded into one dataset, each constraint runs as a single SELECT with the
  focus nodes bound via `VALUES ?this {...}` and `$PATH` substituted from
  the IR, and `max_workers=N` runs the constraint queries in parallel
  processes on the rdflib path only (fork gives copy-on-write sharing of the
  dataset; threads don't help rdflib — it is GIL-bound pure Python; qlever
  and oxigraph are ms-scale sequentially).
  For sh:sparql-heavy profiles build the qlever extension or
  `pip install triplets[oxigraph]`; the rdflib fallback runs in minutes.
  **No query fixing**: constraint queries run exactly as authored. A
  constraint query a strict engine rejects is reported as a
  `triplets:invalidSparql` Warning row naming the shape and is still
  evaluated on the lenient rdflib engine.

## The Lexical-Form Datatype Deviation

The one deliberate divergence from pyshacl. rdflib compares a literal's
*declared* datatype, so `"1"^^xsd:float` is simply valid — the vectorized
engines see the raw lexical form and can judge it. Two levels:

| Finding | Example (declared `xsd:float`) | `VIOLATION_TYPE` | `SEVERITY` |
|---------|-------------------------------|------------------|------------|
| outside the lexical space | `"abc"` | `sh:datatype` | shape's declared severity |
| valid but narrower/non-canonical form | `"1"` (integer form) | `triplets:lexicalForm` | `Warning` |

`validate_shacl(..., lexical=True)` (the default) appends these findings to any
engine's report (duplicates dropped on
`[ID, KEY, VALUE, VIOLATION_TYPE, SOURCE_SHAPE, SEVERITY]`).
Parity tests treat `triplets:lexicalForm` rows as documented extras: engines
must never *lose* a violation pyshacl reports.

`xsd:anyURI` values are literals (the schema entry type decides, not the
datatype), so they get the same lexical check: an IRI reference (RFC 3987,
relative and non-ASCII allowed) — no controls, space, DEL or ``<>"{}|^`\``, and
`%` only as a `%XX` escape. `"has space"^^xsd:anyURI` is an `sh:datatype`
violation on every engine; `xsd:string` stays unchecked.

`rdf_map` + `rdf_map_scope` matter for the pyshacl path: without them every
`VALUE` is an untyped string, so `sh:datatype xsd:float` trips on everything;
with them the N-Quads export attaches the scoped profiles' `xsd` types and only
genuinely broken values fail.

## Result Structure

Every validation returns the canonical violations DataFrame — **empty =
conforms** — identical across all engines:

| Column | Meaning |
|--------|---------|
| `ID` | focus node (instance UUID, `urn:uuid:` stripped — `triplets.iri.local_id`) |
| `KEY` | property path (CIM short name, e.g. `IdentifiedObject.name`) |
| `VALUE` | offending value |
| `VIOLATION_TYPE` | constraint component (`sh:minCount`, `sh:datatype`, `triplets:lexicalForm`, `triplets:invalidSparql`, ...) |
| `MESSAGE` | message from the shape |
| `SEVERITY` | `Violation` / `Warning` / `Info` |
| `SOURCE_SHAPE` | shape URI that produced the result |

plus `EXPECTED` (what the constraint requires, worded from the rule) and
`MESSAGE_SOURCE` (authored `sh:message` or engine text) — both from the rules
alone — and, for the schema validations, `PROFILE` (the list of scoped
section keys, `["EQ", "OP", "SC"]`; the reports format it for display). What
a finding found in the data (`TARGET`) is added by `enrich`.

Violations DataFrames export like any other DataFrame (`export_to_csv`, Excel, ...),
as a standard `sh:ValidationReport` (`violations.shacl.to_shacl_report(...)` — the
exact inverse of the pyshacl report mapping in `shacl_report.py`) or as SARIF 2.1.0
(`violations.shacl.to_sarif(...)`, see below). Source positions come from one shared
pass — `violations.shacl.locate(sources=...)` stamps `LOCATION_COLUMNS`
(`SOURCE_URI`, `SOURCE_LINE`) onto the frame; both exports run it
automatically when given `sources=`, or reuse the columns when already present.

The SHACL report serializes via rdflib: `format=None` (default) derives the format
from the path suffix (`.ttl` → turtle, `.xml`/`.rdf` → RDF/XML, …); an explicit
`format=` always wins.

**Validation-run metadata** is stamped once, by the validation function, onto
the returned frame as `violations.attrs["validation"]`. Every report exporter
reads it, so all formats tell the same story; `concat_violations(frames)`
joins the frames of several runs of one constraint language and merges their
metadata (earliest start, latest end, counts once per rule set, lists
united, differing values kept as the list of distinct values):

| key | meaning |
|-----|---------|
| `started_at` / `generated_at` / `duration_seconds` | validation start/end (UTC, Zulu form) and wall-clock duration of the engine run (shapes compilation excluded — cache-independent) |
| `engine` | the engine that produced the report |
| `creator` | tool + version |
| `source` | data file names (from the data's Distribution label meta rows) |
| `references` | shape file names (recorded at compile) |
| `compiled_hash` | the shape files' content hash (`CompiledShapes.hash`, the same with any `deactivated_shapes`) |
| `deactivated_shapes` | the shape ids the run switched off, sorted (`[]` = every shape ran); the run's rule set is `compiled_hash` + `deactivated_shapes` — `concat_violations` counts shapes and constraints once per rule set |
| `node_shapes` / `constraints` | count of NodeShapes not switched off / distinct constraints of the run's rule set (IR rows that differ only in lineage count once) |
| `profiles` | the section keys the run used, a list (None = root entries, or no rdf_map) |
| `inheritance` | whether class inheritance was applied — `validate_shacl`: an rdf_map scope with classes and `inheritance=True`; `validate_schema_references`: the `inheritance` flag |
| `skipped_shapes` | what THIS run did not evaluate as written: keys whose datatype differs between the merged profiles (every engine); unreachable targets (`sh:target` / `xone`), `sh:rule` shapes (not applied) and inexpressible `sh:path` forms (a listed property shape's sibling constraints on the same NodeShape may still have run) — those two empty for `engine="pyshacl"` (spec-complete); association ranges no given profile defines (`validate_schema_references`) |
| `relaxed_constraints` | checks the profile merge loosened: cardinality relaxed, enumeration values united (every engine) |
| `skipped_components` | constraint components the engine neither vectorizes nor delegates |

The coverage keys turn the compile/engine warnings into data: a report that
says "0 violations" also says whether every shape actually ran. Empty coverage
is stated, not implied — SARIF keeps the `[]`, the csv/excel metadata rows keep
a blank-valued row.

| exporter | carries the metadata as |
|----------|-------------------------|
| `to_shacl_report` | `prov:generatedAtTime`, `dcterms:creator` (tool + version + engine, e.g. `"triplets 0.2.0 (engine: polars)"`) / `source` / `references` on the report node (standard vocabulary only — counts/coverage stay in the tabular/SARIF forms) |
| `to_sarif` | `invocations[].startTimeUtc`/`endTimeUtc` + the full run `properties` bag (engine, duration, counts, coverage) |
| `to_csv` | a `<name>_meta.<ext>` sidecar file with KEY,VALUE rows |
| `to_excel` | a second `metadata` sheet |

Every report export takes `export_to_memory=True` and returns BytesIO
object(s) with `.name` instead of touching the filesystem — the same
convention as `export_to_cimxml`/`export_to_csv` (`to_csv` returns a list:
data file + sidecar).

Every message states its origin with a prefix, and the constraint text is
never rewritten — raw `sh:message` / engine wording stays verbatim behind its
tag. Exactly one of `[shacl_message]`/`[engine_message]` appears per result —
the constraint text has one author:

| tag | carries |
|-----|---------|
| `[shacl_message]` | the shape's own `sh:message`, verbatim |
| `[engine_message]` | engine-worded constraint text (default messages, `triplets:*` tool findings) |
| `[shacl_expected]` | what the constraint requires, worded from the IR parameter (`one of: Bay, VoltageLevel`) — the `EXPECTED` column, stamped by the validation, no extra input needed |
| `[context_value]` | the offending value itself (the bad literal, or the reference) — self-contained errors, no need to open the instance data |
| `[context_message]` | what was actually found — reference targets (id, Type, name / dangling), duplicate values on maxCount — the `TARGET` column (context.enrich with `data=`) |
| `[context_object]` | the validated object (`Breaker BRK-1` — context.enrich) |
| `[shacl_description]` | the shape's `sh:description` (context.enrich) |
| `[schema_property]` | the rdf_map property (attribute/association) definition + multiplicity (context.enrich with `rdf_map=`) |
| `[schema_class]` | the rdf_map description of the object's class (context.enrich with `rdf_map=`) |
| `[context_location]` / `[context_snippet]` | source file + line, and the located line's text (locate pass) |
| `[shacl_path]` / `[context_count]` / `[context_examples]` | SARIF text only: the shape's declared path, grouped totals, sample objects |

Report size is controlled by existing dials, no dedicated flags: SARIF groups
by default (`group=True` — repeated entries like `[schema_class]` appear once
per rule, not per violation; `group=False` opts into the verbose per-violation
form). The SHACL report cannot group (one `sh:result` per focus node is spec
semantics), but every message entry is column-driven — drop a column
(`violations.drop(columns=["CLASS_DESCRIPTION"])`) and its entry disappears;
enrichment itself is opt-in.

The SHACL report additionally **embeds the violated shapes' defining triples**
(CBD, stamped by `validate_shacl()` into the run metadata), so `sh:sourceShape` is
never an empty blank node — the `sh:in` list and every other constraint
parameter are machine-recoverable from the report alone. SARIF carries the
located line as the native `region.snippet.text`. All of this happens in the
post-validate/context/locate passes — the engine hot path is untouched.

The validation stamps the message/engine distinction as a `MESSAGE_SOURCE`
column (authored messages are known from the compiled IR); bare frames fall
back to the violation-type namespace. The SHACL report's `dcterms:creator`
names the engine (`"triplets 0.2.0 (engine: polars)"`); SARIF carries it in
the run properties. The SHACL report carries the tagged entries as separate
`sh:resultMessage`s (results stay one per
violation — merging them would break sh:ValidationReport semantics); SARIF
carries them as newline-separated blocks in one `message.text`, adds
`[count]` / `[examples]` blocks for grouped results, and puts the occurrence
count in the rule title (`Line completeness (8×)` — the ruleId stays stable,
so GitHub alert matching is unaffected).

`enrich` and `locate` preserve the attrs. On `to_shacl_report`, explicit
`report_source=` / `report_references=` override the stamped values (plain
labels — distinct from the `sources=` locate pass and the shapes object
`to_sarif(shapes=)` takes). Frames without the attrs (bare frames,
`report_to_violations` output) carry no run metadata in SARIF/csv/excel; the
SHACL report node still gets `prov:generatedAtTime` (export time) and
`dcterms:creator` — a standard report always says when and by what it was
written.

## Schema validation (`validate_schema` / `validate_schema_references`)

Validate data directly against the export schema (rdf_map) — no SHACL shapes
needed, no rdflib on this path:

```python
rdf_map = schemas.ENTSOE_CGMES_3_0_0_552_ED2
violations = triplets.validation.validate_schema(data, rdf_map, rdf_map_scope=["EQ", "OP", "SC"])
violations = data.shacl.validate_schema(rdf_map, data_scope=[eq_id], rdf_map_scope="EQ",
                                        engine="duckdb", report_undefined_keys=True)
references = triplets.validation.validate_schema_references(
    data, rdf_map, [ssh_id], ["SSH"], target_data_scope=[eq_id], target_rdf_map_scope=["EQ"])
references = triplets.validation.validate_schema_references(   # every loaded instance, source and target
    data, rdf_map, rdf_map_scope=["SSH"], target_rdf_map_scope=["EQ"])
compiled = triplets.validation.compile_schema(rdf_map, ["EQ", "OP", "SC"])     # cached
compiled = triplets.validation.compile_references(rdf_map, ["SSH"], ["EQ"])
```

**`validate_schema`: per instance, against the merged scoped profiles.**
Every instance in `data_scope` is validated on its own (None = every
instance). This is why the run is per instance: every CGMES 3.0 profile
serializes `IdentifiedObject.mRID` 1..1 — a union frame carries duplicate mRID
rows per re-declared object (false maxOccurs), while SSH's own mRID
requirement must be met by SSH's own rows, not EQ's. The checks are:
cardinality from the resolved `xsd:minOccours`/`xsd:maxOccours` (incl. mRID —
the schemas are profile-accurate: CGMES 2.4 declares it 0..1/not serialized,
CGMES 3.0/NCP 1..1), datatype lexical checks from `xsd:type`, enumeration
membership from `values`, and with `report_undefined_keys=True` keys an object
carries that no scoped profile defines for its class (`schema:domainIncludes`).
Class rules never widen to subclasses.

**`validate_schema_references`: reference types against target instances and
profiles.** Each source instance's associations (the source profiles, merged)
are checked with the referenced objects looked up in that instance and the
`target_data_scope` instances. A reference conforms when ANY type of its
object (RDF types are cumulative) is the range class or, with
`inheritance`, a subclass by `class_ancestors` over the source and target
profiles. A range class none of those profiles defines cannot be decided: the
association is listed in `skipped_shapes`. With `report_missing_targets=True`
(default) a reference to an object in neither the source nor the target
instances is a `triplets:missing` finding. Only associations stored in the
source instance are reported — EQ and SSH share object IDs, so a target file
re-declaring an object does not bring its rows into the source's report.

The applied profiles land in `attrs["validation"]["profiles"]`, each
violation carries them as a list in the `PROFILE` column, reports carry
`[rdfs_profile] EQ, OP, SC` entries, and SARIF alert titles read
`RDFS <profiles> <Class> <attr> (N×)`. Schema shape ids carry the scope in
their namespace (`urn:triplets:schema:EQ:OP:SC#Breaker.IdentifiedObject.name`),
so findings of different scopes never share a rule.
The same vectorized engines run everything (plans cached per compiled
schema); the pyshacl engine refuses schema-compiled IR. **Results do not
masquerade as SHACL**: constraint language `"rdfs"`, violation types
`xsd:minOccurs`/`xsd:maxOccurs`/`xsd:type`/`rdfs:range`/`schema:domainIncludes`/
`triplets:missing` — real vocabulary IRIs in `sh:sourceConstraintComponent`,
message tags `[rdfs_expected]`, `[rdfs_path]`, `[rdfs_profile]`, …

## CIM helpers (`triplets.cgmes_tools`)

The only code that reads instance headers, built on the generic calls:

```python
from triplets import cgmes_tools

cgmes_tools.declared_profiles(data, rdf_map)        # {INSTANCE_ID: ["EQ"], ...}
violations = cgmes_tools.validate_by_header(data, rdf_map, references=True)
```

`declared_profiles` matches the header's profile-identity fields
(`Model.messageType`, `keyword`, `Model.profile`, `conformsTo`) against the
schema profiles' declared identity, with the legacy CGMES 2.4.15 profile-URL
substrings as fallback; an identifier several profiles share matches none.
`validate_by_header` runs `validate_schema` for each group of instances
declaring the same profiles (merged), with `references=True` also
`validate_schema_references` against every loaded instance and its declared
profiles, and joins the runs with `concat_violations`. `data_scope=` selects
the validated instances; references still resolve in every loaded instance.
An instance whose header matches no profile is skipped and named in
`skipped_shapes`; `rdf_map_scope=` overrides the declared profiles for
header-less data.

## Shared Loading (`_rdflib_loader.py`)

SHACL and SPARQL reach rdflib through the same two helpers, so a validation and
a query over the same data see an identical graph:

| Function | Produces |
|----------|----------|
| `load_dataset(data, rdf_map=None, store="memory")` | `rdflib.Dataset` seeing the deduplicated union, one named graph per `INSTANCE_ID` (`urn:uuid:{INSTANCE_ID}`). `store="oxigraph"` backs it with the oxigraph SPARQL engine's cached store via oxrdflib (`"auto"`: oxigraph when installed) |
| `scoped_graph(dataset, scope=None)` | full union when `scope is None`; otherwise a concrete `Graph` holding just the scoped instances' named graphs (`validate_shacl`'s `data_scope`) |

## Call Sequence

```
data.shacl.validate_shacl(shapes, rdf_map=..., rdf_map_scope=["EQ"])
|
'-> validation.validate_shacl(data, shapes, engine="auto", lexical=True)
    |
    |-> compile(shapes)                        # cached by content hash
    |   '-> _load_shapes -> parse_ir           # rdflib parses ONCE
    |
    |-> scoped_rdf_map(rdf_map, rdf_map_scope) # the merged schema of the run
    |-> class_ancestors(rdf_map, rdf_map_scope)
    |
    |-> get_engine("auto")                     # polars (first importable)
    |   '-> shacl_polars.validate(data, compiled, scoped, data_scope, ancestors)
    |       # reads raw VALUEs; lexical findings emitted inline
    |       # (pyshacl path instead: load_dataset -> scoped_graph ->
    |       #  pyshacl.validate(compiled.graph, ont_graph=ancestors as
    |       #  rdfs:subClassOf) -> report_to_violations)
    |
    |-> + shacl_pandas datatype/lexical supplement   # ONLY for engine="pyshacl"
    '-> EXPECTED / MESSAGE_SOURCE, run metadata

validation.validate_schema(data, rdf_map, data_scope, rdf_map_scope)
|-> compile_schema(rdf_map, rdf_map_scope)     # merged, cached
'-> one engine pass per instance (data_scope=[instance]), PROFILE = the scoped section keys

enrich(violations, data=, shapes=, rdf_map=, data_scope=)  # TARGET + context columns, a separate call
```

## File Layout

```
triplets/
|-- _rdflib_loader.py        # shared with sparql: load_dataset(), scoped_graph()
'-- validation/
    |-- __init__.py          # validate_shacl / validate_schema / validate_schema_references, engine registry
    |-- schema_scope.py      # rdf_map_scope: profile resolution, merged schema, class_ancestors
    |-- schema_ir.py         # export schema -> CompiledShapes (compile_schema, compile_references)
    |-- shacl_ir.py          # shapes -> CompiledShapes (IR compiler, content-hash cache)
    |-- shacl_pyshacl.py     # reference engine: data + compiled.graph -> report
    |-- shacl_pandas.py      # compiled-IR executor (full registry; eager, debugging)
    |-- shacl_polars.py      # compiled-IR executor (lazy plans + collect_all, performance)
    |-- shacl_duckdb.py      # compiled-IR executor (SQL per constraint, larger-than-memory)
    |-- shacl_report.py      # ValidationReport <-> violations; multi-format export
    |-- context.py           # enrich (TARGET, instance/object/shape/schema context), concat_violations
    |-- locations.py         # violations -> source line/column (the sources= grep pass)
    '-- sarif.py             # violations -> SARIF 2.1.0 (grouped by default)
```

## Usage

```python
import pandas
import triplets
from triplets.export_schema import schemas

data = pandas.read_RDF(["grid.zip"])

# validate against a shapes file (format auto-detected by extension) — empty = conforms;
# the scoped profiles type the literals and give the class inheritance
rdf_map = schemas.ENTSOE_CGMES_3_0_0_552_ED1
violations = data.shacl.validate_shacl("shapes.ttl", rdf_map=rdf_map, rdf_map_scope=["EQ"])

# compile once, validate many
compiled = triplets.validation.compile(["equipment.ttl", "topology.ttl"])
violations = data.shacl.validate_shacl(compiled)

# one compiled base, shapes switched off per run (the NC validation aggregates):
# each set is filtered from the same compile and cached on it
base = triplets.validation.compile(base_shape_files)
violations = data.shacl.validate_shacl(base, deactivated_shapes=aggregate_off)

# data_scope filters to the named graphs (validated instances) — out-of-scope
# data is not loaded; include dependency instances for cross-instance references
one_instance = str(data["INSTANCE_ID"].astype(str).iloc[0])
violations = data.shacl.validate_shacl("shapes.ttl", data_scope=[one_instance])

# engines: auto is polars -> pandas -> pyshacl; works on any input flavor
data.shacl.validate_shacl("shapes.ttl", engine="pyshacl")      # or "polars" / "pandas" / "duckdb"
triplets.validation.validate_shacl(con, "shapes.ttl")          # DuckDB connection
violations = data.shacl.validate_shacl("shapes.ttl", lexical=False)   # pure pyshacl report

# pyshacl on the oxigraph engine's cached store (identical results; opt in
# when the store is already loaded for SPARQL — Memory is faster otherwise)
data.shacl.validate_shacl("shapes.ttl", engine="pyshacl", store="oxigraph")

# the context pass, a separate call — TARGET (what was found), instance/file,
# object type/name, shape sh:name/sh:description and schema definition columns
violations = violations.shacl.enrich(data=data, shapes=compiled, rdf_map=rdf_map, rdf_map_scope=["EQ"])

# SARIF 2.1.0 for GitHub / SonarQube / any SARIF viewer
violations.shacl.to_sarif(path="report.sarif")

# standard sh:ValidationReport for SHACL tooling (format from path suffix,
# or format=); sources= adds an "[context_location] file line N" message per
# result (SHACL has no location vocabulary — plain-text messages travel
# everywhere); the dcterms metadata (timestamp, creator, data/shape file
# names) comes from violations.attrs — stamped by the validation, overridable
# with report_source=/report_references=
violations.shacl.to_shacl_report(path="report.ttl", sources=["grid.zip"])
violations.shacl.to_shacl_report(path="report.xml")  # RDF/XML via .xml

# tabular exports with the same metadata: CSV writes a report_meta.csv
# sidecar, Excel a second "metadata" sheet
violations.shacl.to_csv(path="report.csv")
violations.shacl.to_excel(path="report.xlsx")

# the location pass standalone — SOURCE_URI/LINE/COLUMN/COLUMN_END columns,
# reused by both report exports
violations = violations.shacl.locate(sources=["grid.zip"])
```

pyshacl pass-through options (`inference`, `advanced`, `abort_on_first`,
`store`) are forwarded as keyword arguments. Runnable end-to-end demos live in
`examples/shacl_validation.py` (engine behavior walkthrough) and
`examples/shacl_reports.py` (uv-runnable: performance engines + both report
exports with source locations — `uv run examples/shacl_reports.py`).

## Context enrichment (`validation/context.py`)

`enrich(violations, data=, shapes=, rdf_map=, rdf_map_scope=, data_scope=)` —
a separate call after any validation — appends `ENRICHMENT_COLUMNS` to the
report. Every source is optional; absent sources leave their columns null, so
the output schema is stable. `data_scope` reads only those instances of
`data` — pass the validation's own scope so TARGET finds exactly what the run
saw:

| Columns | Source | Content |
|---------|--------|---------|
| `TARGET` | data (+ shapes, rdf_map) | what the finding found: the referenced object's id, Type and name — or that it is not in the data — for `sh:class` / reference-range / `triplets:missing` findings; the found type of a valueType row (needs *shapes*); the duplicate values of a maximum-count finding. A presented `rdfs:range` counts as a reference when its rule or the schema says the KEY is an Association |
| `INSTANCE_ID`, `INSTANCE_LABEL` | data | instance and its parsed file name (the `Distribution`/`label` meta rows) |
| `OBJECT_TYPE`, `OBJECT_NAME` | data | the focus object's `Type` and `IdentifiedObject.name` |
| `SHAPE_NAME`, `SHAPE_DESCRIPTION` | shapes | `sh:name` / `sh:description` (a property shape inherits the parent NodeShape's) |
| `SCHEMA_DESCRIPTION`, `SCHEMA_MULTIPLICITY` | rdf_map | the violated attribute's schema definition in the profiles the violation was checked against — the `PROFILE` column, else `rdf_map_scope` (one scope, or `{INSTANCE_ID: scope}`), else the run's `attrs["validation"]["profiles"]` — merged like the run (the multiplicity that was checked). Rows without profiles read the root entries; a name the profiles lack stays empty |
| `CLASS_DESCRIPTION` | rdf_map | the object's class description |

`enrich` reads no instance headers. Pass the *same* shapes object the
validation ran with — anonymous property shapes get fresh blank-node ids per
parse, so a re-parsed graph cannot be matched. An object present in several
instances is attributed to its first occurrence.

## SARIF 2.1.0 export (`validation/sarif.py`)

`triplets.validation.export_to_sarif(violations, ...)` /
`violations.shacl.to_sarif(...)` — a spec-valid SARIF 2.1.0 log for GitHub,
SonarQube or any SARIF viewer. Passing `data=`/`shapes=`/`rdf_map=` runs the
enrichment pass first (an already-enriched frame is used as-is).

- **Grouped by default** (`group=True`): a model can carry 100k identical
  findings that are really *one* issue — each rule becomes one result with
  `occurrenceCount` and the first-3 + last-3 sample instances (all listed
  when ≤ 6). `group=False` emits one result per violation row.
- Severity maps `Violation/Warning/Info` → `error/warning/note`; rules carry
  the shape's `sh:name`/`sh:description`; `MESSAGE` falls back to a generated
  text (message is mandatory in SARIF).
- RDF objects carry no text coordinates through the triplets frame:
  results always point at the model via `logicalLocations` (`Type/ID` +
  object name). Passing `sources=` (the original CIM/XML files — paths,
  zips or file-likes) runs the shared `locate_violations` pass
  (`validation/locations.py`) and adds a fully bounded
  whole-line `physicalLocation.region` (`startLine` == `endLine`)
  on the violated property element's line (or the object
  definition) — what GitHub code scanning needs to annotate lines. One
  grep-style pass per file; the parse/validate hot paths are untouched.
  A frame already carrying `LOCATION_COLUMNS` (from
  `violations.shacl.locate(sources=...)`) is used as-is. Without either,
  `artifactLocation.uri` falls back to the enrichment-traced file label,
  region-less. Columns are 1-based UTF-16 code units (SARIF's unit) — a
  start-only region cannot be displayed by GitHub, so regions always carry
  their end.
- Everything domain-specific (triplet coordinates, schema descriptions,
  sample IDs) rides in the `properties` bags.

## Naming Convention

Engine files follow `{purpose}_{engine}.py`, mirroring the parser and export
modules. Shared loading lives in `_rdflib_loader.py`; the IR compiler in
`shacl_ir.py`; the report normalizer in `shacl_report.py`.
