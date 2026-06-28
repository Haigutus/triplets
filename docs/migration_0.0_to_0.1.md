# Migration: 0.0 → 0.1

> **Single source of truth:** edit this file only. The published docs include it
> from `docs/source/guides/migration_0.0_to_0.1.md` via MyST `{include}`.

## Breaking changes

#### Python 3.11 minimum

Python 3.10 is no longer supported. The `StrEnum` class (used internally) requires 3.11+.

### `to_networkx()` renamed to `export_to_networkx()`

```python
# Old
graph = data.to_networkx()

# New
graph = data.export_to_networkx()
```

### Arrow-backed dtypes (when using pyarrow engines)

When `triplets[arrow]` is installed, the `python_lxml_arrow` and `cython_pugixml_arrow` engines
return DataFrames with `string[pyarrow]` and `dictionary<...>[pyarrow]` column dtypes instead of
plain `object`/`str`.

This affects code that:
- Uses `.str` accessor on filtered columns — may fail with `"Can only use .str accessor with string values"`
- Checks `dtype == object` or `dtype == str`
- Does in-place mutation on categorical columns

**Fix:** Convert to plain strings where needed:

```python
# Before (may fail with pyarrow dtypes)
data[data.KEY.str.contains(pattern)]

# After (works with any dtype)
data[data["KEY"].astype(str).str.contains(pattern)]
```

To avoid arrow dtypes entirely, use the pandas-only engine:

```python
data = pandas.read_RDF(files, engine="python_lxml_pandas")  # always returns plain str dtypes
```

### Dictionary-encoded KEY and INSTANCE_ID columns

With arrow engines, `KEY` and `INSTANCE_ID` columns are dictionary-encoded (categorical) by default.
This saves ~60% memory but may affect code that expects plain string columns:

```python
# Check if a column is categorical
print(data["KEY"].dtype)  # dictionary<values=string, indices=int32, ordered=0>[pyarrow]

# Convert to plain strings if needed
data["KEY"] = data["KEY"].astype(str)
```

## Deprecations

All functions in `rdf_parser.py` now emit `DeprecationWarning` and delegate to the new modules.
They will be removed in 0.2.

### Import paths

| Old (0.0) | New (0.1) |
|-----------|-----------|
| `triplets.rdf_parser.type_tableview` | `triplets.tools.type_tableview` |
| `triplets.rdf_parser.key_tableview` | `triplets.tools.key_tableview` |
| `triplets.rdf_parser.types_dict` | `triplets.tools.types_dict` |
| `triplets.rdf_parser.filter_by_type` | `triplets.tools.filter_triplets_by_type` |
| `triplets.rdf_parser.references_to` | `triplets.tools.references_to` |
| `triplets.rdf_parser.export_to_excel` | `triplets.export.export_to_excel` |
| `triplets.rdf_parser.export_to_csv` | `triplets.export.export_to_csv` |
| `triplets.rdf_parser.export_to_cimxml` | `triplets.export.export_to_cimxml` |
| `triplets.rdf_parser.export_to_networkx` | `triplets.export.export_to_networkx` |
| All other `rdf_parser.*` query/filter/diff functions | `triplets.tools.*` |

The old `filter_by_type` name (and other pre-0.1 tool names below) still work via
compatibility aliases but emit `DeprecationWarning`.

### tools renames

Function names follow `<action>_<format>_<qualifier>` since 0.1 ("triplets" = the
long ID/KEY/VALUE/INSTANCE_ID table, "tableview" = the pivoted per-class table).
Old names keep working but emit `DeprecationWarning`; they will be removed in 0.2.

| Old (0.0) | New (0.1) |
|-----------|-----------|
| `filter_by_type` | `filter_triplets_by_type` |
| `filter_by_triplet` | `filter_triplets_by_triplets` |
| `set_VALUE_at_KEY` | `set_value_at_key` |
| `set_VALUE_at_KEY_and_ID` | `set_value_at_key_and_id` |
| `update_triplet_from_triplet` | `update_triplets_from_triplets` |
| `update_triplet_from_tableview` | `update_triplets_from_tableview` |
| `remove_triplet_from_triplet` | `remove_triplets_from_triplets` |
| `triplet_to_tableviews` | `triplets_to_tableviews` |
| `tableview_to_triplet` | `tableview_to_triplets` |
| `tableviews_to_triplet` | `tableviews_to_triplets` |
| `diff_between_triplet` | `diff_triplets` |
| `diff_between_INSTANCE` | `diff_triplets_by_instance` |
| `print_triplet_diff` | `print_triplets_diff` |

> Note: 0.1.0rc4 briefly misnamed these two as `set_triplets_value_by_key(_and_id)`;
> corrected before 0.1.0 — treated as an rc-only bug, no compatibility aliases.

The renames apply to `triplets.tools.*`, root methods on DataFrames/connections, and the
`.triplets` accessor namespace (pandas, polars, and DuckDB).

### cgmes_tools renames

Old names keep working in 0.1 but emit `DeprecationWarning`; they will be removed in 0.2.

| Old (0.0) | New (0.1) |
|-----------|-----------|
| `cgmes_tools.draw_relations_to` | `cgmes_tools.draw_references_to` |
| `cgmes_tools.draw_relations_from` | `cgmes_tools.draw_references_from` |
| `cgmes_tools.draw_relations` | `cgmes_tools.draw_references` |
| `cgmes_tools.statistics_GeneratingUnit_types` | `cgmes_tools.count_GeneratingUnit_types` |
| `cgmes_tools.generate_instances_ID` | `cgmes_tools.generate_instance_ids` |
| `cgmes_tools.get_model_data` | `cgmes_tools.get_model_triplets` |

Visualization helpers were renamed from `draw_relations_*` to `draw_references_*` so they
align with `tools.references_*`. The graph renderer is internal (`_draw_references_graph`);
there is no public `draw_relations_graph` API in 0.1.

### Triplets are always strings (or null)

Since 0.1, the tools enforce that `ID`/`KEY`/`VALUE` contain only strings or
nulls — never raw numbers or other objects:

- `tableview_to_triplets` (and the tableview update functions built on it)
  returns a nullable `string` dtype: numbers from `string_to_number=True`
  tableviews become text, and empty tableview cells stay **null** — previously
  they became literal `"nan"` strings.
- `set_value_at_key` / `set_value_at_key_and_id` normalize
  values with `str()`; `None` stays null (previously polars stored the literal
  string `"None"`, and pandas let raw ints into `VALUE` — which crashed the
  compiled CIM XML export with `Expected bytes, got a 'int' object`).

If your code built triplet rows with numeric `VALUE`s by hand, the exports now
tolerate them, but converting with `data["VALUE"].astype(str)` keeps your data
within the contract.

### `export_to_cimxml` exports schema-defined content only by default

`export_undefined` now defaults to **False**: internal structures
(`Distribution`, `NamespaceMap` and any class/attribute without a schema
definition) are no longer written into CIM XML exports. Pass
`export_undefined=True` to include them — they are emitted under the
`http://triplets#` namespace (making the output valid, strict-parser-safe
RDF/XML; previously they were non-namespaced elements).

Profile resolution is schema-driven now: the schema's own `ProfileMetadata`
(`keyword`, `versionIRI`, `conformsTo`) is matched against the instance
header (`Model.messageType`, `keyword`, `Model.profile`, `conformsTo`), so
CGMES 2.4.15, CGMES 3.0 and NetworkCode headers all resolve to the right
profile section.

### Accessor namespace

The old monkey-patched root methods (`data.type_tableview(...)`) still work but the
recommended approach is the `.triplets` namespace:

```python
# pandas / polars — recommended
df.triplets.type_tableview("ACLineSegment")

# DuckDB connection — same method names, different object
con.triplets.type_tableview("ACLineSegment").df()
```

All three backends expose the same tool and export names on `.triplets`. DuckDB
results are relations — call `.df()` or `.pl()` when you need a DataFrame.
Root-level methods remain on pandas/polars DataFrames and DuckDB connections for
backwards compatibility.

## New module structure

```
triplets/
├── parser/          # parse XML to DataFrames (3 engines)
├── tools/           # query, filter, diff, transform (pandas + polars + duckdb)
├── export/          # Excel, CSV, CIM XML, N-Quads, NetworkX
├── cli/             # cim-spreadsheet, cim-diff CLI tools
├── cgmes_tools/     # CGMES metadata, visualization, data quality
├── rdfs_tools/      # RDF Schema utilities
├── export_schema/   # ENTSO-E JSON schema files
├── _accessor.py     # .triplets namespace registration
└── rdf_parser.py    # deprecated shim (will be removed in 0.2)
```