### About:

 - Parses CIM RDF/XML data to pandas dataframe with 4 columns [ID, KEY, VALUE, INSTANCE_ID] (triplestore like)
 - The solution does not care about CIM version nor namespaces
 - Input files can be xml or zip files (containing one or mutiple xml files)
 - All files are parsed into one and same Pandas DataFrame, thus if you want single file or single data model, you need to filter on INSTANCE_ID column

### Documentation:
https://haigutus.github.io/triplets

### To get started:

```shell
python -m pip install triplets
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

| Engine | Requires | Speed |
|--------|----------|-------|
| `python_lxml_pandas` | lxml + pandas (core) | 1x baseline, **always works** |
| `python_lxml_arrow` | + pyarrow (`pip install triplets[arrow]`) | ~1x, better interop |
| `cython_pugixml_arrow` | + C++ build | 9.8x |

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

# polars
data = polars.read_rdf(["grid_EQ.xml"], return_type="polars")

# return Arrow directly
table = triplets.parser.parse(path, return_type="arrow")

# direct API call (same as read_RDF)
data = triplets.parser.parse(["f.xml"], engine="python_lxml_pandas")
```

To build the performance engine (no system C++ deps needed):
```shell
pixi install -e build
pixi run build-cython-pugixml-arrow
```

See `pixi.toml` for build environment and `tests/test_parser_backends.py` for usage.
See [docs/parsers.md](docs/parsers.md) for the full call sequence and architecture.
