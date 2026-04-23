# distutils: language = c++
# cython: language_level=3, boundscheck=False, wraparound=False
"""
Cython extraction loop using lxml's libxml2 C API + Arrow StringBuilders.

Uses lxml for XML parsing (DOM), then iterates the libxml2 tree at C level
writing directly to Arrow StringBuilders. Best of both worlds: lxml's
mature parser + C-level extraction + zero-copy Arrow output.
"""

from libcpp.string cimport string
from libcpp.memory cimport shared_ptr, make_shared
from libcpp.vector cimport vector
from libcpp cimport bool
from libc.string cimport memcmp, strlen, strrchr, strcmp, strncmp
from libc.stdint cimport int64_t

# Arrow C++ types — declared BEFORE lxml imports to avoid Cython name mangling
# issues when lxml and pyarrow cdef namespaces collide
cdef extern from "arrow/api.h" namespace "arrow":
    cdef cppclass CStatus "arrow::Status":
        pass

    cdef cppclass CDataType "arrow::DataType":
        pass

    cdef cppclass CField "arrow::Field":
        pass

    cdef cppclass CSchema "arrow::Schema":
        CSchema(vector[shared_ptr[CField]])

    cdef cppclass CArray "arrow::Array":
        int64_t length()

    cdef cppclass CArrayBuilder "arrow::ArrayBuilder":
        CStatus AppendNull()
        CStatus Finish(shared_ptr[CArray]* out)

    cdef cppclass CStringBuilder "arrow::StringBuilder"(CArrayBuilder):
        CStringBuilder(CMemoryPool*)
        CStatus Append(const string& value)

    cdef cppclass CRecordBatch "arrow::RecordBatch":
        @staticmethod
        shared_ptr[CRecordBatch] Make(shared_ptr[CSchema], int64_t, vector[shared_ptr[CArray]])

    cdef cppclass CMemoryPool "arrow::MemoryPool":
        pass

    CMemoryPool* c_default_memory_pool "arrow::default_memory_pool"()
    shared_ptr[CDataType] arrow_utf8 "arrow::utf8"()

cdef extern from "arrow/python/api.h":
    pass

# Use arrow C API for wrapping

cdef extern from *:
    """
    #include <arrow/api.h>
    #include <arrow/python/pyarrow.h>

    static PyObject* _wrap_record_batch(std::shared_ptr<arrow::RecordBatch> batch) {
        if (arrow::py::import_pyarrow() != 0) return nullptr;
        return arrow::py::wrap_batch(batch);
    }
    """
    object _wrap_record_batch(shared_ptr[CRecordBatch] batch)


# lxml C types
from lxml.includes.tree cimport (
    xmlNode, xmlAttr, xmlNs, xmlChar, const_xmlChar,
)

# Force lxml's etree_api.h to use C linkage (it declares utf8 as a C symbol
# but we're compiling as C++ which would mangle it)
cdef extern from *:
    """
    // Import lxml's C API, then undef its utf8 macro which
    // conflicts with arrow::utf8()
    extern "C" {
    #include "etree_api.h"
    }
    #ifdef utf8
    #undef utf8
    #endif
    """
    pass

cdef extern from "etree_api.h":
    ctypedef struct LxmlElement "LxmlElement":
        xmlNode* _c_node


cdef inline void clean_id_to_str(const xmlChar* raw, string& out) noexcept:
    """Clean CIM ID prefixes from xmlChar* into C++ string."""
    if raw == NULL:
        out.clear()
        return
    cdef size_t raw_len = strlen(<const char*>raw)
    cdef const char* s = <const char*>raw

    if raw_len >= 9 and memcmp(s, "urn:uuid:", 9) == 0:
        s = s + 9; raw_len -= 9
    if raw_len >= 2 and s[0] == b'#' and s[1] == b'_':
        s = s + 2; raw_len -= 2
    elif raw_len >= 1 and s[0] == b'_':
        s = s + 1; raw_len -= 1
    out.assign(s, raw_len)


cdef extern from *:
    """
    #include <cstring>
    static inline void _clean_ref_value(const char* raw, std::string& out) {
        size_t len = strlen(raw);
        const char* s = raw;
        if (len >= 9 && memcmp(s, "urn:uuid:", 9) == 0) { s += 9; len -= 9; }
        if (len >= 2 && s[0] == '#' && s[1] == '_') { s += 2; len -= 2; }
        else if (len >= 1 && s[0] == '_') { s += 1; len -= 1; }
        out.assign(s, len);
        if (out.size() >= 4 && memcmp(out.c_str(), "http", 4) == 0) {
            size_t pos = out.rfind('#');
            if (pos != std::string::npos) { out = out.substr(pos + 1); }
        }
    }
    """
    void _clean_ref_value(const char* raw, string& out) noexcept


cdef inline const char* local_name_from_ns(const xmlChar* name) noexcept:
    """Extract local name after '}' from '{ns}local' format, or after ':' from 'prefix:local'."""
    cdef const char* s = <const char*>name
    cdef const char* p = strrchr(s, b'}')
    if p != NULL:
        return p + 1
    p = strrchr(s, b':')
    if p != NULL:
        return p + 1
    return s


cdef inline const xmlChar* get_attr_value(xmlAttr* properties, const char* ns_href,
                                           const char* local) noexcept:
    """Find attribute value by namespace href + local name on xmlNode properties chain."""
    cdef xmlAttr* attr = properties
    while attr != NULL:
        if attr.name != NULL and strcmp(<const char*>attr.name, local) == 0:
            # Check namespace match
            if ns_href == NULL or ns_href[0] == 0:
                if attr.ns == NULL or attr.ns.href == NULL:
                    if attr.children != NULL and attr.children.content != NULL:
                        return attr.children.content
            elif attr.ns != NULL and attr.ns.href != NULL:
                if strcmp(<const char*>attr.ns.href, ns_href) == 0:
                    if attr.children != NULL and attr.children.content != NULL:
                        return attr.children.content
        attr = attr.next
    return NULL


def extract_rdf_to_arrow(object root_element, str file_name, str instance_id):
    """Extract RDF data from lxml element tree directly to Arrow RecordBatch.

    Parameters
    ----------
    root_element : lxml.etree._Element
        The parsed rdf:RDF root element
    file_name : str
        Name of the source file (for Distribution metadata)
    instance_id : str
        UUID for this loaded instance

    Returns
    -------
    pyarrow.RecordBatch
        RecordBatch with columns [ID, KEY, VALUE, INSTANCE_ID]
    """
    import uuid as uuid_mod

    cdef xmlNode* c_root = (<LxmlElement*>root_element)._c_node
    if c_root == NULL:
        raise ValueError("Empty root element")

    cdef string c_instance_id = instance_id.encode('utf-8')
    cdef string c_meta_id = str(uuid_mod.uuid4()).encode('utf-8')
    cdef string c_nsmap_id = str(uuid_mod.uuid4()).encode('utf-8')
    cdef bytes file_name_bytes = file_name.encode('utf-8')

    # Arrow builders
    cdef CMemoryPool* pool = c_default_memory_pool()
    cdef CStringBuilder* id_b = new CStringBuilder(pool)
    cdef CStringBuilder* key_b = new CStringBuilder(pool)
    cdef CStringBuilder* val_b = new CStringBuilder(pool)
    cdef CStringBuilder* inst_b = new CStringBuilder(pool)

    # All variable declarations
    cdef string s_type = b"Type"
    cdef string s_distribution = b"Distribution"
    cdef string s_label = b"label"
    cdef string s_nsmap = b"NamespaceMap"
    cdef string s_xml_base = b"xml_base"
    cdef string tmp_str
    cdef string obj_id
    cdef string key_str
    cdef string val_str
    cdef xmlNode* rdf_obj
    cdef xmlNode* elem
    cdef xmlNode* text_node
    cdef const xmlChar* raw_id
    cdef const xmlChar* text_val
    cdef const xmlChar* ref_val
    cdef const char* tag_local
    cdef xmlNs* ns_def
    cdef shared_ptr[CArray] id_array, key_array, value_array, inst_array
    cdef shared_ptr[CDataType] utf8_type
    cdef vector[shared_ptr[CField]] fields
    cdef shared_ptr[CSchema] schema
    cdef vector[shared_ptr[CArray]] columns
    cdef shared_ptr[CRecordBatch] batch

    cdef const char* RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

    try:
        # Metadata: Distribution
        id_b.Append(c_meta_id)
        key_b.Append(s_type)
        val_b.Append(s_distribution)
        inst_b.Append(c_instance_id)

        id_b.Append(c_meta_id)
        key_b.Append(s_label)
        val_b.Append(<string>file_name_bytes)
        inst_b.Append(c_instance_id)

        # Metadata: NamespaceMap
        id_b.Append(c_nsmap_id)
        key_b.Append(s_type)
        val_b.Append(s_nsmap)
        inst_b.Append(c_instance_id)

        # Extract namespace declarations from root
        ns_def = c_root.nsDef
        while ns_def != NULL:
            id_b.Append(c_nsmap_id)
            if ns_def.prefix != NULL:
                tmp_str.assign(<const char*>ns_def.prefix)
            else:
                tmp_str.clear()
            key_b.Append(tmp_str)
            if ns_def.href != NULL:
                tmp_str.assign(<const char*>ns_def.href)
            else:
                tmp_str.clear()
            val_b.Append(tmp_str)
            inst_b.Append(c_instance_id)
            ns_def = ns_def.next

        # xml:base (lxml stores it on the doc)
        id_b.Append(c_nsmap_id)
        key_b.Append(s_xml_base)
        val_b.Append(<string>file_name_bytes)
        inst_b.Append(c_instance_id)

        # Iterate RDF objects (children of rdf:RDF root)
        rdf_obj = c_root.children
        while rdf_obj != NULL:
            # Skip non-element nodes (text, comments, etc.)
            if rdf_obj.type != 1:  # XML_ELEMENT_NODE
                rdf_obj = rdf_obj.next
                continue

            # Get ID: try rdf:ID, rdf:about, rdf:nodeID
            raw_id = get_attr_value(rdf_obj.properties, RDF_NS, "ID")
            if raw_id == NULL:
                raw_id = get_attr_value(rdf_obj.properties, RDF_NS, "about")
            if raw_id == NULL:
                raw_id = get_attr_value(rdf_obj.properties, RDF_NS, "nodeID")

            if raw_id != NULL:
                clean_id_to_str(raw_id, obj_id)
            else:
                obj_id.clear()

            # Type from element tag (local name)
            tag_local = local_name_from_ns(rdf_obj.name)
            id_b.Append(obj_id)
            key_b.Append(s_type)
            tmp_str.assign(tag_local)
            val_b.Append(tmp_str)
            inst_b.Append(c_instance_id)

            # Properties (children of RDF object)
            elem = rdf_obj.children
            while elem != NULL:
                if elem.type != 1:  # XML_ELEMENT_NODE
                    elem = elem.next
                    continue

                tag_local = local_name_from_ns(elem.name)
                key_str.assign(tag_local)

                id_b.Append(obj_id)
                key_b.Append(key_str)
                inst_b.Append(c_instance_id)

                # Try text content (first text child node)
                text_node = elem.children
                text_val = NULL
                while text_node != NULL:
                    if text_node.type == 3:  # XML_TEXT_NODE
                        if text_node.content != NULL and text_node.content[0] != 0:
                            text_val = text_node.content
                            break
                    text_node = text_node.next

                if text_val != NULL:
                    tmp_str.assign(<const char*>text_val)
                    val_b.Append(tmp_str)
                else:
                    # Check rdf:resource or rdf:nodeID attribute
                    ref_val = get_attr_value(elem.properties, RDF_NS, "resource")
                    if ref_val == NULL:
                        ref_val = get_attr_value(elem.properties, RDF_NS, "nodeID")

                    if ref_val != NULL:
                        _clean_ref_value(<const char*>ref_val, val_str)
                        val_b.Append(val_str)
                    else:
                        val_b.AppendNull()

                elem = elem.next

            rdf_obj = rdf_obj.next

        # Finalize Arrow arrays
        id_b.Finish(&id_array)
        key_b.Finish(&key_array)
        val_b.Finish(&value_array)
        inst_b.Finish(&inst_array)

        # Build schema + RecordBatch
        utf8_type = arrow_utf8()
        fields.push_back(make_shared[CField](b"ID", utf8_type, False))
        fields.push_back(make_shared[CField](b"KEY", utf8_type, False))
        fields.push_back(make_shared[CField](b"VALUE", utf8_type, True))
        fields.push_back(make_shared[CField](b"INSTANCE_ID", utf8_type, False))
        schema = make_shared[CSchema](fields)

        columns.push_back(id_array)
        columns.push_back(key_array)
        columns.push_back(value_array)
        columns.push_back(inst_array)

        batch = CRecordBatch.Make(schema, id_array.get().length(), columns)

        return _wrap_record_batch(batch)

    finally:
        del id_b
        del key_b
        del val_b
        del inst_b
