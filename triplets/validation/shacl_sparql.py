"""sh:sparql constraints and SPARQL targets — shared by every vectorized SHACL engine.

Queries run through triplets.sparql (auto engine — qlever when built, else
oxigraph, else rdflib) on the engine's own data: the engine loads it once
(content-hash cached), each constraint runs as one SELECT with the focus nodes
bound via VALUES, and ``max_workers`` runs the constraint queries in parallel
processes on the rdflib path only (fork — copy-on-write shares the dataset;
threads don't help GIL-bound rdflib; the embedded engines are ms-scale
sequentially). Results are violations frames in the canonical schema.
"""
import logging
import multiprocessing

from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pandas

from ..export.nquads_utils import make_subject
from ..iri import CIM_NS, iri_pandas, split_iri
from .shacl_report import VIOLATION_COLUMNS

logger = logging.getLogger(__name__)


class SparqlState:
    """Per-validation SPARQL state: the data any engine passes in, one rdflib
    dataset, and the content-hash / engine-error bookkeeping."""

    def __init__(self, data, rdf_map=None, undefined_namespace=CIM_NS):
        self.data = data
        self.rdf_map = rdf_map
        self.undefined_namespace = undefined_namespace
        self._dataset = None
        # The data cannot change between the constraint queries of one
        # validation run: after the first query has hashed it, the rest
        # assert data_unchanged and skip the per-query content_hash.
        self.data_hashed = False
        # Last engine exception — a cached ingest failure re-raises the same
        # object per rule; run() reports it once instead of once per rule.
        self.engine_error = None

    def dataset(self):
        """rdflib dataset for the constraint queries — loaded once, reused per query."""
        if self._dataset is None:
            from .._rdflib_loader import load_dataset
            self._dataset = load_dataset(self.data, rdf_map=self.rdf_map,
                                         undefined_namespace=self.undefined_namespace)
        return self._dataset

    def select(self, query_text, **kwargs):
        """SELECT on the auto engine over the state's data, as a pandas frame."""
        from .. import sparql
        result = sparql.query(self.data, query_text, rdf_map=self.rdf_map,
                              data_unchanged=self.data_hashed,
                              undefined_namespace=self.undefined_namespace,
                              return_type="pandas", **kwargs)
        self.data_hashed = True
        return result


def _empty():
    return pandas.DataFrame(columns=VIOLATION_COLUMNS)


def shorten(terms, rule):
    """SPARQL result terms → triplet form: IRIs decoded, then the column *rule* (``local_id``
    for the focus, ``local_value`` for the value), literals verbatim (a literal ``_name`` or a blank node ``_:b0`` must
    not lose its ``_``)."""
    return rule(iri_pandas.decode_iri(terms)).where(iri_pandas.is_iri(terms), terms)


def target_ids(state, query_text):
    """Focus IDs from a SPARQLTarget SELECT (the ?this column)."""
    try:
        result = state.select(query_text)
    except Exception:  # noqa: BLE001 — defective target query
        logger.warning("sh:target SPARQL SELECT failed — shape has no focus nodes")
        return []
    if result is None or len(result) == 0:
        return []
    column = "this" if "this" in result.columns else result.columns[0]
    return list(shorten(result[column].astype(str), iri_pandas.local_id).unique())


def query_text(rule, focus_ids):
    """Final executable query: prefixes + SELECT with $PATH substituted and the
    focus nodes bound via VALUES ($this is the SPARQL variable ?this)."""
    select = rule.params["select"]
    if rule.params["path"]:
        select = select.replace("$PATH", f"<{rule.params['path']}>")
    values = " ".join(make_subject(focus) for focus in focus_ids)
    closing = select.rfind("}")
    return rule.params["prefixes"] + f"{select[:closing]} VALUES ?this {{ {values} }} {select[closing:]}"


def violations(rule, result):
    """Each SELECT result row is one violation: $this = focus node, ?value = value."""
    if result is None or len(result) == 0 or "this" not in result.columns:
        return _empty()
    result = result[result["this"].notna()]   # a row without a focus node is no violation
    if len(result) == 0:                      # (rdflib serializes a spurious empty binding
        return _empty()                       #  for some aggregate queries)
    focus = shorten(result["this"].astype(str), iri_pandas.local_id)
    values = shorten(result["value"].astype(str), iri_pandas.local_value) if "value" in result.columns else None
    return pandas.DataFrame({
        "ID": list(focus),
        "KEY": rule.path,
        "VALUE": list(values) if values is not None else None,
        "VIOLATION_TYPE": rule.component,
        "MESSAGE": rule.message or "sparql constraint violated",
        "SEVERITY": rule.severity,
        "SOURCE_SHAPE": rule.shape_id,
    }, columns=VIOLATION_COLUMNS)


def run(state, rule, focus_ids):
    """Run one sh:sparql constraint. Queries run exactly as authored — no fixing.

    When a strict engine (qlever, oxigraph) rejects a query, the constraint is
    still evaluated on the lenient rdflib engine so the report stays complete,
    and a ``triplets:invalidSparql`` Warning row flags the defective shape —
    broken rules get reported and fixed upstream, not auto-patched here.
    """
    from .. import sparql
    if not len(focus_ids):
        return _empty()
    # rdflib queries the shared pre-loaded dataset; other engines (qlever,
    # oxigraph) take the raw data and manage their own engine-state cache
    # (one build, content-hashed)
    text = query_text(rule, focus_ids)
    engine_name = sparql.get_engine("auto")[0]
    if engine_name == "rdflib":
        try:
            return violations(rule, sparql.query(state.dataset(), text))
        except Exception as error:                        # noqa: BLE001 — defective authored query
            logger.error("sh:sparql constraint %s fails on rdflib: %s", rule.shape_id, error)
            return _invalid(rule, _not_evaluated(rule, error), severity="Violation")

    try:
        return violations(rule, state.select(text))
    except Exception as error:                            # noqa: BLE001 — engine strictness
        # A cached ingest failure (data vs schema) re-raises the same exception
        # object for every rule — report it once, not once per rule; per-rule
        # query rejections are fresh exceptions and keep their own note.
        repeated = error is state.engine_error
        state.engine_error = error
        if not repeated:
            logger.warning("sh:sparql constraint %s rejected by %s — evaluating with rdflib "
                           "and flagging the shape (fix the rule upstream):\n%s",
                           rule.shape_id, engine_name, error)
        note = _empty() if repeated else _invalid(
            rule, f"{str(error).splitlines()[0]} — the constraint WAS still evaluated "
                  f"via the rdflib fallback; fix the shape/data upstream")
        try:
            found = violations(rule, sparql.query(state.dataset(), text, engine="rdflib"))
        except Exception as rdflib_error:                 # noqa: BLE001 — truly broken query
            logger.error("sh:sparql constraint %s also fails on rdflib: %s",
                         rule.shape_id, rdflib_error)
            return _invalid(rule, _not_evaluated(rule, rdflib_error), severity="Violation")
        return pandas.concat([found, note], ignore_index=True)


def _not_evaluated(rule, error):
    """The message for a constraint no engine could run: name the shape and
    its target/path (anonymous property shapes only have a blank-node id),
    say plainly that nothing was checked — a shapes bug, not a data finding."""
    where = f"{rule.target_class}/{rule.path}" if rule.path else rule.target_class
    return (f"sh:sparql constraint of shape {split_iri(str(rule.shape_id))[1]} ({where}) was "
            f"NOT evaluated — the query is defective on every engine "
            f"(rdflib: {str(error).splitlines()[0]}). "
            f"A shapes bug, not a data finding; this constraint went unchecked.")


def _invalid(rule, message, severity="Warning"):
    """One report row flagging a constraint query an engine rejected — the
    caller words the message (rejected-but-evaluated vs not evaluated at all)."""
    return pandas.DataFrame({
        "ID": [None],
        "KEY": rule.path,
        "VALUE": None,
        "VIOLATION_TYPE": "triplets:invalidSparql",
        "MESSAGE": message,
        "SEVERITY": severity,
        "SOURCE_SHAPE": rule.shape_id,
    }, columns=VIOLATION_COLUMNS)


def dedupe_invalid(frame):
    """A defective shape fans out to one rule per sh:targetClass — flag it once."""
    invalid = frame["VIOLATION_TYPE"] == "triplets:invalidSparql"
    if not invalid.any():
        return frame
    return pandas.concat([frame[~invalid], frame[invalid].drop_duplicates()], ignore_index=True)


# fork inherits this by copy-on-write — the dataset is never pickled per task
_FORK_DATASET = None


def _worker(text):
    from .. import sparql
    return sparql.query(_FORK_DATASET, text)


def run_parallel(state, tasks, max_workers):
    """Run ``[(rule, focus_ids)]`` sh:sparql constraint queries in parallel processes.

    rdflib query evaluation is GIL-bound pure Python, so threads don't help;
    fork gives copy-on-write sharing of the loaded dataset (Linux). One task
    per constraint query. Only applies to the rdflib engine — the embedded
    engines (qlever: C++ state must not be forked; oxigraph: Rust store) are
    orders of magnitude faster and run the queries sequentially. Both release
    the GIL during queries, so threading them is a recorded future
    optimization (TODO.md).
    """
    from .. import sparql
    if sparql.get_engine("auto")[0] != "rdflib":
        logger.debug("sparql auto engine is not rdflib — max_workers ignored (sequential)")
        return [run(state, rule, focus) for rule, focus in tasks]

    global _FORK_DATASET
    queries = [(rule, query_text(rule, focus)) for rule, focus in tasks if len(focus)]
    if not queries:
        return []
    _FORK_DATASET = state.dataset()
    try:
        with ProcessPoolExecutor(max_workers=max_workers,
                                 mp_context=multiprocessing.get_context("fork")) as pool:
            results = list(pool.map(_worker, [text for _, text in queries]))
    except (BrokenProcessPool, OSError) as error:
        # fork from a thread-heavy process (polars/duckdb pools, pytest) can kill
        # workers — degrade to sequential instead of failing the validation
        logger.warning("sh:sparql process pool failed (%s) — running sequentially", error)
        return [run(state, rule, focus) for rule, focus in tasks]
    finally:
        _FORK_DATASET = None
    return [violations(rule, result) for (rule, _), result in zip(queries, results)]
