# distutils: language = c++
# cython: language_level=3, boundscheck=False, wraparound=False
"""
Cython RDF/CIM XML parser → Arrow RecordBatch (zero-copy).

Uses pugixml C++ library directly (compiled from source, no pygixml needed)
for XML parsing and Arrow C++ StringBuilders for output. The entire pipeline
runs at C++ speed with no Python object allocation per element.

Build requirements:
    - pugixml source (pugixml.hpp + pugixml.cpp) — see setup_cython.py
    - pyarrow (for Arrow C++ headers and shared libs)
    - Cython
"""

from libcpp.string cimport string
from libcpp.memory cimport shared_ptr, make_shared
from libcpp.vector cimport vector
from libcpp cimport bool
from libc.string cimport strrchr, strlen, memcmp
from libc.stdint cimport int64_t

# Arrow C++ types from pyarrow's Cython API
from pyarrow.includes.common cimport CStatus
from pyarrow.includes.libarrow cimport (
    CArray,
    CStringBuilder,
    CRecordBatch,
    CSchema,
    CField,
    CDataType,
    CMemoryPool,
    c_default_memory_pool,
)
from pyarrow.lib cimport pyarrow_wrap_batch

cdef extern from "arrow/type.h" namespace "arrow":
    shared_ptr[CDataType] utf8()

# pugixml C++ types (compiled from source via setup_cython.py)
cdef extern from "pugixml.hpp" namespace "pugi":
    const unsigned int parse_minimal
    const unsigned int parse_embed_pcdata

    cdef cppclass xml_parse_result:
        bool operator bool() const

    cdef cppclass xml_attribute:
        xml_attribute() except +
        const char* name() const
        const char* value() const
        xml_attribute next_attribute() const
        bool empty() const

    cdef cppclass xml_node:
        xml_node() except +
        const char* name() const
        const char* child_value() const
        xml_node first_child() const
        xml_node next_sibling() const
        xml_attribute first_attribute() const
        xml_attribute attribute(const char* name) const
        bool empty() const

    cdef cppclass xml_document:
        xml_document() except +
        xml_parse_result load_file(const char* path, unsigned int options)
        xml_parse_result load_buffer(const void* contents, size_t size, unsigned int options)
        xml_node first_child() const


# ── C++ helpers for string processing ────────────────────────────────────────
# All string operations happen in C++ — no Python objects created per element.

cdef extern from *:
    """
    #include <cstring>
    #include <string>

    // Strip a prefix from a C string. Returns pointer past the prefix
    // and updates len accordingly. If prefix doesn't match, returns unchanged.
    static inline const char* strip_prefix(const char* s, size_t& len,
                                           const char* prefix) {
        size_t plen = strlen(prefix);
        if (len >= plen && memcmp(s, prefix, plen) == 0) {
            s   += plen;
            len -= plen;
        }
        return s;
    }

    // Clean CIM ID prefixes: "urn:uuid:", "#_", "_"
    static inline void clean_id(const char* raw, std::string& out) {
        size_t len = strlen(raw);
        const char* s = raw;
        s = strip_prefix(s, len, "urn:uuid:");
        s = strip_prefix(s, len, "#_");
        s = strip_prefix(s, len, "_");
        out.assign(s, len);
    }

    // Clean a reference value: clean_id + handle http://...#EnumValue
    static inline void clean_ref_value(const char* raw, std::string& out) {
        clean_id(raw, out);
        if (out.size() >= 4 && memcmp(out.c_str(), "http", 4) == 0) {
            size_t pos = out.rfind('#');
            if (pos != std::string::npos) {
                out = out.substr(pos + 1);
            }
        }
    }

    // Extract local name from "prefix:localname"
    static inline const char* local_name(const char* name) {
        const char* colon = strrchr(name, ':');
        return colon ? colon + 1 : name;
    }
    """
    void clean_id(const char* raw, string& out) noexcept
    void clean_ref_value(const char* raw, string& out) noexcept
    const char* local_name(const char* name) noexcept


cdef unsigned int PARSE_FLAGS = parse_minimal | parse_embed_pcdata


def load_rdf_to_arrow_cython(path_or_fileobject, debug=False):
    """Parse RDF XML and return a PyArrow RecordBatch directly.

    The entire pipeline — XML parse, element iteration, Arrow building —
    happens in C++ via Cython. Returns a PyArrow RecordBatch (zero-copy).
    """
    import uuid as uuid_mod
    import datetime

    cdef str file_name
    if isinstance(path_or_fileobject, str):
        file_name = path_or_fileobject
    else:
        file_name = path_or_fileobject.name

    if debug:
        start_time = datetime.datetime.now()

    cdef string instance_id = str(uuid_mod.uuid4()).encode('utf-8')
    cdef string meta_id = str(uuid_mod.uuid4()).encode('utf-8')
    cdef string nsmap_id = str(uuid_mod.uuid4()).encode('utf-8')
    cdef bytes file_name_bytes = file_name.encode('utf-8')

    # Parse XML with pugixml
    cdef xml_document doc
    cdef xml_parse_result result
    cdef bytes path_bytes
    cdef bytes content_bytes
    cdef const char* buf
    cdef size_t buf_len

    if isinstance(path_or_fileobject, str):
        path_bytes = path_or_fileobject.encode('utf-8')
        result = doc.load_file(path_bytes, PARSE_FLAGS)
    else:
        path_or_fileobject.seek(0)
        content = path_or_fileobject.read()
        if isinstance(content, str):
            content_bytes = content.encode('utf-8')
        else:
            content_bytes = content
        buf = content_bytes
        buf_len = len(content_bytes)
        result = doc.load_buffer(buf, buf_len, PARSE_FLAGS)

    if not <bool>result:
        raise ValueError(f"Failed to parse XML: {file_name}")

    if debug:
        end = datetime.datetime.now()
        print(f"  XML parse: {end - start_time}")
        start_time = end

    cdef xml_node root = doc.first_child()

    # Arrow StringBuilders (heap-allocated, no default constructor)
    cdef CMemoryPool* pool = c_default_memory_pool()
    cdef CStringBuilder* id_b = new CStringBuilder(pool)
    cdef CStringBuilder* key_b = new CStringBuilder(pool)
    cdef CStringBuilder* val_b = new CStringBuilder(pool)
    cdef CStringBuilder* inst_b = new CStringBuilder(pool)

    # Variable declarations (must be before try block in Cython)
    cdef string s_type = b"Type"
    cdef string s_distribution = b"Distribution"
    cdef string s_label = b"label"
    cdef string s_namespace_map = b"NamespaceMap"
    cdef string s_xml_base = b"xml_base"
    cdef string s_empty = b""
    cdef xml_attribute attr
    cdef const char* aname
    cdef bint has_xml_base = False
    cdef string tmp_str
    cdef xml_node rdf_object
    cdef xml_node element
    cdef const char* raw_id
    cdef const char* tag_name
    cdef const char* child_text
    cdef const char* ref_val
    cdef string obj_id, key_str, val_str
    cdef size_t text_len
    cdef shared_ptr[CArray] id_arr, key_arr, val_arr, inst_arr
    cdef shared_ptr[CDataType] utf8_type
    cdef vector[shared_ptr[CField]] fields
    cdef shared_ptr[CSchema] schema
    cdef vector[shared_ptr[CArray]] columns
    cdef shared_ptr[CRecordBatch] batch

    try:
        # ── Metadata rows ────────────────────────────────────────────────
        # Distribution
        id_b.Append(meta_id);    key_b.Append(s_type)
        val_b.Append(s_distribution);  inst_b.Append(instance_id)

        id_b.Append(meta_id);    key_b.Append(s_label)
        val_b.Append(<string>file_name_bytes);  inst_b.Append(instance_id)

        # NamespaceMap
        id_b.Append(nsmap_id);   key_b.Append(s_type)
        val_b.Append(s_namespace_map);  inst_b.Append(instance_id)

        # ── Namespace declarations from root attributes ──────────────────
        attr = root.first_attribute()
        while not attr.empty():
            aname = attr.name()
            if memcmp(aname, b"xmlns:", 6) == 0:
                id_b.Append(nsmap_id)
                tmp_str.assign(aname + 6);  key_b.Append(tmp_str)
                tmp_str.assign(attr.value()); val_b.Append(tmp_str)
                inst_b.Append(instance_id)
            elif memcmp(aname, b"xmlns", 5) == 0 and aname[5] == 0:
                id_b.Append(nsmap_id);  key_b.Append(s_empty)
                tmp_str.assign(attr.value()); val_b.Append(tmp_str)
                inst_b.Append(instance_id)
            elif memcmp(aname, b"xml:base", 8) == 0:
                id_b.Append(nsmap_id);  key_b.Append(s_xml_base)
                tmp_str.assign(attr.value()); val_b.Append(tmp_str)
                inst_b.Append(instance_id)
                has_xml_base = True
            attr = attr.next_attribute()

        if not has_xml_base:
            id_b.Append(nsmap_id);  key_b.Append(s_xml_base)
            val_b.Append(<string>file_name_bytes);  inst_b.Append(instance_id)

        # ── RDF objects ──────────────────────────────────────────────────
        rdf_object = root.first_child()
        while not rdf_object.empty():

            # ID from rdf:ID / rdf:about / rdf:nodeID
            raw_id = rdf_object.attribute(b"rdf:ID").value()
            if raw_id[0] == 0:
                raw_id = rdf_object.attribute(b"rdf:about").value()
            if raw_id[0] == 0:
                raw_id = rdf_object.attribute(b"rdf:nodeID").value()

            if raw_id[0] != 0:
                clean_id(raw_id, obj_id)
            else:
                obj_id.clear()

            # Type row
            tag_name = local_name(rdf_object.name())
            id_b.Append(obj_id);  key_b.Append(s_type)
            val_str.assign(tag_name); val_b.Append(val_str)
            inst_b.Append(instance_id)

            # Properties
            element = rdf_object.first_child()
            while not element.empty():
                tag_name = local_name(element.name())
                key_str.assign(tag_name)

                id_b.Append(obj_id)
                key_b.Append(key_str)
                inst_b.Append(instance_id)

                # Text content or reference attribute
                child_text = element.child_value()
                text_len = strlen(child_text)

                if text_len > 0:
                    val_str.assign(child_text, text_len)
                    val_b.Append(val_str)
                else:
                    ref_val = element.attribute(b"rdf:resource").value()
                    if ref_val[0] == 0:
                        ref_val = element.attribute(b"rdf:nodeID").value()

                    if ref_val[0] != 0:
                        clean_ref_value(ref_val, val_str)
                        val_b.Append(val_str)
                    else:
                        val_b.AppendNull()

                element = element.next_sibling()

            rdf_object = rdf_object.next_sibling()

        if debug:
            end = datetime.datetime.now()
            print(f"  Extraction: {end - start_time}")
            start_time = end

        # ── Build Arrow RecordBatch ──────────────────────────────────────
        id_b.Finish(&id_arr)
        key_b.Finish(&key_arr)
        val_b.Finish(&val_arr)
        inst_b.Finish(&inst_arr)

        utf8_type = utf8()
        fields.push_back(make_shared[CField](b"ID", utf8_type, False))
        fields.push_back(make_shared[CField](b"KEY", utf8_type, False))
        fields.push_back(make_shared[CField](b"VALUE", utf8_type, True))
        fields.push_back(make_shared[CField](b"INSTANCE_ID", utf8_type, False))
        schema = make_shared[CSchema](fields)

        columns.push_back(id_arr)
        columns.push_back(key_arr)
        columns.push_back(val_arr)
        columns.push_back(inst_arr)

        batch = CRecordBatch.Make(schema, id_arr.get().length(), columns)

        if debug:
            end = datetime.datetime.now()
            print(f"  Arrow finalize: {end - start_time}")

        return pyarrow_wrap_batch(batch)

    finally:
        del id_b
        del key_b
        del val_b
        del inst_b
