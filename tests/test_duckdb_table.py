"""Per-connection DuckDB table/schema defaults (tools.duckdb_table)."""
import pandas
import pytest

duckdb = pytest.importorskip("duckdb")
import triplets  # noqa: E402, F401 — wraps connect + registers tools
from triplets.tools.duckdb_table import get, quote, resolve, set, sql_name


def _frame():
    return pandas.DataFrame({
        "ID": ["a", "a"],
        "KEY": ["Type", "IdentifiedObject.name"],
        "VALUE": ["Breaker", "b1"],
        "INSTANCE_ID": ["i1", "i1"],
    })


def test_quote_and_sql_name():
    assert quote('a"b') == '"a""b"'
    assert sql_name(None, "triplets") == '"triplets"'
    assert sql_name("cim", "grid") == '"cim"."grid"'


def test_connect_kwargs_set_defaults():
    con = duckdb.connect(table="grid", schema="cim")
    assert get(con) == ("cim", "grid")
    assert resolve(con) == '"cim"."grid"'


def test_default_connect():
    con = duckdb.connect()
    assert get(con) == (None, "triplets")
    assert resolve(con) == '"triplets"'


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
    assert get(con) == ("my schema", "my-table")


def test_resolve_call_kwargs_win():
    con = duckdb.connect(table="grid", schema="cim")
    assert resolve(con, table="x") == '"cim"."x"'
    assert resolve(con, table="x", schema="s") == '"s"."x"'
    assert resolve(con, table_name="legacy") == '"cim"."legacy"'
