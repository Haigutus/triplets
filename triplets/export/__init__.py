"""Triplet DataFrame export functions.

Formats: Excel, CSV, CIM XML, N-Quads, NetworkX.
Each format has its own {format}_{engine}.py file.

CIM XML engines (mirrors triplets.parser engine setup):
- python_lxml    (pure Python + lxml, always available)
- cython_pugixml (compiled Arrow → pugixml extension, fastest)
Fallback: cython_pugixml → python_lxml
"""

import os
import logging
import zipfile
import datetime

from io import BytesIO
from enum import StrEnum
from importlib import import_module
from concurrent.futures import ProcessPoolExecutor

import pandas

from .excel_pandas import export_to_excel
from .cimxml_pandas import generate_xml, _get_qname
from .networkx_pandas import export_to_networkx

logger = logging.getLogger(__name__)


def _is_polars(data):
    return hasattr(data, '__module__') and 'polars' in type(data).__module__


REQUIRED_COLUMNS = ("ID", "KEY", "VALUE", "INSTANCE_ID")


def _check_columns(data):
    """Fail early with a clear message when the input is not a triplets dataset."""
    missing = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError(f"Not a triplets dataset — missing columns {missing}, "
                         f"expected {list(REQUIRED_COLUMNS)}, got {list(data.columns)}")


def export_to_csv(data, path=None, multivalue=True, export_to_memory=False, single_file=False, base_filename=None):
    """Export triplet DataFrame to CSV files.

    Auto-detects engine: polars if input is polars DataFrame, else pandas.
    """
    if _is_polars(data):
        logger.debug("format=csv, engine=polars (auto-detected)")
        from .csv_polars import export_to_csv as _fn
    else:
        logger.debug("format=csv, engine=pandas (auto-detected)")
        from .csv_pandas import export_to_csv as _fn
    return _fn(data, path=path, multivalue=multivalue, export_to_memory=export_to_memory,
               single_file=single_file, base_filename=base_filename)


def export_to_nquads(data, path, rdf_map=None):
    """Export triplet DataFrame to N-Quads file.

    Parameters
    ----------
    rdf_map : dict or str, optional
        Export schema for proper enum detection. If None, enums exported as literals.
    """
    _check_columns(data)
    if _is_polars(data):
        logger.debug("format=nquads, engine=polars (auto-detected)")
        from .nquads_polars import export_to_nquads as _fn
        return _fn(data, path, rdf_map=rdf_map)
    logger.debug("format=nquads, engine=pandas (auto-detected)")
    from .nquads_pandas import export_to_nquads as _fn
    return _fn(data, path, rdf_map=rdf_map)


# ── CIM XML engine dispatch ──────────────────────────────────────────────────
# Engine name → module (lazy import). Auto preference: first importable.
_CIMXML_ENGINE_MODULES = {
    "cython_pugixml": ".cimxml_pugixml",  # compiled extension, fastest
    "python_lxml": ".cimxml_pandas",      # pure python, always available
}
_CIMXML_ENGINE_ALIASES = {
    "performance": "cython_pugixml",
    "pugixml": "cython_pugixml",
    "lxml": "python_lxml",
    "pandas": "python_lxml",
}
_cimxml_engines = {}  # loaded module cache


def get_cimxml_engine(name="auto"):
    """Resolve CIM XML engine name (with aliases) and return (name, module)."""
    if name == "auto":
        for candidate in _CIMXML_ENGINE_MODULES:
            try:
                logger.debug(f"cimxml auto - test engine availability: {candidate}")
                return candidate, _load_cimxml_engine(candidate)
            except ImportError:
                continue

    resolved = _CIMXML_ENGINE_ALIASES.get(name, name)
    logger.debug(f"cimxml engine set: {resolved}")
    return resolved, _load_cimxml_engine(resolved)


def _load_cimxml_engine(name):
    """Import CIM XML engine module on demand."""
    module_name = _CIMXML_ENGINE_MODULES.get(name)
    if module_name is None:
        raise ValueError(f"Unknown cimxml engine: {name}. Known: {', '.join(_CIMXML_ENGINE_MODULES)}")
    if name not in _cimxml_engines:
        _cimxml_engines[name] = import_module(module_name, __package__)
    return _cimxml_engines[name]


class ExportType(StrEnum):
    XML_PER_INSTANCE = "xml_per_instance"
    XML_PER_INSTANCE_ZIP_PER_ALL = "xml_per_instance_zip_per_all"
    XML_PER_INSTANCE_ZIP_PER_XML = "xml_per_instance_zip_per_xml"


def export_to_cimxml(data,
                     rdf_map=None,
                     namespace_map=None,
                     class_KEY="Type",
                     export_undefined=True,
                     export_type=ExportType.XML_PER_INSTANCE_ZIP_PER_XML,
                     global_zip_filename="Export.zip",
                     debug=False,
                     export_to_memory=False,
                     export_base_path="",
                     comment=None,
                     max_workers=None,
                     engine="auto"):
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
    export_undefined : bool, default True
        Export unmapped classes/attributes with default RDF syntax.
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
    if _is_polars(data):
        # the per-instance pipeline is pandas (groupby + engine contract)
        logger.debug("format=cimxml: polars input → pandas")
        data = data.to_pandas(use_pyarrow_extension_array=True)
    engine_name, engine_module = get_cimxml_engine(engine)
    generate = engine_module.generate_xml

    instances = data.groupby("INSTANCE_ID", observed=True)

    if debug:
        _, start_time = _print_duration("All file instance ID-s identified", start_time)

    # Generate one XML document per instance
    if max_workers:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(generate, instance, rdf_map, namespace_map,
                                       class_KEY=class_KEY, export_undefined=export_undefined,
                                       comment=comment, debug=debug)
                       for _, instance in instances]
            xml_documents = [future.result() for future in futures]
    else:
        xml_documents = [generate(instance, rdf_map, namespace_map,
                                  class_KEY=class_KEY, export_undefined=export_undefined,
                                  comment=comment, debug=debug)
                         for _, instance in instances]

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

# Register monkey-patches on pandas.DataFrame
pandas.DataFrame.export_to_excel = export_to_excel
pandas.DataFrame.export_to_csv = export_to_csv
pandas.DataFrame.export_to_cimxml = export_to_cimxml
pandas.DataFrame.export_to_nquads = export_to_nquads
pandas.DataFrame.export_to_networkx = export_to_networkx
