# SPARQL Engine Benchmark: qlever vs oxigraph

**Date:** 2026-04-01
**Dataset:** CGMES v2.4.15 RealGrid EQ (66 MB RDF/XML → 892,147 triples)
**Input:** Both engines loaded from the same N-Quads file on disk (130 MB)
**Hardware:** Linux x86_64, GCC 15.2.1

## Engines

| Engine | Version | Language | Integration |
|--------|---------|----------|-------------|
| **oxigraph** | pyoxigraph 0.5.5 via oxrdflib 0.5.0 | Rust | Embedded Python lib (pip install) |
| **qlever** | libqlever (git master 2026-04-01) | C++ | Embedded C++ lib (built from source) |

## Data Loading (from .nq file on disk)

| Engine | Load time | Throughput |
|--------|-----------|------------|
| oxigraph | 35,142 ms | 25K triples/sec |
| qlever | 2,466 ms (index build) + 3 ms (index load) | 362K triples/sec |
| **Speedup** | **14.3x** | |

## Query Performance

Both engines return identical result counts.

| Query | Oxigraph | qlever | Speedup | Rows |
|-------|----------|--------|---------|------|
| COUNT(*) all triples | 454.7 ms | 4-7 ms | **65-114x** | 1 |
| DISTINCT types | 266.5 ms | 5 ms | **53x** | 33 |
| COUNT per type (GROUP BY) | 64.4 ms | 4-5 ms | **13x** | 33 |
| DISTINCT predicates | 1,920.0 ms | 8 ms | **240x** | 151 |
| Top 20 subjects by triple count | 522.3 ms | 25 ms | **21x** | 20 |
| Find ConnectivityNodes | 21.3 ms | 1 ms | **21x** | 0 |
| JOIN Terminal→Equipment | 56.1 ms | 5-6 ms | **10x** | 0 |
| VoltageLevel + OPTIONAL joins | 60.1-111.5 ms | 26-32 ms | **2-4x** | 5,577 |
| ACLineSegment by BaseVoltage | 25.5 ms | 3-6 ms | **4x** | 1 |
| 3-hop join (Sub→Bay→VL) | 3.8 ms | 3 ms | **1.3x** | 0 |
| **Total (10 queries)** | **2,937 ms** | **94 ms** | **31x** | |

## Observations

- qlever advantage is largest on aggregation and scan-heavy queries (COUNT, DISTINCT) — up to 240x faster
- On complex multi-hop JOINs with small result sets, performance converges (1.3-4x)
- qlever index build creates on-disk permutation indices (~optimized for any query pattern)
- oxigraph loading is slow partly due to Python/rdflib layer overhead (row-by-row rdflib API)
- Both engines produced identical results on all queries that returned data

## Architecture Decision

- **Default (pip install):** oxigraph via oxrdflib — works everywhere, no build deps
- **Fast engine (build from source):** libqlever via Cython wrapper — 14-240x faster, requires C++ build toolchain (boost, icu, openssl, zstd, jemalloc)
- Same dual-engine pattern as RDF parser: lxml (default) vs pugixml+Cython (fast, 12.9x)

## Build Dependencies for qlever

System packages: `boost` (iostreams, program_options, url, container), `icu`, `openssl`, `zstd`, `jemalloc`
CMake FetchContent (automatic): abseil, antlr4, nlohmann-json, re2, s2geometry, ctre, fsst, range-v3

## Reproduction

```bash
# Convert CGMES data to N-Quads
python triplets_to_nquads.py test_data/.../EQ_V2.xml /tmp/cgmes_eq.nq

# Build qlever
cd ~/GIT/qlever && mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$(nproc) qlever

# Run benchmark
python benchmark_sparql_engines.py
python test_fair_benchmark.py
```
