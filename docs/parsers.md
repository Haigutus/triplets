# Parser Architecture

> **Single source of truth:** edit this file only. The published docs include it
> from `docs/source/guides/parsers.md` via MyST `{include}`.

## Engines

Three parser engines with automatic fallback (fastest available):

| Engine | File | Requires | Speed | Peak Memory (RealGrid) |
|--------|------|----------|-------|------------------------|
| `python_lxml_pandas` | `parser/python_lxml_pandas.py` | lxml + pandas (core) | 1x baseline, **always works** | 314 MB |
| `python_lxml_arrow` | `parser/python_lxml_arrow.py` | + pyarrow (`pip install triplets[arrow]`) | ~1x parse, better interop | 145 MB |
| `cython_pugixml_arrow` | `parser/cython_pugixml_arrow.pyx` | + C++ build + pyarrow | 9.8x | 145 MB |

Fallback order: `cython_pugixml_arrow` -> `python_lxml_arrow` -> `python_lxml_pandas`

All three engines expose the same interface: `load_rdf_to_dataframe(path_or_fileobject, debug=False)`

Engine aliases: `performance` / `pugixml` -> `cython_pugixml_arrow`, `native` -> `python_lxml_pandas`

## Call Sequence

```
pd.read_RDF([paths])
|
'-> parser.parse(paths, engine="auto")
    |
    |-> get_engine("auto")
    |   try cython_pugixml_arrow    -> ImportError (not compiled)
    |   try python_lxml_arrow       -> ImportError (no pyarrow)
    |   fall back python_lxml_pandas -> always works
    |
    |-> find_all_xml(paths)
    |   |-> open .xml/.rdf files
    |   |-> extract from .zip (nested zips supported)
    |   '-> returns [file_obj, file_obj, ...]
    |
    |-> for each xml:
    |   '-> engine.load_rdf_to_dataframe(xml)
    |       |
    |       |  python_lxml_pandas          python_lxml_arrow          cython_pugixml_arrow
    |       |  -----------------           -----------------          --------------------
    |       |  etree.parse(xml)            etree.parse(xml)           mmap(xml) or read bytes
    |       |  iterate lxml tree           iterate lxml tree          pugixml C++ parse
    |       |  build Python list           Arrow StringBuilders       Arrow C++ builders
    |       |  pd.DataFrame(tuples)        pa.RecordBatch             pa.RecordBatch
    |       |        |                           |                          |
    |       |        v                           v                          v
    |       |   pd.DataFrame              pa.RecordBatch              pa.RecordBatch
    |       |
    |       '-> returns result
    |
    |-> combine:
    |   |-> pandas engine:  pd.concat(dataframes)
    |   '-> arrow engines:  pa.Table.from_batches(batches)
    |
    |-> categorical encoding:
    |   |-> pandas engine:  df[col].astype("category")
    |   '-> arrow engines:  pa.compute.dictionary_encode(col)
    |
    '-> convert to return_type:
        |-> "pandas"  -> df or table.to_pandas()
        |-> "arrow"   -> pa.Table
        '-> "polars"  -> pl.from_arrow(table)
```

## File Layout

```
triplets/parser/
|-- __init__.py              # parse() dispatcher, get_engine(), find_all_xml re-export
|-- utils.py                 # find_all_xml, clean_ID, _split_prefixed_name, RDF constants
|-- python_lxml_pandas.py    # lxml -> list of tuples -> pd.DataFrame (default)
|-- python_lxml_arrow.py     # lxml -> Arrow StringBuilders -> pa.RecordBatch
'-- cython_pugixml_arrow.pyx # pugixml C++ -> Arrow C++ builders -> pa.RecordBatch
```

## Usage

```python
import pandas
import polars
import triplets

# auto (best available engine)
data = pandas.read_RDF(["grid_EQ.xml", "data.zip"])

# explicit engine selection
data = pandas.read_RDF(path, engine="python_lxml_pandas")
data = pandas.read_RDF(path, engine="python_lxml_arrow")
data = pandas.read_RDF(path, engine="cython_pugixml_arrow")

# polars (return_type defaults to "polars")
data = polars.read_rdf(["grid_EQ.xml"])

# return Arrow or Polars directly
table = triplets.parser.parse(path, return_type="arrow")
data = triplets.parser.parse(path, return_type="polars")
```

## Debug Output

Debug output (file discovery, per-file parse timings, engine selection) follows the
Python logging level — no `debug=True` needed:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

data = pandas.read_RDF(["grid_EQ.xml"])  # debug output because logger is at DEBUG
```

Engine selection is logged at DEBUG level:

```
DEBUG triplets.parser: auto - test engine availability: cython_pugixml_arrow
DEBUG triplets.parser.cython_pugixml_arrow: [grid_EQ.xml] XML parse: 0:00:00.052368
```

## Building the Cython Engine

```shell
pixi install -e build
pixi run build-cython-pugixml-arrow
```

Or manually:

```shell
python setup_cython_parser.py build_ext --inplace
```

## Naming Convention

Engine files follow `{runtime}_{lib}_{output}`:

- **runtime**: `python` (pure Python) or `cython` (compiled)
- **lib**: XML library used (`lxml`, `pugixml`)
- **output**: what it produces (`pandas` DataFrame or `arrow` RecordBatch)