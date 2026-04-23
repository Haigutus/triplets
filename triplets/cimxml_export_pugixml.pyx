# distutils: language = c++
# cython: language_level=3, boundscheck=False, wraparound=False
"""CIM XML export using pugixml C++ DOM builder.

Builds the XML tree using pugixml's C++ DOM API, then serializes it.
Two-pass approach: first creates all class nodes, then adds attributes.
Uses an inline C++ helper class to store xml_node handles (which can't
be stored directly in Python containers).
"""
from libcpp.string cimport string
from libcpp cimport bool as cbool
from libc.string cimport strlen

# pugixml C++ declarations
cdef extern from "pugixml.hpp" namespace "pugi":
    const unsigned int format_indent
    const unsigned int format_default
    const unsigned int format_write_bom
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
        cbool set_name(const char*)
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


# Inline C++ helper: string_writer and node_store
cdef extern from *:
    """
    #include <string>
    #include <vector>
    #include "pugixml.hpp"

    class string_writer : public pugi::xml_writer {
    public:
        std::string result;
        void write(const void* data, size_t size) override {
            result.append(static_cast<const char*>(data), size);
        }
    };

    class node_store {
    public:
        std::vector<pugi::xml_node> nodes;

        int add(pugi::xml_node n) {
            int idx = (int)nodes.size();
            nodes.push_back(n);
            return idx;
        }

        pugi::xml_node& get(int idx) {
            return nodes[idx];
        }

        pugi::xml_node append_child_to(int idx, const char* name) {
            return nodes[idx].append_child(name);
        }

        void append_attr_to_child(pugi::xml_node child, const char* attr_name, const char* attr_value) {
            child.append_attribute(attr_name).set_value(attr_value);
        }
    };
    """
    cdef cppclass string_writer(xml_writer):
        string_writer() except +
        string result

    cdef cppclass node_store:
        node_store() except +
        int add(xml_node n)
        xml_node& get(int idx)
        xml_node append_child_to(int idx, const char* name)
        void append_attr_to_child(xml_node child, const char* attr_name, const char* attr_value)


def generate_xml_bytes(instance_data,
                       rdf_map,
                       namespace_map,
                       instance_rdf_map,
                       str file_name,
                       str class_KEY="Type",
                       cbool export_undefined=True,
                       comment=None):
    """Generate CIM RDF/XML bytes using pugixml C++ DOM.

    Returns
    -------
    bytes : UTF-8 encoded XML
    """
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

    # Pre-build class defs: class_name -> (tag_bytes, id_attr_bytes, id_prefix)
    cdef dict class_defs = {}
    cdef dict tag_defs = {}

    for key, defn in instance_rdf_map.items():
        if not isinstance(defn, dict):
            continue
        if defn.get("type") == "Class":
            tag_str = _make_prefixed(defn["namespace"], key)
            id_attr = _parse_attrib(defn["attrib"]["attribute"])
            id_prefix = defn["attrib"]["value_prefix"]
            class_defs[key] = (tag_str.encode('utf-8'), id_attr.encode('utf-8'), id_prefix)
        elif defn.get("namespace"):
            tag_str = _make_prefixed(defn["namespace"], key)
            attrib = defn.get("attrib")
            text_prefix = defn.get("text", "")
            if attrib:
                attr_name = _parse_attrib(attrib["attribute"])
                vp = attrib.get("value_prefix", "")
                tag_defs[key] = (tag_str.encode('utf-8'), attr_name.encode('utf-8'), vp, text_prefix, True)
            else:
                tag_defs[key] = (tag_str.encode('utf-8'), b"", "", text_prefix, False)

    # Extract numpy arrays
    ids_arr = instance_data["ID"].values.astype(object)
    keys_arr = instance_data["KEY"].values.astype(object)
    values_arr = instance_data["VALUE"].values.astype(object)
    cdef Py_ssize_t n = len(ids_arr)
    cdef Py_ssize_t i

    # Build pugixml document
    cdef xml_document doc
    cdef xml_node decl_node
    cdef xml_node comment_node
    cdef xml_node rdf_root
    cdef xml_node obj_node
    cdef xml_node attr_node

    # XML declaration
    decl_node = doc.append_child(node_declaration)
    decl_node.append_attribute(b"version").set_value(b"1.0")
    decl_node.append_attribute(b"encoding").set_value(b"UTF-8")

    # Comment
    if comment:
        comment_node = doc.append_child(node_comment)
        comment_bytes = comment.encode('utf-8')
        comment_node.set_value(<const char*>comment_bytes)

    # RDF root with namespace attributes
    rdf_root = doc.append_child(b"rdf:RDF")
    cdef bytes uri_bytes
    for prefix, uri in namespace_map.items():
        ns_attr_name = f"xmlns:{prefix}".encode('utf-8')
        uri_bytes = uri.encode('utf-8')
        rdf_root.append_attribute(<const char*>ns_attr_name).set_value(<const char*>uri_bytes)

    # Node store for class elements (C++ side, avoids Python object conversion)
    cdef node_store store
    cdef dict obj_index = {}  # obj_id -> int index
    cdef bytes tag_bytes, id_attr_bytes, id_val_bytes
    cdef int idx

    # First pass: create class elements
    for i in range(n):
        key = keys_arr[i]
        if key != class_KEY:
            continue

        obj_id = ids_arr[i]
        class_name = values_arr[i]

        cd = class_defs.get(class_name)
        if cd is not None:
            tag_bytes = cd[0]
            id_attr_bytes = cd[1]
            id_prefix = cd[2]
        elif export_undefined:
            tag_bytes = class_name.encode('utf-8')
            id_attr_bytes = b"rdf:about"
            id_prefix = "urn:uuid:"
        else:
            continue

        id_val_bytes = f"{id_prefix}{obj_id}".encode('utf-8')
        obj_node = rdf_root.append_child(<const char*>tag_bytes)
        obj_node.append_attribute(<const char*>id_attr_bytes).set_value(<const char*>id_val_bytes)
        idx = store.add(obj_node)
        obj_index[obj_id] = idx

    # Second pass: add attributes
    cdef bytes attr_tag_bytes, attr_name_bytes, attr_val_bytes
    cdef int obj_idx

    for i in range(n):
        key = keys_arr[i]
        if key == class_KEY:
            continue

        obj_id = ids_arr[i]
        py_idx = obj_index.get(obj_id)
        if py_idx is None:
            continue

        value = values_arr[i]
        if value is None:
            continue
        if isinstance(value, float) and value != value:
            continue

        obj_idx = <int>py_idx

        td = tag_defs.get(key)
        if td is not None:
            attr_tag_bytes = td[0]
            is_ref = td[4]

            attr_node = store.append_child_to(obj_idx, <const char*>attr_tag_bytes)

            if is_ref:
                attr_name_bytes = td[1]
                vp = td[2]
                if not vp:
                    enum_def = instance_rdf_map.get(value)
                    if enum_def is not None and isinstance(enum_def, dict):
                        vp = enum_def.get("namespace", "")
                attr_val_bytes = f"{vp}{value}".encode('utf-8')
                store.append_attr_to_child(attr_node, <const char*>attr_name_bytes, <const char*>attr_val_bytes)
            else:
                text_prefix = td[3]
                attr_val_bytes = f"{text_prefix}{value}".encode('utf-8')
                attr_node.append_child(node_pcdata).set_value(<const char*>attr_val_bytes)
        elif export_undefined:
            attr_tag_bytes = key.encode('utf-8')
            attr_val_bytes = str(value).encode('utf-8')
            attr_node = store.append_child_to(obj_idx, <const char*>attr_tag_bytes)
            attr_node.append_child(node_pcdata).set_value(<const char*>attr_val_bytes)

    # Serialize to string
    cdef string_writer writer
    doc.save(writer, b"  ", format_indent, encoding_utf8)

    return writer.result
