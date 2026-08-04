"""Triplet DataFrame export functions.

Formats: Excel, CSV, CIM XML, N-Quads, NetworkX.
Each format has its own {format}_{engine}.py file.

CIM XML engines (mirrors triplets.parser engine setup):
- python_lxml    (pure Python + lxml, always available)
- cython_pugixml (compiled Arrow → pugixml extension, fastest)
Fallback: cython_pugixml → python_lxml

Engine auto-selection differs by format (by design):
- CSV: by input type — a polars DataFrame uses the polars engine, else pandas.
- N-Quads: polars when installed (pandas input is converted), else pandas.
- CIM XML: the fastest available compiled engine (cython_pugixml → python_lxml).
- Excel / NetworkX: pandas only — polars input is converted to pandas first.
"""

import os
import logging
import zipfile
import datetime
import multiprocessing

from io import BytesIO
from enum import StrEnum
from concurrent.futures import ProcessPoolExecutor

import pandas

from .excel_pandas import export_to_excel as _export_to_excel
from .cimxml_pandas import generate_xml, _get_qname
from .networkx_pandas import export_to_networkx as _export_to_networkx

logger = logging.getLogger(__name__)


from .._engine_detect import flavor as _flavor, to_arrow as _to_arrow, to_pandas as _to_pandas
from .._registry import EngineRegistry

# ── per-format engine registries ─────────────────────────────────────────────
# nquads/cimxml auto-pick the fastest available engine (their results are
# flavor-independent bytes). csv is policy="input": each engine is fastest for
# its own input flavor, so the caller picks by flavor(data), never by probe.
_CIMXML = EngineRegistry(
    "exporter_cimxml", __package__,
    modules={"cython_pugixml": ".cimxml_pugixml",  # compiled extension, fastest
             "python_lxml": ".cimxml_pandas"},     # pure python, always available
    aliases={"performance": "cython_pugixml", "pugixml": "cython_pugixml",
             "lxml": "python_lxml", "pandas": "python_lxml"},
    requires={"cython_pugixml": (".cimxml_cython_pugixml", "pyarrow")},
    hints={"cython_pugixml": "Build with: pixi run build-cython-pugixml-arrow."},
)
_NQUADS = EngineRegistry(
    "exporter_nquads", __package__,
    modules={"polars": ".nquads_polars", "pandas": ".nquads_pandas"},
    requires={"polars": ("polars",)},
    hints={"polars": "Install with: pip install triplets[polars]."},
)
_CSV = EngineRegistry(
    "exporter_csv", __package__, policy="input",
    modules={"pandas": ".csv_pandas", "polars": ".csv_polars"},
    requires={"polars": ("polars",)},
)


def export_to_excel(data, *args, **kwargs):
    return _export_to_excel(_to_pandas(data), *args, **kwargs)


def export_to_networkx(data, *args, **kwargs):
    return _export_to_networkx(_to_pandas(data), *args, **kwargs)


export_to_excel.__doc__ = _export_to_excel.__doc__
export_to_networkx.__doc__ = _export_to_networkx.__doc__


REQUIRED_COLUMNS = ("ID", "KEY", "VALUE", "INSTANCE_ID")


def _check_columns(data):
    """Fail early with a clear message when the input is not a triplets dataset."""
    # pyarrow Table/RecordBatch/RecordBatchReader all expose .schema.names
    names = data.schema.names if _flavor(data) == "pyarrow" else data.columns
    missing = [column for column in REQUIRED_COLUMNS if column not in names]
    if missing:
        raise ValueError(f"Not a triplets dataset — missing columns {missing}, "
                         f"expected {list(REQUIRED_COLUMNS)}, got {list(names)}")


def export_to_arrow(data):
    """Triplet columns (ID, KEY, VALUE, INSTANCE_ID) as a pyarrow.Table.

    Zero-copy where the backing store allows it (arrow-backed pandas from
    read_RDF, polars, DuckDB native arrow); dictionary-encoded columns pass
    through undecoded. Columnar interchange for engines with native Arrow
    ingest (the qlever SPARQL engine's index builder).
    """
    if _flavor(data) in ("pandas", "polars", "pyarrow"):
        _check_columns(data)
    return _to_arrow(data, columns=list(REQUIRED_COLUMNS))


def export_to_csv(data, path=None, multivalue=True, export_to_memory=False, single_file=False, base_filename=None):
    """Export triplet DataFrame to CSV files.

    Auto-detects engine: polars if input is polars DataFrame, else pandas.
    """
    from .._engine_detect import drop_context
    data = drop_context(data)            # CSV writers reject nested columns
    if _flavor(data) not in ("pandas", "polars"):
        data = _to_pandas(data)          # arrow-backed dtypes — near zero-copy for arrow input
    engine = "polars" if _flavor(data) == "polars" else "pandas"
    logger.debug("format=csv, engine=%s (input flavor)", engine)
    _fn = _CSV.get(engine)[1].export_to_csv
    return _fn(data, path=path, multivalue=multivalue, export_to_memory=export_to_memory, single_file=single_file, base_filename=base_filename)


def export_to_nquads(data, path=None, rdf_map=None, engine="auto", export_to_memory=False):
    """Export triplet DataFrame to N-Quads file.

    Parameters
    ----------
    path : str or Path, optional
        Output file path (.nq); defaults to "export.nq" in the current
        directory. Ignored when export_to_memory=True.
    rdf_map : dict or str, optional
        Export schema for proper enum detection and literal datatype
        annotations. If None, enums exported as literals.
    engine : str, default "auto"
        "polars" (lazy expression plan, ~4x faster) or "pandas".
        "auto" picks polars when installed, converting pandas input
        (~17 ms per million rows); falls back to pandas otherwise.
    export_to_memory : bool, default False
        If True, return an in-memory BytesIO (with .name) instead of
        writing to disk — same convention as export_to_csv / export_to_cimxml.
    """
    _check_columns(data)
    if not export_to_memory:
        path = "export.nq" if path is None else os.fspath(path)
    engine_name, engine_module = _NQUADS.get(engine)
    logger.debug(f"format=nquads, engine={engine_name}")

    if hasattr(data, "read_next_batch"):
        # pyarrow.RecordBatchReader: stream one batch at a time — memory stays
        # bounded by a single batch (out-of-core export, e.g. a large duckdb table)
        if engine_name != "polars":
            raise ValueError("streaming N-Quads export (RecordBatchReader input) requires "
                             "the polars engine. Install with: pip install triplets[polars].")
        if export_to_memory:
            buffer = BytesIO()
            engine_module.write_nquads_batches(data, buffer, rdf_map=rdf_map)
            buffer.name = "export.nq"
            buffer.seek(0)
            return buffer
        with open(path, "wb") as handle:
            engine_module.write_nquads_batches(data, handle, rdf_map=rdf_map)
        return None

    if engine_name != _flavor(data):
        from .._engine_detect import to_polars
        data = to_polars(data) if engine_name == "polars" else _to_pandas(data)
    return engine_module.export_to_nquads(data, path, rdf_map=rdf_map, export_to_memory=export_to_memory)


def get_cimxml_engine(name="auto"):
    """Resolve CIM XML engine name (with aliases) and return (name, module)."""
    return _CIMXML.get(name)


def _split_instances(data):
    """Per-INSTANCE_ID frames in the input's own flavor (frame ops bind to input flavor)."""
    if _flavor(data) == "polars":
        return data.partition_by("INSTANCE_ID", maintain_order=True)
    return (frame for _, frame in data.groupby("INSTANCE_ID", observed=True))


class ExportType(StrEnum):
    XML_PER_INSTANCE = "xml_per_instance"
    XML_PER_INSTANCE_ZIP_PER_ALL = "xml_per_instance_zip_per_all"
    XML_PER_INSTANCE_ZIP_PER_XML = "xml_per_instance_zip_per_xml"


def export_to_cimxml(data,
                     rdf_map=None,
                     namespace_map=None,
                     class_KEY="Type",
                     export_undefined=False,
                     export_type=ExportType.XML_PER_INSTANCE_ZIP_PER_XML,
                     global_zip_filename="Export.zip",
                     debug=False,
                     export_to_memory=False,
                     export_base_path="",
                     comment=None,
                     max_workers=None,
                     engine="auto",
                     datatypes=False):
    """Export a full triplet dataset to CIM RDF XML files or ZIP archives.

    Processes all instances (grouped by ``INSTANCE_ID``) and exports them according to the
    specified ``export_type``. Supports parallel processing and in-memory or disk output.

    Parameters
    ----------
    data : pandas.DataFrame
        Full triplet dataset with columns ['INSTANCE_ID', 'ID', 'KEY', 'VALUE'].
    rdf_map : dict or str, optional
        RDF mapping configuration (see :func:`generate_xml`).
    namespace_map : dict, optional
        Namespace prefix-to-URI mapping (see :func:`generate_xml`).
    class_KEY : str, default "Type"
        Key identifying object types in triplet data.
    export_undefined : bool, default False
        If True, also export classes/attributes without a schema definition
        (internal structures like Distribution/NamespaceMap) under the
        http://triplets# namespace. Normal exports carry only schema-defined
        content. (The cython engine emits undefined elements un-namespaced.)
    export_type : ExportType or str, default ExportType.XML_PER_INSTANCE_ZIP_PER_XML
        Export format:
        - ``XML_PER_INSTANCE``: One XML file per instance.
        - ``XML_PER_INSTANCE_ZIP_PER_ALL``: All XMLs in a single ZIP.
        - ``XML_PER_INSTANCE_ZIP_PER_XML``: Each XML in its own ZIP.
    global_zip_filename : str, default "Export.zip"
        Filename for the global ZIP archive (used with ``ZIP_PER_ALL``).
    debug : bool, default False
        Enable detailed timing and debug logging.
    export_to_memory : bool, default False
        If True, return file-like objects (``BytesIO``); if False, save to disk.
    export_base_path : str, default ""
        Directory to save files when ``export_to_memory=False``. Uses current directory if empty.
    comment : str, optional
        Optional XML comment added to each generated file.
    max_workers : int, optional
        Number of parallel workers for XML generation. If ``None``, runs sequentially.
    engine : str, default "auto"
        XML generation engine. "auto" picks best available.
        Options: "python_lxml" (lxml, always available), "cython_pugixml" (compiled, fastest).
        Aliases: "performance"/"pugixml" → cython_pugixml, "lxml"/"pandas" → python_lxml.
    datatypes : bool, default False
        If True, annotate literal elements with rdf:datatype from the schema's
        xsd:type, like the N-Quads export ("44.84" → rdf:datatype xsd#float;
        xsd:string stays plain). Currently python_lxml only — "auto" picks it.

    Returns
    -------
    list
        - If ``export_to_memory=True``: List of ``BytesIO`` objects with ``.name`` attribute.
        - If ``export_to_memory=False``: List of saved filenames (relative to ``export_base_path``).

    Examples
    --------
    >>> files = export_to_cimxml(
    ...     data,
    ...     rdf_map="config/cim_map.json",
    ...     export_type=ExportType.XML_PER_INSTANCE_ZIP_PER_XML,
    ...     export_to_memory=True,
    ...     max_workers=4
    ... )
    >>> for f in files:
    ...     print(f"name:", f.name)

    Notes
    -----
    - Uses ``concurrent.futures.ProcessPoolExecutor`` for parallel XML generation.
    - All XML files are UTF-8 encoded with XML declaration.
    - ZIP files use DEFLATED compression.
    - Filenames are derived from instance ``label`` or UUID.
    """
    if debug:
        start_time = datetime.datetime.now()
        init_time = start_time

    _check_columns(data)
    if _flavor(data) == "pyarrow":
        data = _to_pandas(data)          # arrow-backed dtypes — near zero-copy
    if datatypes and engine == "auto":
        logger.debug("cimxml engine set: python_lxml (datatypes=True not yet in cython engine)")
        engine = "python_lxml"
    engine_name, engine_module = get_cimxml_engine(engine)
    generate = engine_module.generate_xml

    if engine_name == "python_lxml" and _flavor(data) == "polars":
        # the lxml engine's per-instance pipeline is pandas; the cython engine
        # consumes polars frames directly (arrow large_utf8 via the shared accessor)
        logger.debug("format=cimxml: polars input → pandas (python_lxml engine)")
        data = data.to_pandas(use_pyarrow_extension_array=True)

    instances = _split_instances(data)

    if debug:
        _, start_time = _print_duration("All file instance ID-s identified", start_time)

    # Generate one XML document per instance
    if max_workers:
        # polars is incompatible with fork (its rayon thread-pool locks are held
        # in the forked child) — spawn fresh workers when the frames are polars
        mp_context = multiprocessing.get_context("spawn") if _flavor(data) == "polars" else None
        with ProcessPoolExecutor(max_workers=max_workers, mp_context=mp_context) as executor:
            futures = [executor.submit(generate, instance, rdf_map, namespace_map,
                                       class_KEY=class_KEY, export_undefined=export_undefined,
                                       comment=comment, debug=debug, datatypes=datatypes)
                       for instance in instances]
            xml_documents = [future.result() for future in futures]
    else:
        xml_documents = [generate(instance, rdf_map, namespace_map,
                                  class_KEY=class_KEY, export_undefined=export_undefined,
                                  comment=comment, debug=debug, datatypes=datatypes)
                         for instance in instances]

    # generate returns None for instances skipped due to missing mapping
    xml_documents = [document for document in xml_documents if document is not None]

    if debug:
        _, start_time = _print_duration("All XML created in memory ", start_time)

    ### Export XML ###
    exported_files = []

    if export_type == ExportType.XML_PER_INSTANCE:
        for document in xml_documents:

            file_object = BytesIO(document["file"])
            file_object.name = document["filename"]

            exported_files.append(file_object)

            logger.info(f"Exported {document['filename']} to memory")

    ### Export ZIP containing all xml ###
    elif export_type == ExportType.XML_PER_INSTANCE_ZIP_PER_ALL:

        gloabl_zip_fileobject = BytesIO()
        gloabl_zip_fileobject.name = global_zip_filename

        with zipfile.ZipFile(gloabl_zip_fileobject, "a", zipfile.ZIP_DEFLATED, False) as zip_file:

            for document in xml_documents:
                zip_file.writestr(document["filename"], document["file"])
                logger.info(f'Added {document["filename"]} to ZIP')

        exported_files.append(gloabl_zip_fileobject)
        logger.info(f'Exported ZIP named {global_zip_filename} to memory')

    ### Export each xml in separate zip ###
    elif export_type == ExportType.XML_PER_INSTANCE_ZIP_PER_XML:

        for document in xml_documents:

            zip_file_object = BytesIO()
            zip_file_object.name = document["filename"].replace('.xml', '.zip').replace('.XML', '.zip')

            with zipfile.ZipFile(zip_file_object, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.writestr(document["filename"], document["file"])

            exported_files.append(zip_file_object)
            logger.info(f'Exported {zip_file_object.name} to memory')

    else:
        logger.info("Not supported option")
        logger.info("Supported options are: xml_per_instance, xml_per_instance_zip_per_all, xml_per_instance_zip_per_xml")

    if debug:
        _print_duration("Files saved in", start_time)
        _print_duration("Whole Export done in", init_time)

    # Save files to disk
    if export_to_memory:
        return exported_files

    else:
        exported_file_names = []

        for file_object in exported_files:
            export_path = os.path.join(export_base_path, file_object.name)
            with open(export_path, 'wb') as export_file_object:

                # Ensure that the read pointer is at the start of the file
                file_object.seek(0)
                export_file_object.write(file_object.read())

            exported_file_names.append(file_object.name)
            logger.info(f'Saved {export_path}')

        return exported_file_names


def _print_duration(text, start_time):
    """Log duration since start_time; return (duration, now)."""
    end_time = datetime.datetime.now()
    duration = end_time - start_time
    logger.info(f"{text} {duration}")
    return duration, end_time


__all__ = [
    "export_to_excel",
    "export_to_csv",
    "export_to_cimxml",
    "export_to_nquads",
    "export_to_networkx",
    "generate_xml",
    "get_cimxml_engine",
    "ExportType",
    "_get_qname",
]
