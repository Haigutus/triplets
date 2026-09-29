# Development Notes

Engine-implementation and contributor notes live here — the design rules a new
engine or a change to an existing one must respect. User-facing behavior is
documented in the reference guides ([validation.md](validation.md),
[sparql.md](sparql.md), ...).

## Engine model

The one rule that decides where automatic engine selection is allowed:

> **Auto-selection of the fastest available engine applies only to operations
> whose result is flavor-independent** (parsed files, exported bytes/XML, query
> results, violation reports). **Frame-in → frame-out operations always run in
> the input object's engine** — never auto-hop pandas → polars (measured: the
> round-trip conversion ≈16 ms/1M rows exceeds typical op savings ≈8 ms).

All engine dispatch goes through **`triplets._registry.EngineRegistry`** — do
not hand-roll module maps or local try-import blocks. Registries are
constructed at import time and probe availability with `find_spec`
(microseconds, imports nothing); the chosen module imports lazily on first
use. An engine module must raise `ImportError` on import when its backend is
missing — that is what makes `"auto"` fall through.

Kind names carry a role prefix when the subsystem is format-specific
(`parser_`/`exporter_`), so "cimxml" is never ambiguous between the parser
and the exporter:

| Kind | Policy | Auto order | Availability probes (`requires`) |
|------|--------|-----------|----------------------------------|
| parser_cimxml | auto | cython_pugixml_arrow → python_lxml_arrow → python_lxml_pandas | compiled ext, pyarrow |
| sparql | auto | qlever → oxigraph → rdflib | `._qlever`+pyarrow, pyoxigraph, rdflib |
| validation | auto | polars → pandas → pyshacl (duckdb explicit-only) | polars, duckdb, pyshacl+rdflib |
| exporter_nquads | auto | polars → pandas | polars |
| exporter_cimxml | auto | cython_pugixml → python_lxml | compiled ext, pyarrow |
| exporter_csv | **input** | — (engine = input flavor) | polars |
| tools | **input** | — (engine = input flavor) | polars, duckdb |

`policy="input"` marks the frame-bound subsystems: csv exists as two engines
because each is fastest for its own input flavor, and tools operates on the
caller's frame — neither may be steered by a global override, and their
`engines()` row reports `engine: None, source: "input"`.

`cgmes_tools` is deliberately NOT a registry: its data functions run natively
for pandas and polars input, while duckdb/arrow input crosses the pandas
boundary (`to_pandas(plain=True)` → pandas engine → `match_flavor` back) —
the engines mutate VALUE in place, which needs a plain materialized frame.
That is a design decision, not an unfinished migration.

User-facing controls: `triplets.engines()` reports what `"auto"` resolved to
per subsystem (plus available alternatives); `triplets.set_engine(parser_cimxml=...,
sparql=..., ...)` overrides it globally (loads eagerly, fails fast).
Precedence: per-call `engine=` > `set_engine()` > auto probe order.
`set_engine` is process-global startup configuration, not a per-thread
control — concurrent code passes `engine=` per call. Per-call capability
constraints still apply regardless of overrides (cimxml `datatypes=True`
requires python_lxml; validation keeps duckdb out of auto because it is the
explicit larger-than-memory choice).

## IRI contract (`triplets.iri`)

Short triplet names ↔ absolute IRIs is defined once, in `triplets/iri/`. Never
split on `#`, strip `urn:uuid:` or prepend `CIM100#` at a call site — pick the
function for the column:

| column / context | local | absolute |
|---|---|---|
| `ID`, `INSTANCE_ID`, focus node, graph | `local_id` | `absolute_id` |
| `KEY` | `local_key` (`rdf:type` → `Type`, else `local_name`) | `absolute_key` |
| `VALUE` of `Type` (class) | `local_name` = `split_name(…)[1]` (after the last `#` or `/`: the XML local name) | `absolute_value` |
| `VALUE` (reference, enum) | `local_value` (`local_id`, then `#frag`; never `/`) | `absolute_value` → `(kind, payload)` |
| RDF term read back (subject / object) | `local_node` / `local_object` (decode, then the rows above) | — |
| SHACL / RDFS vocabulary, `sh:in`, SARIF, `schema_ir` | `local_name` (the same name rule) | not instance data |
| name → namespace **and** local name | `split_name` (`rdfs_tools.get_namespace_and_name` builds on it) | — |

- The absolute side takes flat maps built from `rdf_map`, one function per
  map, each a single comprehension over `rdf_map_entries` (all profile
  sections, first occurrence wins): `namespaces` (name → namespace IRI),
  `key_types` (name → schema entry type), `value_types` (KEY → `literal` /
  `reference` / `enum`, by entry type only), `datatypes` (Attribute KEY → xsd
  IRI, `None` = string; anyURI included). No cache — each is ~2 ms on a 2.8 MB
  schema, a content key cost 6x that. Public entry points (`validate`,
  `validate_schema`, `export_to_*`, the qlever ingest) call `load_rdf_map`
  once; below them only dicts flow, so a path is parsed once per call.
- **The schema entry type decides the serialisation form, never `xsd:type`**
  and never the shape of the text: an Attribute value is a literal even when
  it reads `https://…` or looks like a UUID (`xsd:type` only annotates it), an
  Association value is `absolute_id(value)` (absolute passes through, else
  `urn:uuid:`), an Enumeration value an enum IRI. The absolute-IRI / canonical-
  UUID heuristic applies only to a KEY the schema does not declare.
  `node_kind` gives the SHACL engines the same decision for `sh:nodeKind`.
- An IRI stays an IRI: `absolute_id` / `absolute_name` percent-encode what an
  IRIREF may not hold (`encode_iri`); the N-Quads / SPARQL readers reverse only
  those escapes (`decode_iri`). Literals are never encoded.
- **Undefined names** — a class not in the schema (abstract CIM classes such as
  `Equipment` included), a KEY not in it, an enum value not in it, and every
  name when there is no `rdf_map` — take `undefined_namespace`.
  `absolute_name` has no schema branch: the map answers or the parameter does.
  `iri.defined(key, value, namespaces, value_types)` names the row-level test.
  Every exporter has the same two knobs: `export_undefined` (write such rows
  or drop them — N-Quads keeps them by default, CIM XML drops them) and
  `undefined_namespace` (default `http://triplets#`). `sparql.query`,
  `validate`, `validate_schema` and `export_to_shacl_report` always load every
  row and default the namespace to CIM100 so schema-less data answers `cim:`
  queries; the same frame therefore exports `triplets#Equipment` and is
  queried as `CIM100#Equipment` unless the parameter is passed.
- Flavors: `iri_pandas` (Series in/out), `iri_polars` (Expr in/out, no UDFs),
  `iri_duckdb` (SQL text in/out) carry the same names.
  `__init__` imports only the standard library — the parser imports it per file.
- **Types:** `Type` is the typed-node element name (one per CIM object), `rdf:type`
  a reserved KEY for explicit `rdf:type` statements; the local name `type` is taken
  by `dcterms:type` in every shipped schema. Design and current gaps:
  [parsers.md — Types](parsers.md#types-type-vs-rdftype).
- **Importers have two sources of truth**, each held to the rows above:

  | importer | rules from |
  |---|---|
  | CIM XML: `python_lxml_pandas`, `python_lxml_arrow`, `rdf_parser.load_RDF_to_list` | `parser.utils.iter_rdf_rows` — the one python row loop |
  | CIM XML: `cython_pugixml_arrow` | C++ mirror of that loop (`clean_id`, `clean_ref_value`, `local_name`) |
  | `read_nquads`, oxigraph / qlever CONSTRUCT (`terms_to_triplets`), rdflib CONSTRUCT, `sh:sparql` results, the pyshacl report | `iri.local_node` / `iri.local_object` / `iri.local_key` |
  | SPARQL SELECT | none — absolute IRIs as stored |

  The XML side splits element QNames natively (XML already separates namespace
  and local name, so no IRI string exists for `local_key`) and finds the RDF
  attributes by namespace (the cython engine resolves the prefix the root binds to
  it). `test_parse_engines_follow_iri_rules` derives every object row from lxml's
  resolved QNames with the `triplets.iri` functions and requires each parser to
  produce exactly those rows, for any RDF prefix. The XML side has no `%XX`
  decoding and no subject join (see *Known limitations* in parsers.md).
- Native mirrors (cython parser `clean_id`/`clean_ref_value`, qlever C++
  `isUri`/`isUuid`/`namespaceFor`/`encodeIri`) are commented as such and checked by the
  parse / ingest parity tests. Vectorized patterns derive from the constants
  (`ID_PREFIX_RE`, `URI_PREFIXES` / `URI_PREFIX_RE`, `UUID_RE.pattern`), so a prefix change
  cannot miss a flavor.
- `UUID_RE` (strict lowercase, export rule) and `REFERENCE_LIKE` (loose
  nodeKind heuristic) are different contracts on purpose.
- CIM XML export is *not* on `absolute_id`: `rdf:about` / `rdf:resource` prefixes
  are the per-class schema `value_prefix`, and enum namespaces come from the
  resolved instance profile map (both CIM XML engines agree); only the
  `rdf:datatype` annotation uses `iri.datatypes`.

## Flavor conversion

Triplet data arrives as pandas, polars, pyarrow, or a DuckDB connection. Convert
at subsystem boundaries with **`triplets._engine_detect`** — do not add local
`if polars / if duckdb` materialization blocks:

| Helper | Role |
|--------|------|
| `flavor(data)` | `"pandas"` / `"polars"` / `"pyarrow"` / `"duckdb"` |
| `to_pandas(data, plain=False, …)` | any → pandas (`plain=True` for mutation-safe cgmes frames) |
| `to_arrow(data, columns=…, …)` | any → pyarrow (DuckDB uses native arrow, no pandas) |
| `to_polars(data, …)` | any → polars (Arrow path for duckdb/pyarrow) |
| `as_frame(data)` | pandas/polars unchanged; arrow/duckdb → pandas |
| `match_flavor(result, template)` | pandas result → template's flavor (cgmes dispatch) |
| `to_return_type(frame, return_type)` | pandas frame → "pandas"/"polars"/"arrow" (explicit return_type params) |

DuckDB table/schema defaults stay in `tools.duckdb_engine`; converters pass
optional `table` / `schema` / `table_name` through `_resolve_table`.

## Polars Engine Guidance

The lazy validation engine's design rules. Speed always wins over memory:

- Build **one LazyFrame plan per IR constraint** against a shared `.lazy()`
  base; execute everything with a single `polars.collect_all(plans)` (parallel
  execution + common-subplan elimination). Pre-materialize shared indices once
  (per-Type row index, the set of all IDs) and reuse them across plans.
- Use expressions only (`polars.col`), `Categorical`/`Enum` dtype for KEY and
  Type, `.cast(strict=False)` + null-check for datatype casts,
  `str.contains(literal=True)` when no regex is needed, join-based membership
  for large `sh:in` lists (`is_in` only for small ones).
- Avoid `map_elements`/`map_rows` (Python UDFs serialize execution),
  per-constraint eager `.collect()`, `.to_pandas()` round-trips mid-pipeline,
  `iter_rows`, object dtype, eager `pivot` on large frames.
- No streaming collect — that trades speed for memory, which is the duckdb
  engine's job. Keep the base frame and indices materialized; rechunk once
  after load.
