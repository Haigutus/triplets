### About:

 - Parses CIM RDF/XML data to pandas dataframe with 4 columns [ID, KEY, VALUE, INSTANCE_ID] (triplestore like)
 - The solution does not care about CIM version nor namespaces
 - Input files can be xml or zip files (containing one or mutiple xml files)
 - All files are parsed into one and same Pandas DataFrame, thus if you want single file or single data model, you need to filter on INSTANCE_ID column

### Documentation:
https://haigutus.github.io/triplets

### To get started:

```shell
# Core (python_lxml_pandas engine, no extra deps)
pip install triplets

# With pyarrow (enables python_lxml_arrow + cython_pugixml_arrow engines, ~12x faster)
pip install triplets[arrow]
```

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
data.export_to_cimxml(
    rdf_map=schemas.ENTSOE_CGMES_2_4_15_552_ED1,
    export_type=ExportType.XML_PER_INSTANCE_ZIP_PER_XML,
)
```


Look into examples folders for more

## Parser engines

Three parser engines with automatic fallback (fastest available):

| Engine | Install | Speed |
|--------|---------|-------|
| `python_lxml_pandas` | `pip install triplets` | 1x baseline, **always works** |
| `python_lxml_arrow` | `pip install triplets[arrow]` | ~1x, better interop |
| `cython_pugixml_arrow` | `pip install triplets[arrow]` (included in wheels) | **12x faster** |

The `cython_pugixml_arrow` engine is a compiled C++ extension included in published wheels.
It requires pyarrow at runtime, so install with `triplets[arrow]` to enable it.

The cython engine is pre-built in published wheels — no compilation needed.


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
```

## DuckDB

```python
import duckdb
import triplets

data = duckdb.connect()                              # in-memory
data = duckdb.connect("grid.duckdb")                 # persistent (no re-parsing next session)

data.read_rdf(["grid_EQ.xml", "data.zip"])           # parse via Arrow (zero-copy into DuckDB)
data.get_types_count()                                     # → dict
data.tableview_by_type("ACLineSegment").df()             # → pandas DataFrame
data.tableview_by_type("ACLineSegment").pl()             # → polars DataFrame
data.filter_triplets(KEY="Type", VALUE=".*Sub.*", regex=True).df()
data.filter_triplets_by_type("Terminal").df()
data.references_to("some-uuid").df()
data.export_to_nquads("/tmp/output.nq")

# Direct SQL (full DuckDB SQL on the triplets table)
data.sql("SELECT VALUE, COUNT(*) FROM triplets WHERE KEY = 'Type' GROUP BY VALUE").df()

# The same tools are also on the `.triplets` namespace (parity with pandas/polars)
data.triplets.tableview_by_type("ACLineSegment").df()
data.triplets.get_types_count()
```

## Accessor namespace

All engines — including a DuckDB connection (`con.triplets.*`) — share the same
`df.triplets.*` accessor:

```python
data.triplets.tableview_by_type("ACLineSegment")
data.triplets.get_types_count()
data.triplets.filter_triplets(KEY="Type")
data.triplets.export_to_excel(export_to_memory=True)
data.triplets.export_to_nquads("/tmp/output.nq")
```

## CLI tools

```shell
cim-spreadsheet -i model.xml -o output.xlsx
cim-diff original.xml modified.xml
```

## Performance (RealGrid, 1.14M rows)

| Operation | pandas | polars | DuckDB |
|-----------|--------|--------|--------|
| Parse (cython engine) | 128ms | 156ms | 283ms |
| tableview_by_type | 72ms | **21ms** | 53ms |
| filter_triplets_by_type | 103ms | **9ms** | 50ms |
| get_types_count | 21ms | **11ms** | 18ms |

The old `rdf_parser.py` functions still work but emit deprecation warnings.
