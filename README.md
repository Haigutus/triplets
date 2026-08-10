### About:

 - Parses CIM RDF/XML data to pandas dataframe with 4 columns [ID, KEY, VALUE, INSTANCE_ID] (triplestore like)
 - The solution does not care about CIM version nor namespaces
 - Input files can be xml or zip files (containing one or mutiple xml files)
 - All files are parsed into one and same Pandas DataFrame, thus if you want single file or single data model, you need to filter on INSTANCE_ID column

### Documentation:
https://haigutus.github.io/triplets

Upgrading from 0.0.x? See [docs/migration_0.0_to_0.1.md](docs/migration_0.0_to_0.1.md).

### To get started:

```shell
# Core (python_lxml_pandas engine, no extra deps)
pip install triplets

# With pyarrow (enables python_lxml_arrow + cython_pugixml_arrow engines, ~10x faster)
pip install triplets[arrow]
```

Install extras by feature:

| Extra | Enables |
|-------|---------|
| `arrow` | compiled Arrow parser engines (~10x faster parsing) |
| `polars` | polars DataFrames (`polars.read_rdf`, `.triplets` namespace) |
| `duckdb` | DuckDB connections (`con.read_rdf`, SQL over triplets) |
| `sparql` | SPARQL queries (rdflib reference engine) |
| `oxigraph` | **recommended pip performance path** — embedded Rust SPARQL engine (auto-preferred over rdflib) |
| `validation` | SHACL validation (pyshacl reference engine) |
| `excel` / `networkx` / `visualization` | Excel export / graph export / drawing |

The embedded qlever SPARQL engine (fastest) ships in no wheel — it is a local
source build, see [docs/building.md](docs/building.md).

```python
import pandas
import triplets

path = "CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"
data = pandas.read_RDF([path])
```

### Result:

![image](https://user-images.githubusercontent.com/11408965/64228384-53350500-ceef-11e9-9a8b-473ed1dc6e4d.png)


You can then query a dataframe of all same type elements and its parameters across all [EQ, SSH, TP, SV etc.] instance files, where parameters are columns and index is object ID-s

```python
data.tableview_by_type("ACLineSegment")
```

![image](https://user-images.githubusercontent.com/11408965/64228433-7eb7ef80-ceef-11e9-81d4-43e39ecf099d.png)


### Export:

```python
from triplets.export_schema import schemas
from triplets.export import ExportType

data.export_to_cimxml(
    rdf_map=schemas.ENTSOE_CGMES_2_4_15_552_ED1,
    export_type=ExportType.XML_PER_INSTANCE_ZIP_PER_XML,
)
```

Export schemas are versioned and shipped per profile. Alongside the CGMES
bundles (`schemas.ENTSOE_CGMES_2_4_15_552_ED1`,
`schemas.ENTSOE_CGMES_3_0_0_552_ED1`, …) the versioned **NC (Network Code)**
profiles are available as `schemas.ENTSOE_NC_2_4_1_552_ED1` /
`schemas.ENTSOE_NC_2_4_1_552_ED2`. The `_ED1` / `_ED2` suffix selects the
serialization edition; profile resolution is schema-driven, so the right
profile section is matched from the instance header.

Look into examples folders for more

## Parser engines

Three parser engines with automatic fallback (fastest available):

| Engine | Install | Speed |
|--------|---------|-------|
| `python_lxml_pandas` | `pip install triplets` | 1x baseline, **always works** |
| `python_lxml_arrow` | `pip install triplets[arrow]` | ~1x, better interop |
| `cython_pugixml_arrow` | `pip install triplets[arrow]` (included in wheels) | **~10x faster** |

The `cython_pugixml_arrow` engine is a compiled C++ extension included in published wheels.
It requires pyarrow at runtime, so install with `triplets[arrow]` to enable it.

The cython engine is pre-built in published wheels — no compilation needed.

Engine selection is automatic across the library (parser, exports, SPARQL,
validation): installing an extra makes everything that can use it faster, with
no code changes. Inspect and steer it globally:

```python
triplets.engines()                        # what "auto" resolved to, per subsystem
triplets.set_engine(parser_cimxml="python_lxml_pandas", sparql="rdflib")
triplets.set_engine(parser_cimxml="auto") # restore auto-selection
```

Per-call `engine=` arguments always win over `set_engine`. Operations on your
DataFrame itself (filters, tableviews, references) always run in the engine of
the object you call them on — pandas frames stay pandas, polars stays polars.


## Polars

```python
import polars
import triplets

data = polars.read_rdf(["grid_EQ.xml", "data.zip"])   # returns polars DataFrame

data.triplets.get_types_count()
data.triplets.tableview_by_type("ACLineSegment")
data.triplets.filter_triplets(KEY="Type", VALUE=".*Generator.*", regex=True)
data.triplets.export_to_csv(export_to_memory=True)
data.triplets.export_to_nquads("/tmp/output.nq")
data = polars.read_nquads("/tmp/output.nq")           # round-trips N-Quads back to triplets
```

`read_nquads` is registered as `pandas.read_nquads` / `polars.read_nquads` and
is also exposed top-level as `triplets.read_nquads`.

## DuckDB

```python
import duckdb
import triplets

data = duckdb.connect()                              # default table "triplets"
data = duckdb.connect("grid.duckdb", table="grid", schema="cim")  # per-connection defaults
# explicit table/schema config is stored in the database file — reopening
# duckdb.connect("grid.duckdb") later resolves cim.grid automatically

data.read_rdf(["grid_EQ.xml", "data.zip"])           # streams into the connection's table
data.read_rdf(["update.zip"], append=True)           # adds rows instead of replacing
data.get_types_count()                               # uses connection table/schema
data.tableview_by_type("ACLineSegment").df()
data.filter_triplets(KEY="Type", VALUE=".*Sub.*", regex=True).df()
data.references_to("some-uuid").df()
data.export_to_nquads("/tmp/output.nq")

# Per-call override; rebind defaults with set_triplets_table(...)
data.types_dict(table="other", schema="main")

# Direct SQL (your identifiers — tools always quote theirs)
data.sql('SELECT VALUE, COUNT(*) FROM "cim"."grid" WHERE KEY = \'Type\' GROUP BY VALUE').df()

# The same tools are also on the `.triplets` namespace (parity with pandas/polars)
data.triplets.tableview_by_type("ACLineSegment").df()
data.triplets.get_types_count()
```

## SPARQL queries

SPARQL 1.1 over the loaded data — `SELECT` → DataFrame, `ASK` → bool,
`CONSTRUCT` → triplet DataFrame. Works on pandas, polars and DuckDB inputs:

```python
PREFIXES = """
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX cim: <http://iec.ch/TC57/CIM100#>
"""
names = data.sparql.query(PREFIXES + "SELECT ?s ?name WHERE { ?s cim:IdentifiedObject.name ?name }")
```

Three engines behind one API (auto picks the fastest available):

| Engine | Install | Role |
|--------|---------|------|
| `qlever` | local source build ([docs/building.md](docs/building.md)) | fastest — embedded C++, persistent on-disk index |
| `oxigraph` | `pip install triplets[oxigraph]` | embedded Rust — ~3x faster import, 2–5x faster queries than rdflib |
| `rdflib` | `pip install triplets[sparql]` | pure-Python reference |

Details and measured numbers: [docs/sparql.md](docs/sparql.md).

## SHACL validation

Validate against SHACL shape files; the result is a violations DataFrame
(empty = conforms) with the same shape across all engines:

```python
from triplets.export_schema import schemas

violations = data.shacl.validate("shapes.ttl", rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)

# slower optional context pass: source file, object type/name,
# shape sh:name/sh:description, schema attribute/class definitions
violations = data.shacl.validate(shapes, context=True)

# SARIF 2.1.0 for GitHub / SonarQube / any SARIF viewer — grouped by default
# (one result per rule with occurrenceCount + sample instances)
violations.shacl.to_sarif(path="report.sarif")

# standard SHACL sh:ValidationReport — format from path suffix (or format=)
violations.shacl.to_shacl_report(path="report.ttl")
violations.shacl.to_shacl_report(path="report.xml", report_source="model.xml",
                                 report_references=["equipment.ttl"])
```

Engines: `polars` (auto, real profiles in ~2 s) → `pandas` → `pyshacl`
(reference); `duckdb` for larger-than-memory data. `sh:sparql` constraints
ride the SPARQL engine above (minutes → milliseconds with oxigraph/qlever).
Details: [docs/validation.md](docs/validation.md).

## Accessor namespace

pandas and polars DataFrames use `df.triplets.*`; a DuckDB connection uses
`con.triplets.*`. The same method names are available on both (DuckDB returns
relations — add `.df()` or `.pl()` when needed):

```python
# pandas / polars
df.triplets.tableview_by_type("ACLineSegment")
df.triplets.export_to_nquads("/tmp/output.nq")

# DuckDB
con.triplets.tableview_by_type("ACLineSegment").df()
con.triplets.get_types_count()
```

Root-level methods (`df.type_tableview(...)`, `con.filter_triplets(...)`) still
work for backwards compatibility.

## Cache lifecycle

Engines keep internal state (compiled shapes, SPARQL indexes) cached across
calls. Reset it explicitly with `triplets.clear_caches()`, or scope it to a
block so it is cleared on exit:

```python
import triplets

with triplets.cache_scope():
    ...            # caches populated here are dropped when the block exits

triplets.clear_caches()   # or clear everything manually
```

## CLI tools

```shell
cim-spreadsheet -i model.xml -o output.xlsx
cim-diff original.xml modified.xml
```

## Performance (RealGrid, 1.14M rows)

Committed benchmark results live in `tests/performance_results/`; re-run with
`pytest -m performance`. Representative numbers (cython parse 1.47s → 0.157s
vs the lxml engine = ~9.4x):

| Operation | pandas | polars | DuckDB |
|-----------|--------|--------|--------|
| Parse (cython engine) | 157ms | 180ms | streams (see duckdb section) |
| tableview_by_type | 72ms | **15ms** | 53ms |
| filter_triplets_by_type | 103ms | **9ms** | 50ms |
| get_types_count | 21ms | **11ms** | 18ms |

The old `rdf_parser.py` functions still work but emit deprecation warnings.
See [docs/migration_0.0_to_0.1.md](docs/migration_0.0_to_0.1.md) for renames and breaking changes.
