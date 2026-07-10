# distutils: language = c++
# cython: language_level=3
"""Cython binding for the embedded qlever SPARQL engine (via libqlever).

Two boundaries, both dumb (all result shaping lives in sparql_qlever.py):
- strings: SPARQL text → serialized result (SPARQL 1.1 JSON / Turtle / CSV),
  used for ASK and diagnostics.
- Arrow: SPARQL text → pyarrow.RecordBatch of utf8 columns — the data path.
  The C++ wrapper decodes the result straight into Arrow buffers (same
  pattern as the cython_pugixml_arrow parser); here they are only wrapped
  zero-copy via pyarrow_wrap_array.

The GIL is released during index building, querying and decoding, so Python
threads parallelize across queries.

Build: python setup_qlever.py build_ext --inplace
(needs a compiled qlever checkout — see setup_qlever.py).
"""

from libcpp.memory cimport shared_ptr
from libcpp.string cimport string
from libcpp.vector cimport vector

from pyarrow.includes.libarrow cimport CArray
from pyarrow.lib cimport pyarrow_wrap_array

import pyarrow


cdef extern from "_qlever_wrapper.h" nogil:
    cdef cppclass ArrowColumns:
        vector[shared_ptr[CArray]] columns
        vector[string] names

    cdef cppclass QleverWrapper:
        QleverWrapper(const string& index_basename, int memory_gb) except +
        string query(const string& sparql, const string& media_type) except +
        ArrowColumns select_arrow(const string& sparql) except +
        ArrowColumns construct_arrow(const string& sparql) except +

        @staticmethod
        void set_quiet(bint quiet) except +

        @staticmethod
        void build_index(const string& input_file, const string& index_basename,
                         const string& filetype, int memory_gb) except +


cdef _wrap_batch(ArrowColumns result):
    """C++-built columns → pyarrow.RecordBatch (zero-copy wrap)."""
    arrays = [pyarrow_wrap_array(result.columns[i])
              for i in range(result.columns.size())]
    names = [result.names[i].decode() for i in range(result.names.size())]
    return pyarrow.RecordBatch.from_arrays(arrays, names=names)


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

    def query(self, str sparql, str media_type="sparqljson") -> str:
        """SPARQL → serialized result ("sparqljson" | "turtle" | "tsv" | "csv")."""
        cdef string c_sparql = sparql.encode()
        cdef string c_media = media_type.encode()
        cdef string result
        with nogil:
            result = self._engine.query(c_sparql, c_media)
        return result.decode()

    def select_arrow(self, str sparql):
        """SELECT → pyarrow.RecordBatch (utf8 columns; unbound → null)."""
        cdef string c_sparql = sparql.encode()
        cdef ArrowColumns result
        with nogil:
            result = self._engine.select_arrow(c_sparql)
        return _wrap_batch(result)

    def construct_arrow(self, str sparql):
        """CONSTRUCT/DESCRIBE → pyarrow.RecordBatch (subject/predicate/object
        utf8 columns, N-Triples-form terms)."""
        cdef string c_sparql = sparql.encode()
        cdef ArrowColumns result
        with nogil:
            result = self._engine.construct_arrow(c_sparql)
        return _wrap_batch(result)
