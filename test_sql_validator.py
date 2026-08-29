"""Checks for sql_validator.assert_select_only."""

from sql_validator import assert_select_only


def _rejects(sql: str) -> None:
    try:
        assert_select_only(sql)
    except ValueError:
        return
    raise AssertionError(f"expected reject: {sql!r}")


def test_allows_select_and_cte() -> None:
    assert_select_only("SELECT 1")
    assert_select_only("  select a, b from silver.customers where a > 1 limit 10")
    assert_select_only("WITH x AS (SELECT 1 AS n) SELECT * FROM x JOIN y ON x.n = y.n")
    assert_select_only("SELECT * FROM t /* INSERT */ WHERE 1=1")
    assert_select_only("SELECT * FROM t ORDER BY a DESC LIMIT 5")


def test_rejects_writes_and_batches() -> None:
    _rejects("")
    _rejects("INSERT INTO t VALUES (1)")
    _rejects("UPDATE t SET a = 1")
    _rejects("DELETE FROM t")
    _rejects("DROP TABLE t")
    _rejects("CREATE TABLE t AS SELECT 1")
    _rejects("SELECT 1; SELECT 2")
    _rejects("SELECT * INTO u FROM t")
    _rejects("COPY t TO 'out.parquet'")
    _rejects("ATTACH 'ws/lh.Lakehouse' AS lh (TYPE ONELAKE)")
    _rejects("SELECT 1; DROP TABLE t")
    _rejects("EXPLAIN SELECT 1")


if __name__ == "__main__":
    test_allows_select_and_cte()
    test_rejects_writes_and_batches()
    print("test_sql_validator ok")
