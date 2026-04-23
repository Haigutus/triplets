# cython: language_level=3, boundscheck=False, wraparound=False
"""CIM XML export using Cython with direct C++ string building.

Builds XML as a C++ string, avoiding all Python object overhead for the
inner loops. Data is extracted from numpy arrays once, then processed in
near-C speed.
"""
from libcpp.string cimport string
from libcpp.vector cimport vector
from libcpp.unordered_map cimport unordered_map
from libc.string cimport memcpy

import numpy as np
cimport numpy as np

# Type for numpy string arrays
ctypedef np.npy_intp DTYPE_t


cdef inline void _append(string &buf, const char *s) noexcept:
    buf.append(s)


cdef inline void _append_str(string &buf, const string &s) noexcept:
    buf.append(s)


cdef class TagDef:
    """Pre-resolved tag definition for fast lookup."""
    cdef public string open_tag       # e.g. "cim:ACLineSegment.r"
    cdef public string close_tag      # e.g. "</cim:ACLineSegment.r>"
    cdef public string attr_name      # e.g. "rdf:resource"  (empty if text)
    cdef public string value_prefix   # e.g. "#_"
    cdef public string text_prefix    # e.g. ""
    cdef public bint is_ref           # True if attribute-based (resource/ID)
    cdef public bint needs_enum_lookup  # True if value_prefix was empty on a ref


cdef class ClassDef:
    """Pre-resolved class definition."""
    cdef public string open_tag       # e.g. "cim:ACLineSegment"
    cdef public string close_tag      # e.g. "</cim:ACLineSegment>"
    cdef public string id_attr        # e.g. 'rdf:ID'
    cdef public string id_prefix      # e.g. '_'


def generate_xml_bytes(instance_data,
                       rdf_map,
                       namespace_map,
                       instance_rdf_map,
                       str file_name,
                       str class_KEY="Type",
                       bint export_undefined=True,
                       comment=None):
    """Generate CIM RDF/XML bytes using Cython string building.

    Parameters
    ----------
    instance_data : pandas.DataFrame
    rdf_map : dict - the full rdf_map (needed for enum lookups)
    namespace_map : dict - prefix -> URI
    instance_rdf_map : dict - the profile-specific map
    file_name : str
    class_KEY : str
    export_undefined : bool
    comment : str or None

    Returns
    -------
    bytes : UTF-8 encoded XML
    """
    # Build reverse namespace map: URI -> prefix
    cdef dict uri_to_prefix = {}
    for prefix, uri in namespace_map.items():
        uri_to_prefix[uri] = prefix

    def _make_prefixed(ns, local_name):
        """Return 'prefix:local_name' string."""
        if ns is None:
            return local_name
        p = uri_to_prefix.get(ns)
        if p:
            return f"{p}:{local_name}"
        return local_name

    def _parse_attrib(full_attrib):
        """Parse '{namespace}local' -> 'prefix:local'."""
        if full_attrib.startswith("{"):
            ns_end = full_attrib.index("}")
            ns = full_attrib[1:ns_end]
            local = full_attrib[ns_end + 1:]
            p = uri_to_prefix.get(ns)
            if p:
                return f"{p}:{local}"
            return local
        return full_attrib

    # Pre-build class definitions
    cdef dict class_defs = {}  # class_name -> ClassDef
    cdef dict tag_defs = {}    # KEY -> TagDef

    for key, defn in instance_rdf_map.items():
        if not isinstance(defn, dict):
            continue
        if defn.get("type") == "Class":
            cd = ClassDef()
            tag_str = _make_prefixed(defn["namespace"], key)
            cd.open_tag = tag_str.encode('utf-8')
            cd.close_tag = (f"</{tag_str}>").encode('utf-8')
            cd.id_attr = _parse_attrib(defn["attrib"]["attribute"]).encode('utf-8')
            cd.id_prefix = defn["attrib"]["value_prefix"].encode('utf-8')
            class_defs[key] = cd
        elif defn.get("namespace"):
            td = TagDef()
            tag_str = _make_prefixed(defn["namespace"], key)
            td.open_tag = tag_str.encode('utf-8')
            td.close_tag = (f"</{tag_str}>").encode('utf-8')
            attrib = defn.get("attrib")
            td.text_prefix = defn.get("text", "").encode('utf-8')
            if attrib:
                td.is_ref = True
                td.attr_name = _parse_attrib(attrib["attribute"]).encode('utf-8')
                vp = attrib.get("value_prefix", "")
                td.value_prefix = vp.encode('utf-8')
                td.needs_enum_lookup = (vp == "")
            else:
                td.is_ref = False
            tag_defs[key] = td

    # Extract numpy arrays
    ids_arr = instance_data["ID"].values.astype(object)
    keys_arr = instance_data["KEY"].values.astype(object)
    values_arr = instance_data["VALUE"].values.astype(object)
    cdef Py_ssize_t n = len(ids_arr)
    cdef Py_ssize_t i

    # Estimate buffer size (rough: 200 bytes per row)
    cdef string buf
    buf.reserve(n * 200)

    # XML declaration
    _append(buf, b"<?xml version='1.0' encoding='UTF-8'?>\n")

    if comment:
        _append(buf, b"<!--")
        _append_str(buf, comment.encode('utf-8'))
        _append(buf, b"-->\n")

    # RDF root with namespaces
    _append(buf, b"<rdf:RDF")
    for prefix, uri in namespace_map.items():
        _append(buf, b'\n  xmlns:')
        _append_str(buf, prefix.encode('utf-8'))
        _append(buf, b'="')
        _append_str(buf, uri.encode('utf-8'))
        _append(buf, b'"')
    _append(buf, b">\n")

    # First pass: collect objects and their positions
    # obj_id -> (ClassDef, [attr_xml_strings])
    cdef dict objects = {}   # id -> list of byte strings for attributes
    cdef dict obj_order = {} # id -> ClassDef (preserves insertion order in Python 3.7+)
    cdef string obj_open

    for i in range(n):
        key = keys_arr[i]
        if key != class_KEY:
            continue

        obj_id = ids_arr[i]
        class_name = values_arr[i]

        cd = class_defs.get(class_name)
        if cd is not None:
            obj_order[obj_id] = cd
            objects[obj_id] = []
        elif export_undefined:
            # Create a default ClassDef
            cd_default = ClassDef()
            cd_default.open_tag = class_name.encode('utf-8')
            cd_default.close_tag = (f"</{class_name}>").encode('utf-8')
            cd_default.id_attr = b"rdf:about"
            cd_default.id_prefix = b"urn:uuid:"
            obj_order[obj_id] = cd_default
            objects[obj_id] = []

    # Second pass: build attribute strings
    cdef string attr_buf
    cdef bytes vp_bytes

    for i in range(n):
        key = keys_arr[i]
        if key == class_KEY:
            continue

        obj_id = ids_arr[i]
        attr_list = objects.get(obj_id)
        if attr_list is None:
            continue

        value = values_arr[i]
        if value is None:
            continue
        # NaN check for float
        if isinstance(value, float) and value != value:
            continue

        td = tag_defs.get(key)
        if td is not None:
            attr_buf.clear()
            _append(attr_buf, b"    <")
            _append_str(attr_buf, td.open_tag)

            if td.is_ref:
                _append(attr_buf, b" ")
                _append_str(attr_buf, td.attr_name)
                _append(attr_buf, b'="')
                if td.needs_enum_lookup:
                    # Look up enum namespace
                    enum_def = instance_rdf_map.get(value)
                    if enum_def is not None and isinstance(enum_def, dict):
                        vp_bytes = enum_def.get("namespace", "").encode('utf-8')
                    else:
                        vp_bytes = b""
                    _append_str(attr_buf, vp_bytes)
                else:
                    _append_str(attr_buf, td.value_prefix)
                _append_str(attr_buf, str(value).encode('utf-8'))
                _append(attr_buf, b'"/>\n')
            else:
                _append(attr_buf, b">")
                _append_str(attr_buf, td.text_prefix)
                _append_str(attr_buf, str(value).encode('utf-8'))
                _append(attr_buf, b"</")
                _append_str(attr_buf, td.open_tag)
                _append(attr_buf, b">\n")

            attr_list.append(attr_buf)
        elif export_undefined:
            attr_buf.clear()
            _append(attr_buf, b"    <")
            _append_str(attr_buf, key.encode('utf-8'))
            _append(attr_buf, b">")
            _append_str(attr_buf, str(value).encode('utf-8'))
            _append(attr_buf, b"</")
            _append_str(attr_buf, key.encode('utf-8'))
            _append(attr_buf, b">\n")
            attr_list.append(attr_buf)

    # Assemble final XML
    cdef ClassDef cd_obj
    for obj_id, cd_obj in obj_order.items():
        _append(buf, b"  <")
        _append_str(buf, cd_obj.open_tag)
        _append(buf, b" ")
        _append_str(buf, cd_obj.id_attr)
        _append(buf, b'="')
        _append_str(buf, cd_obj.id_prefix)
        _append_str(buf, str(obj_id).encode('utf-8'))
        _append(buf, b'">\n')

        for attr_bytes in objects[obj_id]:
            _append_str(buf, attr_bytes)

        _append_str(buf, cd_obj.close_tag)
        _append(buf, b"\n")

    _append(buf, b"</rdf:RDF>\n")

    return buf
