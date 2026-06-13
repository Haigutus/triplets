# Feeding rdflib a million CGMES triples: N-Quads vs CIM XML, typed vs untyped

We keep CGMES grid models in [triplets](https://github.com/Haigutus/triplets)
DataFrames, but sometimes the data has to go where DataFrames can't — into an
RDF stack: rdflib for SPARQL, pyshacl for reference SHACL validation. That
raises a mundane but consequential question: **which serialization gets a
million triples into rdflib fastest?**

triplets 0.1.0rc5 can export the same dataset three ways:

```python
data.export_to_cimxml(rdf_map=schema)                  # CIM RDF/XML, plain literals
data.export_to_cimxml(rdf_map=schema, datatypes=True)  # + rdf:datatype on literals
data.export_to_nquads("grid.nq", rdf_map=schema)       # N-Quads, typed literals
```

## The measurement

RealGrid test configuration (CGMES 2.4.15, 552 ED2 schema): 1,146,215 triplet
rows → 1,080,314 RDF triples. Each load runs in a **fresh Python process**
(in-process repetition skews results by up to 20% through allocator and GC
pressure — measure cold or don't bother). rdflib 7.x, CPython 3.13.

| Serialization | Export | rdflib load | Export + load |
|---|---|---|---|
| **N-Quads (typed)** | 3.0 s | **18.1 s** | **21.1 s** |
| CIM XML, untyped | 0.9 s | 20.5 s | 21.4 s |
| original CGMES files | — | 21.1 s | — |
| CIM XML, typed | 7.9 s | 23.5 s | 31.4 s |

## Finding 1 — N-Quads wins the load

~12% faster than untyped RDF/XML, despite the .nq file being the largest on
disk (209 MB). The N-Quads grammar is one triple per line with absolute IRIs:
no namespace resolution, no element stack, no SAX event machinery — rdflib's
parser is essentially a line splitter. The XML parsers carry the whole
RDF/XML abbreviation model with them.

## Finding 2 — typed literals cost you at parse time

`datatypes=True` XML loads ~15% *slower* than plain XML. The reason is in
rdflib's `Literal`: when a literal carries `rdf:datatype="…xsd#float"`,
rdflib eagerly converts the lexical form to a Python `float` during parsing.
A plain literal skips conversion entirely. Several hundred thousand `float()`
calls are the difference.

That's not an argument against typed exports — it's **pay now or pay later**.
An untyped graph compares `"400"` as a string forever: `FILTER(?v > 100)`
silently misbehaves, and every consumer re-parses values ad hoc. The typed
graph paid three seconds once and answers numeric SPARQL correctly. (Our
N-Quads export is *also* typed — and still the fastest load, because the
parser is that much cheaper.)

## Finding 3 — the export side seals it

The untyped XML export is fast (0.9 s) because it runs through the compiled
Arrow→pugixml engine. The typed export currently falls back to the lxml
engine (7.9 s) — typed-attribute support hasn't reached the compiled engine
yet. N-Quads exports in 3.0 s. End to end, the N-Quads pipeline is the
clear choice for feeding rdflib **and** it preserves datatypes.

## Takeaways

- Feeding an RDF stack? **Export N-Quads.** Fastest load, typed literals
  included, and named graphs preserve the CGMES instance structure.
- Need CIM XML with datatypes for another consumer? `datatypes=True` exists —
  budget ~15% extra rdflib load time, which buys correct typed semantics.
- Benchmark loads in fresh processes; in-process ordering effects are larger
  than some of the differences you're measuring.

Reproduce: `pytest tests/test_benchmarks_realgrid.py -k rdflib_load --benchmark-only`
