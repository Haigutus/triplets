# Export Architecture

> **Single source of truth:** edit this file only. The published docs include it
> from `docs/source/guides/exports.md` via MyST `{include}`.

## Formats and Engines

Each format has its own `{format}_{engine}.py` file. The dispatcher in
`export/__init__.py` picks the engine automatically.

| Format | Function | Engines | Selection |
|--------|----------|---------|-----------|
| CIM XML | `export_to_cimxml` | `cython_pugixml`, `python_lxml` | `engine` parameter, auto = fastest available |
| N-Quads | `export_to_nquads` | polars (lazy plan, ~4x), pandas | `engine` parameter, auto = polars when installed |
| CSV | `export_to_csv` | polars, pandas | by input DataFrame type |
| Arrow | `export_to_arrow` | polars, pandas, DuckDB (→ `pyarrow.Table`; DuckDB native, no pandas hop) | by input flavor; columnar interchange feeding qlever's Arrow ingest |
| Excel | `export_to_excel` | pandas | polars input converted to pandas first |
| NetworkX | `export_to_networkx` | pandas | polars input converted to pandas first |

## CIM XML Engines

Two engines with automatic fallback, mirroring the parser engine setup:

| Engine | File | Requires | Speed (RealGrid 1.14M rows) |
|--------|------|----------|------------------------------|
| `python_lxml` | `export/cimxml_pandas.py` | lxml + pandas (core), **always works** | 9.3 s |
| `cython_pugixml` | `export/cimxml_pugixml.py` + `cimxml_cython_pugixml.pyx` | + C++ build + pyarrow | 0.8 s (11.5x) |

Fallback order: `cython_pugixml` -> `python_lxml`

Input flavor: the cython engine consumes polars input directly — per-instance
frames stay polars and reach the extension as Arrow `large_string` /
dictionary columns through the shared accessor (`triplets/_arrow/
string_column.h`), no pandas hop. The python_lxml engine is pandas-only, so
polars input converts first; `datatypes=True` implies python_lxml under
`engine="auto"`.

DuckDB notes: `con.read_rdf(paths)` streams one Arrow batch per XML file into
the table (`append=True` adds instead of replacing) — see
[parsers.md](parsers.md). In the other direction `con.export_to_nquads`
streams batch-by-batch through duckdb's record-batch reader (bounded memory —
works larger-than-RAM; needs the polars engine). The remaining formats fetch
the table through duckdb's native arrow result path (~4x cheaper than a
pandas materialization) but hold the whole table in memory during the export;
streaming cimxml is the recorded TODO follow-up.

Both engines expose the same interface:
`generate_xml(instance_data, rdf_map, namespace_map, class_KEY, export_undefined, comment, debug, datatypes)`
returning `{"filename": str, "file": bytes}` for one instance. They produce
data-identical XML (verified by an engine-equivalence test); only whitespace
formatting differs.

Engine aliases: `performance` / `pugixml` -> `cython_pugixml`, `lxml` / `pandas` -> `python_lxml`

`export_to_cimxml(..., datatypes=True)` annotates literal elements with
`rdf:datatype` from the schema's `xsd:type` (like N-Quads; `xsd:string` stays
plain). Supported by `python_lxml` only — passing `datatypes=True` with
`engine="auto"` forces the `python_lxml` engine.

## Call Sequence (CIM XML)

```
data.export_to_cimxml(rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)
|
'-> export.export_to_cimxml(data, engine="auto")
    |
    |-> get_cimxml_engine("auto")
    |   try cython_pugixml -> ImportError (not compiled)
    |   fall back python_lxml -> always works
    |
    |-> _split_instances(data)        # one XML document per instance,
    |                                 # in the input's own flavor (polars partition_by / pandas groupby)
    |
    |-> for each instance:
    |   '-> engine.generate_xml(instance, rdf_map, ...)
    |       |
    |       |-> cimxml_utils.resolve_instance_config()
    |       |   |-> file_name from 'label' (source filename) or new UUID
    |       |   |-> profile sub-schema via Model.messageType / Model.profile
    |       |   '-> namespace map: given > instance NamespaceMap > schema
    |       |
    |       |  python_lxml                     cython_pugixml
    |       |  -----------                     --------------
    |       |  lxml ElementMaker               Arrow string arrays (utf8/large_utf8/string_view/dict)
    |       |  per-row Python loop             C++ loop over Arrow buffers
    |       |  etree.tostring()                pugixml DOM -> serialize
    |       |        |                                |
    |       |        v                                v
    |       '-> {"filename": ..., "file": xml bytes}
    |
    '-> package per export_type:
        |-> XML_PER_INSTANCE             -> one BytesIO per XML
        |-> XML_PER_INSTANCE_ZIP_PER_ALL -> all XMLs in one ZIP
        '-> XML_PER_INSTANCE_ZIP_PER_XML -> each XML in its own ZIP (default)
```

## File Layout

```
triplets/export/
|-- __init__.py                # dispatchers: export_to_cimxml() + engine registry,
|                              # export_to_csv(), export_to_nquads(), ExportType
|-- cimxml_utils.py            # shared per-instance config resolution (both cimxml engines)
|-- cimxml_pandas.py           # generate_xml() via lxml (default)
|-- cimxml_pugixml.py          # generate_xml() via compiled extension
|-- cimxml_cython_pugixml.pyx  # Arrow -> pugixml C++ -> XML bytes
|-- csv_pandas.py              # CSV via pandas
|-- csv_polars.py              # CSV via polars
|-- nquads_utils.py            # shared N-Quads formatting helpers
|-- nquads_pandas.py           # N-Quads via pandas
|-- nquads_polars.py           # N-Quads via polars
|-- excel_pandas.py            # Excel via openpyxl
'-- networkx_pandas.py         # NetworkX graph
```

## Usage

```python
import pandas
import triplets
from triplets.export_schema import schemas

data = pandas.read_RDF(["grid.zip"])

# CIM XML — auto picks the fastest available engine
files = data.export_to_cimxml(
    rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
    export_type="xml_per_instance",
    export_to_memory=True,
)

# explicit engine selection
files = data.export_to_cimxml(rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, engine="python_lxml")
files = data.export_to_cimxml(rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, engine="cython_pugixml")

# N-Quads (fast input for SPARQL engines like qlever); schema enables enum namespaces.
# N-Quads output is serialization-edition-independent — references are always
# emitted as absolute urn:uuid: IRIs, so ED1 and ED2 produce identical, valid
# input for any SPARQL engine (the ED1 "#uuid" fragment-reference pitfall is a
# CIM XML / RDF-XML concern, not N-Quads). ED2 shown for consistency.
data.export_to_nquads("grid.nq", rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED2)

# CIM XML with rdf:datatype annotations (python_lxml only; forces that engine)
files = data.export_to_cimxml(rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, datatypes=True)

# Arrow — columnar interchange (ID, KEY, VALUE, INSTANCE_ID) for qlever's Arrow ingest
table = data.export_to_arrow()  # pyarrow.Table, zero-copy where the backing store allows

# other formats
data.export_to_csv(export_to_memory=True)
data.export_to_excel(export_to_memory=True)
graph = data.export_to_networkx()

# accessor namespace (pandas / polars)
data.triplets.export_to_cimxml(rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)

import duckdb
con = duckdb.connect()
con.read_rdf(["grid.zip"])
con.export_to_nquads("grid.nq")
con.triplets.export_to_csv(export_to_memory=True)
```

## Building the Cython Engine

The same build produces both compiled extensions (parser and CIM XML export).
First fetch the pugixml source (`setup_cython_parser.py` hard-errors without it):

```shell
git submodule update --init vendor/pugixml
```

Then build:

```shell
pixi install -e build
pixi run build-cython-pugixml-arrow
```

Or manually:

```shell
python setup_cython_parser.py build_ext --inplace
```

## Naming Convention

Engine files follow `{format}_{engine}.py`:

- **format**: what is produced (`cimxml`, `csv`, `nquads`, `excel`, `networkx`)
- **engine**: what does the work (`pandas`, `polars`, `pugixml`)

Shared format helpers live in `{format}_utils.py`.