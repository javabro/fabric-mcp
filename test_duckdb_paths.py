"""Unit tests for OneLake path helpers (no Fabric / DuckDB network)."""

from fabric_duckdb_client import (
    abfss_table_path,
    entry_name,
    has_delta_log,
    is_dfs_directory,
    referenced_tables,
)


def test_entry_name_strips_directory_prefix() -> None:
    directory = "lh-guid/Tables"
    assert entry_name("lh-guid/Tables/customers", directory) == "customers"
    assert entry_name("lh-guid/Tables/dbo/", directory) == "dbo"
    assert entry_name("customers", directory) == "customers"


def test_is_dfs_directory() -> None:
    assert is_dfs_directory({"isDirectory": "true"})
    assert is_dfs_directory({"isDirectory": True})
    assert not is_dfs_directory({"isDirectory": "false"})
    assert not is_dfs_directory({"name": "part-000.parquet"})


def test_abfss_uses_guids() -> None:
    path = abfss_table_path("ws", "lh", "silver", "customers")
    assert path == (
        "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Tables/silver/customers"
    )
    assert abfss_table_path("ws", "lh", "events") == (
        "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Tables/events"
    )


def test_has_delta_log_not_confused_with_table_names() -> None:
    directory = "lh/Tables/events"
    entries = [
        {"name": "lh/Tables/events/_delta_log", "isDirectory": "true"},
        {"name": "lh/Tables/events/part-000.snappy.parquet", "isDirectory": "false"},
    ]
    assert has_delta_log(entries, directory)
    assert not has_delta_log(
        [{"name": "lh/Tables/bronze", "isDirectory": "true"}], "lh/Tables"
    )


def test_referenced_tables_from_join() -> None:
    refs = referenced_tables(
        'SELECT COUNT(*) AS n FROM "dbo"."zfa_glossary" g '
        'JOIN dbo.zfa_texts t ON g.FIELDNAME = t.FIELDNAME',
        default_schema="dbo",
    )
    assert refs == [("dbo", "zfa_glossary"), ("dbo", "zfa_texts")]
    assert referenced_tables("SELECT * FROM zfa_glossary") == [("dbo", "zfa_glossary")]
    assert referenced_tables("SELECT * FROM (SELECT 1) s") == []


if __name__ == "__main__":
    test_entry_name_strips_directory_prefix()
    test_is_dfs_directory()
    test_abfss_uses_guids()
    test_has_delta_log_not_confused_with_table_names()
    test_referenced_tables_from_join()
    print("test_duckdb_paths ok")
