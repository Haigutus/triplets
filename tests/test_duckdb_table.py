"""Per-connection DuckDB table/schema defaults (tools.duckdb_engine)."""
import pandas
import pytest

duckdb = pytest.importorskip("duckdb")
import triplets  # noqa: E402, F401 — wraps connect + registers tools
from triplets.tools.duckdb_engine import _get_table, _quote, _resolve_table, _sql_name


def _frame():
    return pandas.DataFrame({
        "ID": ["a", "a"],
        "KEY": ["Type", "IdentifiedObject.name"],
        "VALUE": ["Breaker", "b1"],
        "INSTANCE_ID": ["i1", "i1"],
    })


def test_quote_and_sql_name():
    assert _quote('a"b') == '"a""b"'
    assert _sql_name(None, "triplets") == '"triplets"'
    assert _sql_name("cim", "grid") == '"cim"."grid"'


def test_connect_kwargs_set_defaults():
    con = duckdb.connect(table="grid", schema="cim")
    assert _get_table(con) == ("cim", "grid")
    assert _resolve_table(con) == '"cim"."grid"'


def test_default_connect():
    con = duckdb.connect()
    assert _get_table(con) == (None, "triplets")
    assert _resolve_table(con) == '"triplets"'


def test_set_triplets_table_and_tools():
    con = duckdb.connect()
    con.register("_src", _frame())
    con.execute('CREATE SCHEMA IF NOT EXISTS "my schema"')
    con.execute('CREATE TABLE "my schema"."my-table" AS SELECT * FROM _src')
    con.set_triplets_table(table="my-table", schema="my schema")

    types = con.types_dict()
    assert types == {"Breaker": 1}

    # call override: bare table keeps connection schema; schema="main" for default
    con.register("_other", _frame().assign(VALUE=["BusbarSection", "x"]))
    con.execute('CREATE TABLE "my schema"."other" AS SELECT * FROM _other')
    assert con.types_dict(table="other") == {"BusbarSection": 1}
    con.execute('CREATE TABLE "plain" AS SELECT * FROM _other')
    assert con.types_dict(table="plain", schema="main") == {"BusbarSection": 1}
    assert _get_table(con) == ("my schema", "my-table")


def test_resolve_call_kwargs_win():
    con = duckdb.connect(table="grid", schema="cim")
    assert _resolve_table(con, table="x") == '"cim"."x"'
    assert _resolve_table(con, table="x", schema="s") == '"s"."x"'
    assert _resolve_table(con, table_name="legacy") == '"cim"."legacy"'


def test_filter_triplets_by_type_quoted_value():
    """A type name containing a quote is escaped, not interpolated raw."""
    con = duckdb.connect()
    con.register("_src", _frame().assign(VALUE=["O'Brien", "b1"]))
    con.execute("CREATE TABLE triplets AS SELECT * FROM _src")
    assert con.filter_triplets_by_type("O'Brien").df()["ID"].tolist() == ["a", "a"]
    assert con.filter_triplets_by_type("No'Such").df().empty


def test_read_rdf_append_and_replace():
    """append=True adds rows to the existing table; the default replaces."""
    con = duckdb.connect()
    n1 = con.read_rdf("tests/data/minimal_cim.xml")
    n2 = con.read_rdf("tests/data/minimal_cim.xml", append=True)
    assert n1 == n2 > 0
    assert con.execute("SELECT COUNT(*) FROM triplets").fetchone()[0] == n1 + n2
    assert con.execute("SELECT COUNT(DISTINCT INSTANCE_ID) FROM triplets").fetchone()[0] == 2

    n3 = con.read_rdf("tests/data/minimal_cim.xml")   # default: replace
    assert con.execute("SELECT COUNT(*) FROM triplets").fetchone()[0] == n3


def test_read_rdf_append_creates_missing_table():
    con = duckdb.connect()
    rows = con.read_rdf("tests/data/minimal_cim.xml", append=True)
    assert con.execute("SELECT COUNT(*) FROM triplets").fetchone()[0] == rows > 0


def test_read_rdf_empty_input_creates_standard_table():
    con = duckdb.connect()
    assert con.read_rdf([]) == 0
    columns = [row[0] for row in con.execute("DESCRIBE triplets").fetchall()]
    assert columns == ["ID", "KEY", "VALUE", "INSTANCE_ID"]


# ── config persistence across reopen (file-backed DBs) ───────────────────────

def test_config_survives_reopen(tmp_path):
    db = str(tmp_path / "g.duckdb")
    con = duckdb.connect(db, table="grid", schema="cim")
    con.read_rdf("tests/data/minimal_cim.xml")
    con.close()

    reopened = duckdb.connect(db)          # no kwargs — config comes from the DB
    assert _get_table(reopened) == ("cim", "grid")
    assert "Substation" in reopened.types_dict()
    cursor = reopened.cursor()             # cursors share the database → same config
    assert _get_table(cursor) == ("cim", "grid")
    reopened.close()


def test_explicit_reopen_kwargs_override_and_repersist(tmp_path):
    db = str(tmp_path / "g.duckdb")
    duckdb.connect(db, table="grid", schema="cim").close()
    duckdb.connect(db, table="grid2").close()          # override re-persists
    reopened = duckdb.connect(db)
    assert _get_table(reopened) == (None, "grid2")
    reopened.close()


def test_read_only_connection_resolves_stored_config(tmp_path):
    db = str(tmp_path / "g.duckdb")
    duckdb.connect(db, table="grid", schema="cim").close()
    con = duckdb.connect(db, read_only=True)
    assert _get_table(con) == ("cim", "grid")
    con.set_triplets_table(table="other")   # in-process only — must not write
    assert _get_table(con) == (None, "other")
    con.close()
    fresh = duckdb.connect(db)
    assert _get_table(fresh) == ("cim", "grid")   # DB config untouched
    fresh.close()


def test_bare_connect_writes_no_config():
    con = duckdb.connect()
    count = con.execute("SELECT COUNT(*) FROM duckdb_tables() "
                        "WHERE table_name = '_triplets_config'").fetchone()[0]
    assert count == 0


# ── mutators are in-place DML: extra user columns survive ────────────────────

def test_mutators_preserve_extra_columns():
    con = duckdb.connect()
    con.register("_src", _frame().assign(note=["keep-a", "keep-b"]))
    con.execute("CREATE TABLE triplets AS SELECT * FROM _src")
    update = pandas.DataFrame({"ID": ["a", "new"], "KEY": ["IdentifiedObject.name", "Type"],
                               "VALUE": ["renamed", "Switch"]})

    con.set_value_at_key("IdentifiedObject.name", "x")
    con.set_value_at_key_and_id("Type", "Disconnector", "a")
    con.update_triplets_from_triplets(update)
    con.remove_triplets_from_triplets(pandas.DataFrame(
        {"ID": ["a"], "KEY": ["Type"], "VALUE": ["Disconnector"]}))

    rows = con.execute("SELECT ID, note FROM triplets ORDER BY ID").fetchall()
    assert ("a", "keep-b") in rows                  # extra column survived every mutator
    assert ("new", None) in rows                    # inserted rows get NULL extras
    assert con.execute("SELECT COUNT(*) FROM triplets").fetchone()[0] == 2
    values = con.execute("SELECT VALUE FROM triplets WHERE ID = 'a'").fetchall()
    assert values == [("renamed",)]                 # update applied on the surviving row


# ── rowid robustness: views and registered frames as table= targets ──────────

def _loaded_connection():
    con = duckdb.connect()
    con.register("_src", _frame())
    con.execute("CREATE TABLE triplets AS SELECT * FROM _src")
    return con


def test_tools_work_on_view_target():
    con = _loaded_connection()
    con.execute("CREATE VIEW v AS SELECT * FROM triplets")
    tv = con.type_tableview("Breaker", table="v", view_name="tv_v")
    assert tv.df()["ID"].tolist() == ["a"]
    refs = con.references_to("a", table="v")
    assert refs.df().shape[0] >= 0            # no BinderException on rowid
    assert con.content_hash(table="v") == con.content_hash()   # order-invariant matches


def test_tools_work_on_registered_frame_target():
    con = duckdb.connect()
    con.register("reg", _frame())
    tv = con.type_tableview("Breaker", table="reg", view_name="tv_reg")
    assert tv.df()["ID"].tolist() == ["a"]


def test_order_sensitive_hash_requires_base_table():
    con = _loaded_connection()
    con.execute("CREATE VIEW v AS SELECT * FROM triplets")
    assert con.content_hash(order_sensitive=True)              # base table fine
    with pytest.raises(ValueError, match="requires a base table"):
        con.content_hash(order_sensitive=True, table="v")


def test_views_created_in_configured_schema():
    con = duckdb.connect(table="grid", schema="cim")
    con.execute("CREATE SCHEMA cim")
    con.register("_src", _frame())
    con.execute("CREATE TABLE cim.grid AS SELECT * FROM _src")
    con.type_tableview("Breaker")
    row = con.execute("SELECT schema_name FROM duckdb_views() "
                      "WHERE view_name = 'Breaker'").fetchone()
    assert row == ("cim",)



def test_connection_exports_ride_native_arrow():
    """con.export_to_* fetches through duckdb's native arrow result path and
    produces the same bytes as exporting the equivalent frame."""
    con = duckdb.connect()
    con.register("_src", _frame())
    con.execute("CREATE TABLE triplets AS SELECT * FROM _src")
    from_connection = con.export_to_nquads(export_to_memory=True).getvalue()
    import triplets as t
    from_frame = t.export.export_to_nquads(_frame(), export_to_memory=True).getvalue()
    assert sorted(from_connection.splitlines()) == sorted(from_frame.splitlines())



def test_connection_nquads_streams_to_disk(tmp_path):
    """con.export_to_nquads streams batch-by-batch to the file — content equals
    the whole-frame export (line order may differ; N-Quads lines are independent)."""
    import triplets as t
    con = duckdb.connect()
    con.register("_src", _frame())
    con.execute("CREATE TABLE triplets AS SELECT * FROM _src")
    out = tmp_path / "x.nq"
    con.export_to_nquads(str(out))
    from_frame = t.export.export_to_nquads(_frame(), export_to_memory=True).getvalue()
    assert sorted(out.read_bytes().splitlines()) == sorted(from_frame.splitlines())


def test_reader_input_requires_polars_engine():
    import pyarrow
    import triplets as t
    table = pyarrow.Table.from_pandas(_frame(), preserve_index=False)
    with pytest.raises(ValueError, match="requires.*polars engine"):
        t.export.export_to_nquads(table.to_reader(), engine="pandas", export_to_memory=True)


def test_connection_nquads_explicit_pandas_engine():
    """engine="pandas" skips the streaming reader (polars-only) and exports
    through the whole-table path — same content, no ValueError."""
    pytest.importorskip("polars")
    import triplets as t
    con = duckdb.connect()
    con.register("_src", _frame())
    con.execute("CREATE TABLE triplets AS SELECT * FROM _src")
    from_connection = con.export_to_nquads(engine="pandas", export_to_memory=True).getvalue()
    from_frame = t.export.export_to_nquads(_frame(), engine="pandas", export_to_memory=True).getvalue()
    assert sorted(from_connection.splitlines()) == sorted(from_frame.splitlines())
