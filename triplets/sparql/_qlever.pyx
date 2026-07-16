# distutils: language = c++
# cython: language_level=3
"""Cython binding for the embedded qlever SPARQL engine (via libqlever).

Three boundaries, all dumb (all result shaping lives in sparql_qlever.py):
- strings: SPARQL text → serialized result (SPARQL 1.1 JSON / Turtle / CSV),
  used for ASK and diagnostics.
- Arrow out: SPARQL text → pyarrow.RecordBatch of utf8 columns — the result
  path. The C++ wrapper decodes the result straight into Arrow buffers (same
  pattern as the cython_pugixml_arrow parser); here they are only wrapped
  zero-copy via pyarrow_wrap_array.
- Arrow in: pyarrow.RecordBatches → on-disk index (build_index_from_arrow).
  The batches are unwrapped zero-copy via pyarrow_unwrap_batch and feed
  qlever's index builder through an injected parser — no RDF text
  serialization or re-parsing anywhere.

Scope: query methods take an optional list of named-graph IRIs, passed to
qlever as SPARQL-protocol dataset clauses — the query text is never modified.

The GIL is released during index building, querying and decoding, so Python
threads parallelize across queries.

Build: python setup_qlever.py build_ext --inplace
(needs a compiled qlever checkout — see setup_qlever.py).
"""

from libcpp.memory cimport shared_ptr
from libcpp.string cimport string
from libcpp.unordered_map cimport unordered_map
from libcpp.unordered_set cimport unordered_set
from libcpp.utility cimport move
from libcpp.vector cimport vector

from pyarrow.includes.libarrow cimport CArray, CRecordBatch
from pyarrow.lib cimport pyarrow_wrap_array, pyarrow_unwrap_batch

import pyarrow


cdef extern from "_qlever_wrapper.h" nogil:
    cdef cppclass ArrowColumns:
        vector[shared_ptr[CArray]] columns
        vector[string] names

    cdef cppclass TermMapping:
        unordered_set[string] enumKeys
        unordered_map[string, string] keyNamespaces
        unordered_map[string, string] keyDatatypes
        string defaultNamespace

    cdef cppclass QleverWrapper:
        QleverWrapper(const string& index_basename, int memory_gb) except +
        string query(const string& sparql, const string& media_type,
                     const vector[string]& scope_graphs) except +
        ArrowColumns select_arrow(const string& sparql,
                                  const vector[string]& scope_graphs) except +
        ArrowColumns construct_arrow(const string& sparql,
                                     const vector[string]& scope_graphs) except +

        @staticmethod
        void set_quiet(bint quiet) except +

        @staticmethod
        void build_index(const string& input_file, const string& index_basename,
                         const string& filetype, int memory_gb) except +

        @staticmethod
        void build_index_from_arrow(vector[shared_ptr[CRecordBatch]] batches,
                                    TermMapping mapping,
                                    const string& index_basename,
                                    int memory_gb) except +


cdef _wrap_batch(ArrowColumns result):
    """C++-built columns → pyarrow.RecordBatch (zero-copy wrap)."""
    arrays = [pyarrow_wrap_array(result.columns[i])
              for i in range(result.columns.size())]
    names = [result.names[i].decode() for i in range(result.names.size())]
    return pyarrow.RecordBatch.from_arrays(arrays, names=names)


cdef vector[string] _scope_vector(scope_graphs):
    cdef vector[string] result
    if scope_graphs is not None:
        for graph in scope_graphs:
            result.push_back(<string>graph.encode())
    return result


def set_quiet(quiet=True):
    """Silence (or restore) qlever's own INFO logging on stdout."""
    QleverWrapper.set_quiet(quiet)


def build_index(str input_file, str index_basename, str filetype="nq", int memory_gb=4):
    """Build an on-disk qlever index from an N-Quads ("nq") or Turtle ("ttl") file."""
    cdef string c_input = input_file.encode()
    cdef string c_basename = index_basename.encode()
    cdef string c_filetype = filetype.encode()
    with nogil:
        QleverWrapper.build_index(c_input, c_basename, c_filetype, memory_gb)


def build_index_from_arrow(batches, str index_basename, enum_keys, key_namespaces,
                           key_datatypes, str default_namespace, int memory_gb=4):
    """Build an on-disk qlever index directly from triplet Arrow batches
    (columns ID, KEY, VALUE, INSTANCE_ID; zero-copy; term mapping identical
    to the N-Quads export rules — see _qlever_arrow_parser.cpp)."""
    cdef vector[shared_ptr[CRecordBatch]] c_batches
    for batch in batches:
        c_batches.push_back(pyarrow_unwrap_batch(batch))
    cdef TermMapping mapping
    for key in enum_keys:
        mapping.enumKeys.insert(<string>key.encode())
    for key, namespace in key_namespaces.items():
        mapping.keyNamespaces[<string>key.encode()] = <string>namespace.encode()
    for key, datatype in key_datatypes.items():
        mapping.keyDatatypes[<string>key.encode()] = <string>(datatype or "").encode()
    mapping.defaultNamespace = <string>default_namespace.encode()
    cdef string c_basename = index_basename.encode()
    with nogil:
        QleverWrapper.build_index_from_arrow(move(c_batches), move(mapping),
                                             c_basename, memory_gb)


cdef class QleverIndex:
    """A loaded qlever index; query() returns the raw serialized result string."""

    cdef QleverWrapper* _engine

    def __cinit__(self, str index_basename, int memory_gb=4):
        cdef string c_basename = index_basename.encode()
        with nogil:
            self._engine = new QleverWrapper(c_basename, memory_gb)

    def __dealloc__(self):
        if self._engine != NULL:
            del self._engine

    def query(self, str sparql, str media_type="sparqljson", scope_graphs=None) -> str:
        """SPARQL → serialized result ("sparqljson" | "turtle" | "tsv" | "csv")."""
        cdef string c_sparql = sparql.encode()
        cdef string c_media = media_type.encode()
        cdef vector[string] c_scope = _scope_vector(scope_graphs)
        cdef string result
        with nogil:
            result = self._engine.query(c_sparql, c_media, c_scope)
        return result.decode()

    def select_arrow(self, str sparql, scope_graphs=None):
        """SELECT → pyarrow.RecordBatch (utf8 columns; unbound → null)."""
        cdef string c_sparql = sparql.encode()
        cdef vector[string] c_scope = _scope_vector(scope_graphs)
        cdef ArrowColumns result
        with nogil:
            result = self._engine.select_arrow(c_sparql, c_scope)
        return _wrap_batch(result)

    def construct_arrow(self, str sparql, scope_graphs=None):
        """CONSTRUCT/DESCRIBE → pyarrow.RecordBatch (subject/predicate/object
        utf8 columns, N-Triples-form terms)."""
        cdef string c_sparql = sparql.encode()
        cdef vector[string] c_scope = _scope_vector(scope_graphs)
        cdef ArrowColumns result
        with nogil:
            result = self._engine.construct_arrow(c_sparql, c_scope)
        return _wrap_batch(result)
