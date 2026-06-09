# distutils: language = c++
# cython: language_level=3, boundscheck=False, wraparound=False
"""CIM XML export: Arrow columns → pugixml DOM → XML bytes (zero-copy read).

Reads ID/KEY/VALUE columns directly from Arrow string arrays using
GetView() (pointer arithmetic into contiguous Arrow buffers), builds
the XML tree via pugixml C++ DOM, then serializes.

The inner loop never creates Python objects — all string data flows
from Arrow buffers through C++ string_views into pugixml nodes.
"""
from libcpp.string cimport string
from libcpp.vector cimport vector
from libcpp cimport bool as cbool
from libc.string cimport memcmp, strlen, strcmp
from libc.stdint cimport int64_t

# Arrow C++ types from pyarrow Cython API
from pyarrow.includes.libarrow cimport (
    CArray,
    CRecordBatch,
    CStringArray,
)
from pyarrow.lib cimport pyarrow_unwrap_array, pyarrow_unwrap_batch

# pugixml C++ declarations
cdef extern from "pugixml.hpp" namespace "pugi":
    const unsigned int format_indent
    const unsigned int format_raw

    cdef cppclass xml_attribute:
        xml_attribute() except +
        cbool set_name(const char*)
        cbool set_value(const char*)

    cdef cppclass xml_node:
        xml_node() except +
        xml_node append_child(const char* name)
        xml_attribute append_attribute(const char* name)
        cbool set_value(const char*)
        xml_node append_child(int type)

    cdef cppclass xml_document:
        xml_document() except +
        xml_node append_child(const char* name)
        xml_node append_child(int type)
        void save(xml_writer& writer, const char* indent, unsigned int flags, int encoding) const

    int node_declaration "pugi::node_declaration"
    int node_comment "pugi::node_comment"
    int node_pcdata "pugi::node_pcdata"
    int encoding_utf8 "pugi::encoding_utf8"

    cdef cppclass xml_writer:
        pass


# Inline C++ helpers
cdef extern from *:
    """
    #include <string>
    #include <vector>
    #include <unordered_map>
    #include "pugixml.hpp"

    class string_writer : public pugi::xml_writer {
    public:
        std::string result;
        void write(const void* data, size_t size) override {
            result.append(static_cast<const char*>(data), size);
        }
    };

    // Stores xml_node handles in C++ (can't put them in Python dicts)
    class node_store {
    public:
        std::vector<pugi::xml_node> nodes;

        int add(pugi::xml_node n) {
            int idx = (int)nodes.size();
            nodes.push_back(n);
            return idx;
        }

        pugi::xml_node append_child_to(int idx, const char* name) {
            return nodes[idx].append_child(name);
        }
    };

    // Tag definition resolved at setup time
    struct TagDef {
        std::string tag;         // "cim:ACLineSegment.r"
        std::string attr_name;   // "rdf:resource" or ""
        std::string value_prefix;// "#_" or ""
        std::string text_prefix; // "" usually
        bool is_ref;             // true if has attrib (resource/ID)
        bool needs_enum_lookup;  // true if value_prefix was empty on a ref
    };

    // Class definition resolved at setup time
    struct ClassDef {
        std::string tag;         // "cim:ACLineSegment"
        std::string id_attr;     // "rdf:ID"
        std::string id_prefix;   // "_"
    };
    """
    cdef cppclass string_writer(xml_writer):
        string_writer() except +
        string result

    cdef cppclass node_store:
        node_store() except +
        int add(xml_node n)
        xml_node append_child_to(int idx, const char* name)

    cdef cppclass TagDef:
        string tag
        string attr_name
        string value_prefix
        string text_prefix
        cbool is_ref
        cbool needs_enum_lookup

    cdef cppclass ClassDef:
        string tag
        string id_attr
        string id_prefix


# Direct access to Arrow StringArray GetString
cdef extern from "arrow/array/array_binary.h" namespace "arrow":
    cdef cppclass CArrowStringArray "arrow::StringArray":
        string GetString(int64_t i) const
        cbool IsNull(int64_t i) const
        int64_t length() const


def generate_xml_from_arrow(arrow_table_or_batch,
                            dict rdf_map,
                            dict namespace_map,
                            dict instance_rdf_map,
                            str file_name,
                            str class_KEY="Type",
                            cbool export_undefined=True,
                            comment=None):
    """Generate CIM RDF/XML bytes directly from Arrow columnar data.

    Reads Arrow string arrays at C++ level (zero-copy GetString),
    builds pugixml DOM, serializes to bytes.

    Parameters
    ----------
    arrow_table_or_batch : pyarrow.RecordBatch or pyarrow.Table
        Must have columns: ID, KEY, VALUE (string type)
    rdf_map : dict
    namespace_map : dict
    instance_rdf_map : dict
    file_name : str
    class_KEY : str
    export_undefined : bool
    comment : str or None

    Returns
    -------
    bytes : UTF-8 encoded XML
    """
    import pyarrow as pa

    # Convert Table to RecordBatch if needed
    if isinstance(arrow_table_or_batch, pa.Table):
        batch = arrow_table_or_batch.to_batches()[0] if arrow_table_or_batch.num_rows > 0 else None
        if batch is None:
            return b""
        # If table has multiple batches, combine
        if len(arrow_table_or_batch.to_batches()) > 1:
            batch = arrow_table_or_batch.combine_chunks().to_batches()[0]
    else:
        batch = arrow_table_or_batch

    # Get column indices
    schema = batch.schema
    id_idx = schema.get_field_index("ID")
    key_idx = schema.get_field_index("KEY")
    val_idx = schema.get_field_index("VALUE")

    # Unwrap to C++ Arrow arrays
    cdef CArrowStringArray* id_arr = <CArrowStringArray*>(pyarrow_unwrap_array(batch.column(id_idx)).get())
    cdef CArrowStringArray* key_arr = <CArrowStringArray*>(pyarrow_unwrap_array(batch.column(key_idx)).get())
    cdef CArrowStringArray* val_arr = <CArrowStringArray*>(pyarrow_unwrap_array(batch.column(val_idx)).get())

    cdef int64_t n = id_arr.length()
    cdef int64_t i

    # Build reverse namespace map
    cdef dict uri_to_prefix = {}
    for prefix, uri in namespace_map.items():
        uri_to_prefix[uri] = prefix

    def _make_prefixed(ns, local_name):
        if ns is None:
            return local_name
        p = uri_to_prefix.get(ns)
        return f"{p}:{local_name}" if p else local_name

    def _parse_attrib(full_attrib):
        if full_attrib.startswith("{"):
            ns_end = full_attrib.index("}")
            ns = full_attrib[1:ns_end]
            local = full_attrib[ns_end + 1:]
            p = uri_to_prefix.get(ns)
            return f"{p}:{local}" if p else local
        return full_attrib

    # Pre-build C++ ClassDef and TagDef lookup maps
    # We use Python dicts with string keys -> index into C++ vectors
    cdef vector[ClassDef] class_defs_vec
    cdef dict class_defs_idx = {}  # class_name -> int index

    cdef vector[TagDef] tag_defs_vec
    cdef dict tag_defs_idx = {}  # KEY -> int index

    cdef ClassDef cd
    cdef TagDef td

    for key, defn in instance_rdf_map.items():
        if not isinstance(defn, dict):
            continue
        if defn.get("type") == "Class":
            cd.tag = _make_prefixed(defn["namespace"], key).encode('utf-8')
            cd.id_attr = _parse_attrib(defn["attrib"]["attribute"]).encode('utf-8')
            cd.id_prefix = defn["attrib"]["value_prefix"].encode('utf-8')
            class_defs_idx[key] = class_defs_vec.size()
            class_defs_vec.push_back(cd)
        elif defn.get("namespace"):
            td.tag = _make_prefixed(defn["namespace"], key).encode('utf-8')
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
                td.attr_name = b""
                td.value_prefix = b""
                td.needs_enum_lookup = False
            tag_defs_idx[key] = tag_defs_vec.size()
            tag_defs_vec.push_back(td)

    # Build pugixml document
    cdef xml_document doc
    cdef xml_node decl_node, rdf_root, obj_node, attr_node
    cdef bytes uri_bytes

    decl_node = doc.append_child(node_declaration)
    decl_node.append_attribute(b"version").set_value(b"1.0")
    decl_node.append_attribute(b"encoding").set_value(b"UTF-8")

    cdef bytes comment_bytes
    if comment:
        comment_bytes = comment.encode('utf-8')
        doc.append_child(node_comment).set_value(<const char*>comment_bytes)

    rdf_root = doc.append_child(b"rdf:RDF")
    for prefix, uri in namespace_map.items():
        ns_attr_name = f"xmlns:{prefix}".encode('utf-8')
        uri_bytes = uri.encode('utf-8')
        rdf_root.append_attribute(<const char*>ns_attr_name).set_value(<const char*>uri_bytes)

    # Node store for class elements
    cdef node_store store
    cdef dict obj_index = {}  # obj_id(str) -> int index in store
    cdef int store_idx
    cdef int cd_idx, td_idx

    # Temp C++ strings for reading Arrow data
    cdef string s_id, s_key, s_value, s_combined
    cdef bytes class_key_bytes = class_KEY.encode('utf-8')

    # ── First pass: create class elements ──────────────────────────
    for i in range(n):
        s_key = key_arr.GetString(i)
        if s_key != <string>class_key_bytes:
            continue

        s_id = id_arr.GetString(i)
        if val_arr.IsNull(i):
            continue
        s_value = val_arr.GetString(i)

        # Look up class definition
        py_class_name = s_value.decode('utf-8')
        py_cd_idx = class_defs_idx.get(py_class_name)

        if py_cd_idx is not None:
            cd_idx = <int>py_cd_idx
            obj_node = rdf_root.append_child(class_defs_vec[cd_idx].tag.c_str())
            # Build id value: prefix + id
            s_combined = class_defs_vec[cd_idx].id_prefix
            s_combined.append(s_id)
            obj_node.append_attribute(class_defs_vec[cd_idx].id_attr.c_str()).set_value(s_combined.c_str())
        elif export_undefined:
            obj_node = rdf_root.append_child(s_value.c_str())
            s_combined.assign(b"urn:uuid:")
            s_combined.append(s_id)
            obj_node.append_attribute(b"rdf:about").set_value(s_combined.c_str())
        else:
            continue

        store_idx = store.add(obj_node)
        py_id = s_id.decode('utf-8')
        obj_index[py_id] = store_idx

    # ── Second pass: add attributes ──────────────────────────────
    for i in range(n):
        s_key = key_arr.GetString(i)
        if s_key == <string>class_key_bytes:
            continue

        # Check for null VALUE
        if val_arr.IsNull(i):
            continue

        s_id = id_arr.GetString(i)
        py_id = s_id.decode('utf-8')

        py_store_idx = obj_index.get(py_id)
        if py_store_idx is None:
            continue

        s_value = val_arr.GetString(i)

        py_key = s_key.decode('utf-8')
        py_td_idx = tag_defs_idx.get(py_key)

        if py_td_idx is not None:
            td_idx = <int>py_td_idx

            attr_node = store.append_child_to(<int>py_store_idx, tag_defs_vec[td_idx].tag.c_str())

            if tag_defs_vec[td_idx].is_ref:
                if tag_defs_vec[td_idx].needs_enum_lookup:
                    # Need to look up enum namespace from Python dict
                    py_val = s_value.decode('utf-8')
                    enum_def = instance_rdf_map.get(py_val)
                    if enum_def is not None and isinstance(enum_def, dict):
                        s_combined = enum_def.get("namespace", "").encode('utf-8')
                    else:
                        s_combined.clear()
                    s_combined.append(s_value)
                else:
                    s_combined = tag_defs_vec[td_idx].value_prefix
                    s_combined.append(s_value)
                attr_node.append_attribute(tag_defs_vec[td_idx].attr_name.c_str()).set_value(s_combined.c_str())
            else:
                s_combined = tag_defs_vec[td_idx].text_prefix
                s_combined.append(s_value)
                attr_node.append_child(node_pcdata).set_value(s_combined.c_str())
        elif export_undefined:
            attr_node = store.append_child_to(<int>py_store_idx, s_key.c_str())
            attr_node.append_child(node_pcdata).set_value(s_value.c_str())

    # Serialize
    cdef string_writer writer
    doc.save(writer, b"  ", format_indent, encoding_utf8)

    return writer.result
