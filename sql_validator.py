"""Reject anything that is not a single SELECT / WITH…SELECT statement."""

from __future__ import annotations

import re

_BLOCKED = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "CREATE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "EXEC",
        "EXECUTE",
        "CALL",
        "COPY",
        "INSTALL",
        "LOAD",
        "ATTACH",
        "DETACH",
        "EXPORT",
        "IMPORT",
        "PRAGMA",
        "VACUUM",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "PREPARE",
        "DEALLOCATE",
        "UPSERT",
        "REFRESH",
        "MSCK",
        "CACHE",
        "UNCACHE",
        "USE",
        "RESET",
    }
)


def _strip_comments_and_strings(sql: str) -> str:
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        if sql.startswith("--", i):
            i = sql.find("\n", i)
            if i == -1:
                break
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j == -1:
                break
            i = j + 2
            continue
        if sql[i] == "'":
            i += 1
            while i < n:
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue
        out.append(sql[i])
        i += 1
    return "".join(out)


def assert_select_only(sql: str) -> None:
    """Raise ValueError unless sql is one SELECT or WITH…SELECT statement."""
    if not sql or not sql.strip():
        raise ValueError("Query is empty")
    body = _strip_comments_and_strings(sql).strip()
    if not body:
        raise ValueError("Query is empty")
    parts = [p.strip() for p in body.split(";") if p.strip()]
    if len(parts) != 1:
        raise ValueError("Only a single SELECT statement is allowed")
    stmt = parts[0]
    match = re.match(r"\(?\s*([A-Za-z_]+)", stmt)
    if not match:
        raise ValueError("Could not parse SQL")
    keyword = match.group(1).upper()
    if keyword not in ("SELECT", "WITH"):
        raise ValueError(f"Only SELECT queries are allowed, got {keyword}")
    if keyword == "WITH" and not re.search(r"\bSELECT\b", stmt, re.I):
        raise ValueError("WITH query must contain SELECT")
    if re.search(r"\bSELECT\b[\s\S]*\bINTO\b", stmt, re.I):
        raise ValueError("SELECT INTO is not allowed")
    for token in re.findall(r"[A-Za-z_]+", stmt):
        upper = token.upper()
        if upper in _BLOCKED:
            raise ValueError(f"Keyword {upper} is not allowed")


if __name__ == "__main__":
    assert_select_only("SELECT * FROM t")
    assert_select_only("WITH x AS (SELECT 1 AS a) SELECT * FROM x")
    assert_select_only("SELECT * FROM t -- INSERT INTO u")
    for bad in (
        "INSERT INTO t VALUES (1)",
        "SELECT 1; DROP TABLE t",
        "SELECT * INTO u FROM t",
        "COPY t TO 'x'",
        "ATTACH 'x' AS y",
    ):
        try:
            assert_select_only(bad)
        except ValueError:
            continue
        raise SystemExit(f"should have rejected: {bad}")
    print("sql_validator ok")
