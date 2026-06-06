# distutils: language = c++
# cython: language_level=3, boundscheck=False, wraparound=False
"""
cython_pugixml: Cython + pugixml (direct) + pyarrow → Arrow RecordBatch (zero-copy).

This is the fastest "pugixml parser" / performance backend for triplets CIMXML parsing.
It uses pugixml C++ directly + Arrow C++ builders (via Cython) for minimal overhead.

IMPORTANT FOR MAINTAINERS:
  The hot loop uses very low-level Arrow C++ (CStringBuilder + a thin wrapper
  around StringDictionary32Builder) and direct char* Append paths. This is
  intentional for the ~9x speedup on large models. Readability of the *per-row*
  path is deliberately sacrificed a bit for speed; surrounding code and comments
  try to compensate.

  See CYTHON_PERFORMANCE_IDEAS.md (in the docs repo) for the history of these
  trade-offs and further ideas.

Build: pixi (see pixi.toml) or python setup_cython_parser.py build_ext --inplace
(see setup.py / setup_cython_parser.py for Extension + vendor/pugixml).

Exposes load_rdf_to_dataframe(...) which returns a pyarrow.RecordBatch.
"""

from libcpp.string cimport string
from libcpp.string_view cimport string_view
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
from pyarrow.lib cimport pyarrow_wrap_batch, pyarrow_wrap_array

from cpython.buffer cimport PyObject_GetBuffer, PyBuffer_Release, PyBUF_SIMPLE

import mmap
import os

cdef extern from "arrow/type.h" namespace "arrow":
    shared_ptr[CDataType] utf8()
    shared_ptr[CDataType] int32()

# --- Low-level Arrow dictionary builder for the hot path ---
# We define a thin wrapper entirely inside an inline C++ block so the Cython
# layer never mentions the internal "StringDictionary32Builder" name or its
# header directly. This improves maintainability if pyarrow/Arrow internals shift.
#
# The wrapper just forwards to arrow::StringDictionary32Builder which does:
#   - hash lookup on the dictionary for the string
#   - appends an int32 index instead of the string data
# This is what gives us dictionary-encoded KEY "for free" during extraction
# (no post-hoc pa.compute.dictionary_encode on 1M+ rows).
cdef extern from *:
    """
    #include "arrow/array/builder_dict.h"
    #include "arrow/array/builder_binary.h"   // brings in arrow::StringBuilder (what CStringBuilder is typedef'd to)

    // Thin wrapper so Cython code stays readable and insulated from Arrow internals.
    class KeyDictBuilder {
    public:
        explicit KeyDictBuilder(arrow::MemoryPool* pool) : inner_(pool) {}
        arrow::Status Append(const char* value, int length) {
            return inner_.Append(value, length);
        }
        arrow::Status Finish(std::shared_ptr<arrow::Array>* out) {
            return inner_.Finish(out);
        }
    private:
        arrow::StringDictionary32Builder inner_;
    };

    // ------------------------------------------------------------------
    // Ergonomic Append helpers
    // These let us write Append(builder, some_string) or Append(builder, b"Literal")
    // without manually spelling out lengths or .c_str() + .size() everywhere.
    // This is purely for readability in the non-hot paths (metadata, etc.).
    // The hot loop still often passes explicit lengths when we already have them
    // from strlen(local_name(...)).
    // ------------------------------------------------------------------

    // We use the real Arrow type arrow::StringBuilder here (CStringBuilder is
    // just a Cython convenience typedef from pyarrow.includes).
    // We must include the header so the name is known inside this inline block.

    // std::string version - length comes from the string itself
    inline arrow::Status Append(arrow::StringBuilder* b, const std::string& s) {
        return b->Append(s.c_str(), (int)s.size());
    }
    inline arrow::Status Append(KeyDictBuilder* b, const std::string& s) {
        return b->Append(s.c_str(), (int)s.size());
    }

    // const char* version - for C string literals (b"Type" etc.).
    // strlen on a string literal is basically free (often constant-folded).
    inline arrow::Status Append(arrow::StringBuilder* b, const char* s) {
        return b->Append(s, (int)strlen(s));
    }
    inline arrow::Status Append(KeyDictBuilder* b, const char* s) {
        return b->Append(s, (int)strlen(s));
    }

    // Explicit length version - when we already know the length
    // (dynamic names from XML, Python bytes objects, etc.)
    inline arrow::Status Append(arrow::StringBuilder* b, const char* s, int len) {
        return b->Append(s, len);
    }
    inline arrow::Status Append(KeyDictBuilder* b, const char* s, int len) {
        return b->Append(s, len);
    }

    // Convenience for Python bytes objects (used for file_name_bytes etc.)
    // This is only for the few non-hot-path appends.
    inline arrow::Status Append(arrow::StringBuilder* b, PyObject* pybytes) {
        if (PyBytes_Check(pybytes)) {
            return b->Append(PyBytes_AS_STRING(pybytes), (int)PyBytes_GET_SIZE(pybytes));
        }
        return arrow::Status::Invalid("Append expected bytes");
    }
    """
    cdef cppclass KeyDictBuilder:
        KeyDictBuilder(CMemoryPool* pool) except +
        CStatus Append(const char* value, int length)
        CStatus Finish(shared_ptr[CArray]* out)

    # Overloads for the ergonomic Append helpers defined above.
    # Cython has decent support for C++ overload resolution here.
    CStatus Append(CStringBuilder* b, const string& s)
    CStatus Append(KeyDictBuilder* b, const string& s)

    CStatus Append(CStringBuilder* b, const char* s)
    CStatus Append(KeyDictBuilder* b, const char* s)

    CStatus Append(CStringBuilder* b, const char* s, int len)
    CStatus Append(KeyDictBuilder* b, const char* s, int len)

    # Overload for Python bytes (so we can write Append(val_b, file_name_bytes) directly)
    CStatus Append(CStringBuilder* b, object pybytes)

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


# ── C++ helpers for string processing (all happen without Python objects) ─────
cdef extern from *:
    """
    #include <cstring>
    #include <string>
    #include <string_view>

    // Clean CIM ID prefixes: "urn:uuid:", "#_", "_"
    // Pure string_view — zero-copy slice into the original pugixml buffer.
    static inline std::string_view clean_id(std::string_view sv) {
        using namespace std::string_view_literals;
        constexpr auto urn_uuid   = "urn:uuid:"sv;
        constexpr auto hash_under = "#_"sv;
        constexpr auto underscore = "_"sv;
        if (sv.starts_with(urn_uuid))        sv.remove_prefix(urn_uuid.size());
        else if (sv.starts_with(hash_under)) sv.remove_prefix(hash_under.size());
        else if (sv.starts_with(underscore)) sv.remove_prefix(underscore.size());
        return sv;
    }

    // Clean a reference value (for rdf:resource etc).
    // Strips CIM ID prefixes, then extracts fragment after '#' for http URIs.
    // Returns a (possibly shortened) view into the original buffer.
    static inline std::string_view clean_ref_value(std::string_view sv) {
        using namespace std::string_view_literals;
        constexpr auto http_prefix = "http"sv;
        std::string_view v = clean_id(sv);
        if (v.starts_with(http_prefix)) {
            size_t pos = v.rfind('#');
            if (pos != std::string_view::npos) {
                v = v.substr(pos + 1);
            }
        }
        return v;
    }

    // Extract local name from "prefix:localname" or "{ns}local".
    static inline const char* local_name(const char* name) {
        const char* colon = strrchr(name, ':');
        return colon ? colon + 1 : name;
    }
    """
    string_view clean_id(string_view sv) noexcept
    string_view clean_ref_value(string_view sv) noexcept
    const char* local_name(const char* name) noexcept


cdef unsigned int PARSE_FLAGS = parse_minimal | parse_embed_pcdata


def load_rdf_to_dataframe(path_or_fileobject, debug=False):
    """Parse RDF XML and return a PyArrow RecordBatch directly.

    The entire pipeline — XML parse, element iteration, Arrow building —
    happens in C++ via Cython. Returns a PyArrow RecordBatch (zero-copy).

    Special optimization: when path_or_fileobject is a str (real local filesystem path),
    the file is memory-mapped. pugixml then parses directly from the kernel page cache
    with no extra user-space copy of the full document. This makes loading from actual
    on-disk files even faster / lower memory than reading everything into Python bytes first.
    File-like objects fall back to an explicit read() + load_buffer.
    """
    # We import uuid here (inside the function) so the module can be imported
    # even if someone never calls the cython path. The import is cheap after
    # the first time.
    import uuid as uuid_mod

    cdef str file_name
    if isinstance(path_or_fileobject, str):
        file_name = path_or_fileobject
    else:
        file_name = getattr(path_or_fileobject, 'name', '<file-like>')

    # Debug timing and per-file identity
    cdef object start_time
    cdef object _dt  # lazy datetime module, only when debug=True
    if debug:
        import datetime as _dt_mod
        _dt = _dt_mod
        start_time = _dt.datetime.now()

    # Per-file identity (used for INSTANCE_ID column and for metadata rows).
    # We generate the UUIDs here (once per XML file) rather than on every row.
    cdef string instance_id = str(uuid_mod.uuid4()).encode('utf-8')
    cdef string meta_id = str(uuid_mod.uuid4()).encode('utf-8')
    cdef string nsmap_id = str(uuid_mod.uuid4()).encode('utf-8')
    cdef bytes file_name_bytes = file_name.encode('utf-8')

    # Parse XML with pugixml
    cdef xml_document doc
    cdef xml_parse_result result
    cdef bytes content_bytes
    cdef const char* buf
    cdef size_t buf_len
    cdef const unsigned char[::1] _mm_view
    _mmap_keepalive = None   # keep mmap and file open while we parse + build

    if isinstance(path_or_fileobject, str):
        # mmap path for real local files (the optimization)
        try:
            f = open(path_or_fileobject, "rb")
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            _mmap_keepalive = (f, mm)
            _mm_view = mm
            buf = <const char*>&_mm_view[0]
            buf_len = len(_mm_view)
            result = doc.load_buffer(buf, buf_len, PARSE_FLAGS)
        except Exception:
            # fallback
            if _mmap_keepalive is not None:
                try:
                    ff, mmm = _mmap_keepalive
                    mmm.close()
                    ff.close()
                except Exception:
                    pass
            _mmap_keepalive = None
            # fall back to read
            with open(path_or_fileobject, "rb") as ff:
                content_bytes = ff.read()
            buf = content_bytes
            buf_len = len(content_bytes)
            result = doc.load_buffer(buf, buf_len, PARSE_FLAGS)
    else:
        # file-like: read into memory
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
        end = _dt.datetime.now()
        print(f"  XML parse: {end - start_time}")
        start_time = end

    cdef xml_node root = doc.first_child()

    # ------------------------------------------------------------------
    # Arrow column builders (allocated on the C++ heap for the hot path)
    # ------------------------------------------------------------------
    # ID    : high cardinality (mostly unique) → plain CStringBuilder
    # KEY   : very low cardinality (~hundreds of property names + "Type")
    #         → KeyDictBuilder (our wrapper around StringDictionary32Builder)
    #         builds a DictionaryArray *during* the loop. No post-processing.
    # VALUE : mixed cardinality → plain CStringBuilder
    # INSTANCE_ID : constant per file → deliberately *not* built in the loop.
    #               See the post-loop construction below for why.
    cdef CMemoryPool* pool = c_default_memory_pool()
    cdef CStringBuilder* id_b = new CStringBuilder(pool)
    cdef KeyDictBuilder* key_b = new KeyDictBuilder(pool)
    cdef CStringBuilder* val_b = new CStringBuilder(pool)

    # Namespace attribute prefixes (lengths derived from definitions, not hardcoded)
    cdef const char* XMLNS_COLON = b"xmlns:"
    cdef size_t XMLNS_COLON_LEN = strlen(XMLNS_COLON)
    cdef const char* XMLNS = b"xmlns"
    cdef size_t XMLNS_LEN = strlen(XMLNS)
    cdef const char* XML_BASE = b"xml:base"
    cdef size_t XML_BASE_LEN = strlen(XML_BASE)

    # Cython requires all C/C++ declarations before the try: block
    cdef xml_attribute attr
    cdef const char* aname
    cdef const char* aval
    cdef bint has_xml_base = False
    cdef xml_node rdf_object
    cdef xml_node element
    cdef const char* raw_id
    cdef const char* tag_name
    cdef const char* child_text
    cdef const char* ref_val
    cdef string_view obj_id, val_str     # string_view into the XML buffer (zero-copy after clean)
    cdef size_t text_len, name_len
    cdef shared_ptr[CArray] id_arr, key_arr, val_arr
    cdef int64_t nrows
    cdef vector[shared_ptr[CField]] fields
    cdef shared_ptr[CSchema] schema
    cdef vector[shared_ptr[CArray]] columns
    cdef shared_ptr[CRecordBatch] batch

    try:
        # ── Metadata rows (only a handful per file, not performance critical) ──
        # Thanks to the Append() overloads, we no longer need to manually
        # write lengths or .c_str() + .size() for every call.
        # Distribution header
        Append(id_b, meta_id)
        Append(key_b, b"Type")
        Append(val_b, b"Distribution")

        Append(id_b, meta_id)
        Append(key_b, b"label")
        Append(val_b, file_name_bytes)

        # NamespaceMap header
        Append(id_b, nsmap_id)
        Append(key_b, b"Type")
        Append(val_b, b"NamespaceMap")

        # ── Namespace declarations from root attributes ──────────────────
        attr = root.first_attribute()
        while not attr.empty():
            aname = attr.name()
            if memcmp(aname, XMLNS_COLON, XMLNS_COLON_LEN) == 0:
                Append(id_b, nsmap_id)
                aval = aname + XMLNS_COLON_LEN
                Append(key_b, aval)
                aval = attr.value()
                Append(val_b, aval)
            elif memcmp(aname, XMLNS, XMLNS_LEN) == 0 and aname[XMLNS_LEN] == 0:
                Append(id_b, nsmap_id)
                Append(key_b, b"")
                aval = attr.value()
                Append(val_b, aval)
            elif memcmp(aname, XML_BASE, XML_BASE_LEN) == 0:
                Append(id_b, nsmap_id)
                Append(key_b, b"xml_base")
                aval = attr.value()
                Append(val_b, aval)
                has_xml_base = True
            attr = attr.next_attribute()

        if not has_xml_base:
            Append(id_b, nsmap_id)
            Append(key_b, b"xml_base")
            Append(val_b, file_name_bytes)

        # ── RDF objects (the hot loop — every row is processed here) ──────
        # We deliberately avoid:
        #   - appending the same INSTANCE_ID string millions of times
        #   - using std::string for the high-frequency KEY column (use KeyDictBuilder instead)
        #   - Python objects or GIL
        rdf_object = root.first_child()
        while not rdf_object.empty():

            # Choose best ID source (rdf:ID > rdf:about > rdf:nodeID)
            raw_id_ptr = rdf_object.attribute(b"rdf:ID").value()
            if raw_id_ptr[0] == 0:
                raw_id_ptr = rdf_object.attribute(b"rdf:about").value()
            if raw_id_ptr[0] == 0:
                raw_id_ptr = rdf_object.attribute(b"rdf:nodeID").value()

            if raw_id_ptr[0] != 0:
                raw_len = strlen(raw_id_ptr)
                obj_id = clean_id(string_view(raw_id_ptr, raw_len))
            else:
                obj_id = string_view()

            # "Type" row for this object
            tag_name = local_name(rdf_object.name())
            Append(id_b, obj_id.data(), <int>obj_id.size())
            Append(key_b, b"Type")
            name_len = strlen(tag_name)
            Append(val_b, tag_name, <int>name_len)

            # All property rows for this object
            element = rdf_object.first_child()
            while not element.empty():
                tag_name = local_name(element.name())
                name_len = strlen(tag_name)

                Append(id_b, obj_id.data(), <int>obj_id.size())
                Append(key_b, tag_name, <int>name_len)

                # Value is either text content or an rdf:resource / rdf:nodeID reference
                child_text = element.child_value()
                text_len = strlen(child_text)

                if text_len > 0:
                    Append(val_b, child_text, <int>text_len)
                else:
                    ref_val = element.attribute(b"rdf:resource").value()
                    if ref_val[0] == 0:
                        ref_val = element.attribute(b"rdf:nodeID").value()

                    if ref_val[0] != 0:
                        ref_len = strlen(ref_val)
                        val_str = clean_ref_value(string_view(ref_val, ref_len))
                        Append(val_b, val_str.data(), <int>val_str.size())
                    else:
                        Append(val_b, b"")

                element = element.next_sibling()

            rdf_object = rdf_object.next_sibling()

        if debug:
            end = _dt.datetime.now()
            print(f"  Extraction: {end - start_time}")
            start_time = end

        # ── Build Arrow RecordBatch ──────────────────────────────────────
        id_b.Finish(&id_arr)
        key_b.Finish(&key_arr)      # already dictionary-encoded via KeyDictBuilder (int32 indices)
        val_b.Finish(&val_arr)

        nrows = id_arr.get().length()

        # Wrap the three C++-built arrays (ID, KEY dict, VALUE) into pyarrow objects.
        # We use the low-level pyarrow_wrap_array so we stay zero-copy from the
        # CStringBuilder / KeyDictBuilder data.
        id_col = pyarrow_wrap_array(id_arr)
        key_col = pyarrow_wrap_array(key_arr)
        val_col = pyarrow_wrap_array(val_arr)

        # INSTANCE_ID is a constant value repeated for every row in this file.
        # We deliberately did *not* append it N times in the hot loop (that was the
        # big win for cat=ON). Instead we construct a minimal DictionaryArray here:
        #   - one dictionary entry (the UUID string)
        #   - an indices array of all zeros (size = nrows)
        #
        # Using numpy + pa.DictionaryArray.from_arrays is fast for this "constant
        # column" case because the heavy lifting (1.14M string appends) was avoided.
        # This small Python-side construction happens only once per XML file.
        import pyarrow as pa
        import numpy as np
        inst_dict = pa.array([instance_id.decode('utf-8')])
        inst_indices = pa.array(np.zeros(nrows, dtype=np.int32))
        inst_col = pa.DictionaryArray.from_arrays(inst_indices, inst_dict)

        batch_py = pa.RecordBatch.from_arrays(
            [id_col, key_col, val_col, inst_col],
            names=["ID", "KEY", "VALUE", "INSTANCE_ID"],
        )

        if debug:
            end = _dt.datetime.now()
            print(f"  Arrow finalize: {end - start_time}")

        return batch_py

    finally:
        del id_b
        del key_b
        del val_b

        # Always release mmap resources (even on exception paths).
        # We kept the reference in _mmap_keepalive so the memory stayed valid
        # during pugixml load + our walking + Arrow builder appends.
        if _mmap_keepalive is not None:
            f, mm = _mmap_keepalive
            try:
                mm.close()
            except Exception:
                pass
            try:
                f.close()
            except Exception:
                pass



