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
data.type_tableview("ACLineSegment")
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

```python
import pandas
import polars
import triplets  # registers pandas.read_RDF, polars.read_rdf etc.

# default (auto: best available engine)
data = pandas.read_RDF(["grid_EQ.xml", "data.zip"])

# explicit engine selection
data = pandas.read_RDF(path, engine="python_lxml_pandas")        # no pyarrow needed
data = pandas.read_RDF(path, engine="python_lxml_arrow")         # arrow intermediate
data = pandas.read_RDF(path, engine="cython_pugixml_arrow")      # fastest

# polars (returns polars DataFrame automatically)
data = polars.read_rdf(["grid_EQ.xml"])

# return Arrow directly
table = triplets.parser.parse(path, return_type="arrow")

# direct API call (same as read_RDF)
data = triplets.parser.parse(["f.xml"], engine="python_lxml_pandas")
```

The cython engine is pre-built in published wheels — no compilation needed.
For local development builds, see [docs/building.md](docs/building.md).

See [docs/parsers.md](docs/parsers.md) for the full call sequence and architecture.

## New in 0.1: Module structure

Functions from `rdf_parser.py` are now organized into dedicated modules:

```python
import triplets

# Parser (parse XML to DataFrames)
data = triplets.parser.parse(["grid.xml"])

# Tools (query, filter, diff, transform — pandas + polars engines)
triplets.tools.type_tableview(data, "ACLineSegment")
triplets.tools.types_dict(data)
triplets.tools.filter_by_type(data, "Terminal")

# Export (Excel, CSV, CIM XML, NetworkX)
triplets.export.export_to_excel(data, export_to_memory=True)
triplets.export.export_to_cimxml(data, rdf_map=rdf_map)

# Accessor namespace (same functions via df.triplets.*)
data.triplets.type_tableview("ACLineSegment")
data.triplets.export_to_excel(export_to_memory=True)

# CLI tools
# cim-spreadsheet -i model.xml -o output.xlsx
# cim-diff original.xml modified.xml
```

The old `rdf_parser.py` functions still work but emit deprecation warnings.

## Migrating from 0.0.x to 0.1

### Breaking changes

| Change | Old (0.0.x) | New (0.1) |
|--------|-------------|-----------|
| Minimum Python | 3.10 | **3.11** |
| `to_networkx()` | `data.to_networkx()` | `data.export_to_networkx()` |
| pyarrow dtypes | All columns `object`/`str` | Arrow engines return `string[pyarrow]` and `dictionary[pyarrow]` dtypes. Use `.astype(str)` if you need plain strings. |
| `.str` accessor | Works directly | With arrow dtypes, use `.astype(str).str.contains(...)` instead of `.str.contains(...)` |

### Deprecations (still work, will be removed in 0.2)

| Old | New |
|-----|-----|
| `triplets.rdf_parser.type_tableview(data, ...)` | `triplets.tools.type_tableview(data, ...)` |
| `triplets.rdf_parser.filter_by_type(data, ...)` | `triplets.tools.filter_by_type(data, ...)` |
| `triplets.rdf_parser.export_to_excel(data, ...)` | `triplets.export.export_to_excel(data, ...)` |
| All other `rdf_parser.*` functions | Corresponding `triplets.tools.*` or `triplets.export.*` |
| `pandas.DataFrame.type_tableview(...)` (monkey-patch) | `data.triplets.type_tableview(...)` (accessor) |

### New features

- **Three parser engines** with automatic fallback (`python_lxml_pandas`, `python_lxml_arrow`, `cython_pugixml_arrow`)
- **Polars support** — `polars.read_rdf()` + polars-native tools engine (1.6-18.7x faster)
- **`df.triplets.*` accessor** namespace for both pandas and polars
- **`engine=` parameter** on all tools functions for explicit pandas/polars selection
- **`multivalue=` parameter** on `type_tableview`, `export_to_excel`, `export_to_csv`
- **CLI tools** — `cim-spreadsheet` (CIM XML <-> Excel/CSV) and `cim-diff`
- **Cross-platform wheels** with compiled C++ parser (Linux, macOS, Windows)
