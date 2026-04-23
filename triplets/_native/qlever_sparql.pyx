# distutils: language = c++
# cython: language_level=3, boundscheck=False, wraparound=False
"""
Cython wrapper around libqlever for embedded SPARQL query execution.

Uses qlever's C++ library directly (compiled from source) for in-process
SPARQL query evaluation — no server needed.

Build requirements:
    - libqlever (built from source via cmake)
    - Boost, ICU, OpenSSL, ZSTD, jemalloc (system deps)
    - Cython
"""

from libcpp.string cimport string
from libcpp.memory cimport unique_ptr

import json

# Declare the C++ wrapper class
cdef extern from "qlever_wrapper.h":
    cdef cppclass QleverWrapper:
        QleverWrapper(const string& index_basename, int memory_gb) except +
        string query(const string& sparql) except +
        string query_tsv(const string& sparql) except +

        @staticmethod
        void build_index(const string& input_file,
                        const string& index_basename,
                        const string& filetype,
                        int memory_gb) except +


cdef class QleverEngine:
    """In-process SPARQL engine powered by qlever.

    Usage::

        # Build index from Turtle/NTriples file
        QleverEngine.create_index("data.ttl", "/tmp/myindex")

        # Load and query
        engine = QleverEngine("/tmp/myindex")
        results = engine.sparql("SELECT * WHERE { ?s ?p ?o } LIMIT 10")
    """
    cdef QleverWrapper* _engine

    def __cinit__(self, str index_basename, int memory_gb=1):
        self._engine = new QleverWrapper(
            index_basename.encode('utf-8'),
            memory_gb
        )

    def __dealloc__(self):
        if self._engine != NULL:
            del self._engine

    @staticmethod
    def create_index(str input_file, str index_basename,
                     str filetype="turtle", int memory_gb=1):
        """Build a qlever index from an RDF file.

        Args:
            input_file: Path to RDF file (Turtle or N-Triples)
            index_basename: Base path for index files
            filetype: "turtle" or "ntriples"
            memory_gb: Memory limit in GB
        """
        QleverWrapper.build_index(
            input_file.encode('utf-8'),
            index_basename.encode('utf-8'),
            filetype.encode('utf-8'),
            memory_gb
        )

    def sparql(self, str query) -> list:
        """Execute a SPARQL query and return results as list of dicts.

        Args:
            query: SPARQL SELECT query string

        Returns:
            List of dicts, one per result row, keys are variable names
        """
        cdef string result = self._engine.query(query.encode('utf-8'))
        parsed = json.loads(result.decode('utf-8'))

        # Parse SPARQL JSON results format
        variables = parsed.get('head', {}).get('vars', [])
        rows = []
        for binding in parsed.get('results', {}).get('bindings', []):
            row = {}
            for var in variables:
                if var in binding:
                    row[var] = binding[var].get('value', '')
                else:
                    row[var] = None
            rows.append(row)
        return rows

    def sparql_raw(self, str query) -> str:
        """Execute a SPARQL query and return raw JSON string."""
        cdef string result = self._engine.query(query.encode('utf-8'))
        return result.decode('utf-8')

    def sparql_tsv(self, str query) -> str:
        """Execute a SPARQL query and return TSV results."""
        cdef string result = self._engine.query_tsv(query.encode('utf-8'))
        return result.decode('utf-8')
