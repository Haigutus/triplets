# distutils: language = c++
# cython: language_level=3, boundscheck=False, wraparound=False
"""
Cython extraction loop for RDF/CIM XML parsing.

Uses pugixml C++ API directly — no Python wrapper overhead per element.
The entire iteration over ~800K elements happens in C++ speed with
typed Cython variables.
"""

from libcpp.string cimport string
from libcpp cimport bool
from libc.string cimport memcmp, strlen, strrchr

# Declare pugixml C++ types we need
cdef extern from "pugixml.hpp" namespace "pugi":
    const unsigned int parse_minimal
    const unsigned int parse_default
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
        const char* value() const
        const char* child_value() const
        xml_node first_child() const
        xml_node next_sibling() const
        xml_node parent() const
        xml_attribute first_attribute() const
        xml_attribute attribute(const char* name) const
        bool empty() const

    cdef cppclass xml_document:
        xml_document() except +
        xml_parse_result load_file(const char* path, unsigned int options)
        xml_parse_result load_file(const char* path)
        xml_parse_result load_buffer(const void* contents, size_t size, unsigned int options)
        xml_parse_result load_buffer(const void* contents, size_t size)
        xml_node first_child() const


# C-level helper: extract local name from "prefix:local"
cdef inline const char* c_local_name(const char* name) noexcept:
    """Return pointer to local part after ':' or the original string."""
    cdef const char* colon = strrchr(name, b':')
    if colon != NULL:
        return colon + 1
    return name


# C-level helper: strip CIM ID prefixes
cdef inline str clean_id(const char* raw):
    """Remove urn:uuid:, #_, _ prefixes from a C string, return Python str."""
    cdef size_t raw_len = strlen(raw)
    cdef const char* s = raw

    # strip "urn:uuid:"
    if raw_len >= 9 and memcmp(s, b"urn:uuid:", 9) == 0:
        s = s + 9
        raw_len -= 9

    # strip "#_"
    if raw_len >= 2 and s[0] == b'#' and s[1] == b'_':
        s = s + 2
        raw_len -= 2
    # strip "_"
    elif raw_len >= 1 and s[0] == b'_':
        s = s + 1
        raw_len -= 1

    return s[:raw_len].decode('utf-8')


# C-level helper: handle enum values starting with "http"
cdef inline str clean_ref_value(const char* raw):
    """Clean a reference value: clean_id + handle http enumerations."""
    cdef str cleaned = clean_id(raw)
    if cleaned[:4] == "http":
        idx = cleaned.rfind("#")
        if idx >= 0:
            return cleaned[idx + 1:]
    return cleaned


cdef unsigned int PARSE_FLAGS = parse_minimal | parse_embed_pcdata


def load_rdf_to_list_cython(path_or_fileobject, debug=False):
    """Parse RDF XML to list of tuples using pugixml C++ directly.

    This replaces the Python-level iteration loop with typed Cython
    that calls pugixml C++ methods directly — no Python wrapper overhead.
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

    cdef str instance_id = str(uuid_mod.uuid4())
    cdef str meta_id = str(uuid_mod.uuid4())
    cdef str nsmap_id = str(uuid_mod.uuid4())

    # Parse XML
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
        print(f"  Cython XML parse: {end - start_time}")
        start_time = end

    cdef xml_node root = doc.first_child()

    # Pre-allocate result list
    cdef list data_list = [
        (meta_id, "Type", "Distribution", instance_id),
        (meta_id, "label", file_name, instance_id),
        (nsmap_id, "Type", "NamespaceMap", instance_id),
    ]

    # Extract namespace map from root attributes
    cdef xml_attribute attr = root.first_attribute()
    cdef const char* aname
    cdef str aval_str
    cdef str prefix_str
    cdef bint has_xml_base = False

    while not attr.empty():
        aname = attr.name()
        if memcmp(aname, b"xmlns:", 6) == 0:
            prefix_str = (aname + 6).decode('utf-8')
            aval_str = attr.value().decode('utf-8')
            data_list.append((nsmap_id, prefix_str, aval_str, instance_id))
        elif memcmp(aname, b"xmlns", 5) == 0 and aname[5] == 0:
            aval_str = attr.value().decode('utf-8')
            data_list.append((nsmap_id, "", aval_str, instance_id))
        elif memcmp(aname, b"xml:base", 8) == 0:
            aval_str = attr.value().decode('utf-8')
            data_list.append((nsmap_id, "xml_base", aval_str, instance_id))
            has_xml_base = True
        attr = attr.next_attribute()

    if not has_xml_base:
        data_list.append((nsmap_id, "xml_base", file_name, instance_id))

    # Iterate RDF objects — all in C++ typed variables
    cdef xml_node rdf_object = root.first_child()
    cdef xml_node element
    cdef const char* raw_id
    cdef const char* tag_name
    cdef const char* child_text
    cdef const char* ref_val
    cdef str ID
    cdef str KEY
    cdef str VALUE
    cdef str type_value
    cdef size_t text_len

    while not rdf_object.empty():
        # Get ID: try rdf:ID, rdf:about, rdf:nodeID
        raw_id = rdf_object.attribute(b"rdf:ID").value()
        if raw_id[0] == 0:
            raw_id = rdf_object.attribute(b"rdf:about").value()
        if raw_id[0] == 0:
            raw_id = rdf_object.attribute(b"rdf:nodeID").value()

        if raw_id[0] != 0:
            ID = clean_id(raw_id)
        else:
            ID = None

        # Type from tag
        tag_name = c_local_name(rdf_object.name())
        type_value = tag_name.decode('utf-8')
        data_list.append((ID, "Type", type_value, instance_id))

        # Properties
        element = rdf_object.first_child()
        while not element.empty():
            tag_name = c_local_name(element.name())
            KEY = tag_name.decode('utf-8')

            # Try text content first
            child_text = element.child_value()
            text_len = strlen(child_text)

            if text_len > 0:
                VALUE = child_text[:text_len].decode('utf-8')
            else:
                # Check rdf:resource or rdf:nodeID
                ref_val = element.attribute(b"rdf:resource").value()
                if ref_val[0] == 0:
                    ref_val = element.attribute(b"rdf:nodeID").value()

                if ref_val[0] != 0:
                    VALUE = clean_ref_value(ref_val)
                else:
                    VALUE = None

            data_list.append((ID, KEY, VALUE, instance_id))
            element = element.next_sibling()

        rdf_object = rdf_object.next_sibling()

    if debug:
        end = datetime.datetime.now()
        print(f"  Cython extraction: {end - start_time}")

    return data_list
