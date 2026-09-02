"""Query Fabric lakehouse Delta tables with DuckDB (azure + delta_scan)."""

from __future__ import annotations

import asyncio
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import httpx

from fabric_api_client import FabricAPIClient
from fabric_auth import AzureIdentityAuthProvider
from sql_validator import _strip_comments_and_strings

STORAGE_SCOPE = "https://storage.azure.com/.default"
ONELAKE_DFS = "https://onelake.dfs.fabric.microsoft.com"
MAX_RESULT_ROWS = 1000
DFS_VERSION = "2023-11-03"
# Hard cap on total on-disk size of tables a single query may touch. Checked via
# cheap OneLake metadata listing before any actual data is scanned. 0 disables it.
MAX_QUERY_SCAN_BYTES = int(os.environ.get("FABRIC_MAX_QUERY_SCAN_MB", "10")) * 1024 * 1024
_SKIP_DIR_NAMES = frozenset({"_delta_log", "_symlink_format_manifest"})
_SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog"})
_FROM_JOIN = re.compile(r"\b(?:FROM|JOIN)\s+", re.I)
_QUOTED_PAIR = re.compile(r'^"((?:[^"]|"")*)"\s*\.\s*"((?:[^"]|"")*)"')
_PLAIN_PAIR = re.compile(r"^([A-Za-z_][\w]*)\s*\.\s*([A-Za-z_][\w]*)")
_QUOTED_ONE = re.compile(r'^"((?:[^"]|"")*)"')
_PLAIN_ONE = re.compile(r"^([A-Za-z_][\w]*)")


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _jsonish(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (list, tuple)):
        return [_jsonish(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonish(v) for k, v in value.items()}
    return str(value)


def is_dfs_directory(entry: Dict[str, Any]) -> bool:
    raw = entry.get("isDirectory")
    if isinstance(raw, bool):
        return raw
    return str(raw or "").lower() in ("true", "1")


def entry_name(path_name: str, directory: str) -> str:
    name = (path_name or "").replace("\\", "/").rstrip("/")
    prefix = directory.replace("\\", "/").rstrip("/") + "/"
    if name.startswith(prefix):
        name = name[len(prefix) :]
    elif "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def has_delta_log(entries: List[Dict[str, Any]], directory: str) -> bool:
    for entry in entries:
        if not is_dfs_directory(entry):
            continue
        if entry_name(entry.get("name") or "", directory) == "_delta_log":
            return True
    return False


def abfss_table_path(workspace_id: str, lakehouse_id: str, *parts: str) -> str:
    rel = "/".join(p.strip("/") for p in parts if p)
    return (
        f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/"
        f"{lakehouse_id}/Tables/{rel}"
    )


def parse_table_ref(rest: str) -> Tuple[Optional[str], Optional[str]]:
    rest = rest.lstrip()
    match = _QUOTED_PAIR.match(rest)
    if match:
        return match.group(1).replace('""', '"'), match.group(2).replace('""', '"')
    match = _PLAIN_PAIR.match(rest)
    if match:
        return match.group(1), match.group(2)
    match = _QUOTED_ONE.match(rest)
    if match:
        return None, match.group(1).replace('""', '"')
    match = _PLAIN_ONE.match(rest)
    if match:
        return None, match.group(1)
    return None, None


def referenced_tables(sql: str, default_schema: str = "dbo") -> List[Tuple[str, str]]:
    """FROM/JOIN table refs in a SELECT. Subqueries starting with '(' are skipped."""
    body = _strip_comments_and_strings(sql)
    found: List[Tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _FROM_JOIN.finditer(body):
        rest = body[match.end() :].lstrip()
        if rest.startswith("("):
            continue
        schema, table = parse_table_ref(rest)
        if not table:
            continue
        key = (schema or default_schema, table)
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def parse_qualified_ident(sql: str) -> Tuple[Optional[str], Optional[str]]:
    return parse_table_ref(sql.strip())


class FabricDuckDBClient:
    def __init__(self, auth_provider: AzureIdentityAuthProvider, api_client: FabricAPIClient) -> None:
        self.auth_provider = auth_provider
        self.api_client = api_client
        self._lock = threading.Lock()
        self._con: Optional[duckdb.DuckDBPyConnection] = None
        self._catalog_key: Optional[tuple[str, str]] = None
        self._default_schema = "dbo"
        self._tables: List[Tuple[str, str, str]] = []
        self._registered: List[Tuple[str, str]] = []
        self._table_size_cache: Dict[Tuple[str, str, str, str], int] = {}

    def _storage_token(self) -> str:
        return self.auth_provider.get_access_token(STORAGE_SCOPE)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._con is not None:
            return self._con
        con = duckdb.connect()
        con.execute("INSTALL azure")
        con.execute("LOAD azure")
        con.execute("INSTALL delta")
        con.execute("LOAD delta")
        con.execute("SET azure_transport_option_type = 'curl'")
        self._con = con
        return con

    def _refresh_azure_secret(self, con: duckdb.DuckDBPyConnection) -> None:
        token = self._storage_token()
        con.execute(
            f"""
            CREATE OR REPLACE SECRET onelake (
                TYPE AZURE,
                PROVIDER ACCESS_TOKEN,
                ACCESS_TOKEN {_sql_quote(token)},
                ACCOUNT_NAME 'onelake'
            )
            """
        )

    async def _dfs_list(
        self, workspace_id: str, directory: str, *, recursive: bool = False
    ) -> List[Dict[str, Any]]:
        token = self._storage_token()
        url = f"{ONELAKE_DFS}/{workspace_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "x-ms-version": DFS_VERSION,
            "Accept": "application/json",
        }
        entries: List[Dict[str, Any]] = []
        continuation: Optional[str] = None
        async with httpx.AsyncClient(timeout=60.0) as client:
            while True:
                params: Dict[str, str] = {
                    "resource": "filesystem",
                    "recursive": "true" if recursive else "false",
                    "directory": directory,
                }
                if continuation:
                    params["continuation"] = continuation
                response = await client.get(url, headers=headers, params=params)
                if response.status_code == 404:
                    return []
                if response.status_code != 200:
                    body = (response.text or "")[:500]
                    hint = ""
                    if response.status_code == 403:
                        hint = (
                            " Confirm `az login` is the Fabric tenant, this identity "
                            "has Workspace Viewer (or higher), and OneLake external "
                            "access is allowed."
                        )
                    raise Exception(
                        f"OneLake list failed {response.status_code}:{hint} {body}"
                    )
                try:
                    payload = response.json()
                except Exception as exc:
                    raise Exception(
                        f"OneLake list did not return JSON: {response.text[:500]}"
                    ) from exc
                entries.extend(payload.get("paths") or [])
                continuation = response.headers.get("x-ms-continuation") or payload.get(
                    "continuationToken"
                )
                if not continuation:
                    break
        return entries

    def _dir_names(self, entries: List[Dict[str, Any]], directory: str) -> List[str]:
        names: List[str] = []
        seen: set[str] = set()
        for entry in entries:
            if not is_dfs_directory(entry):
                continue
            name = entry_name(entry.get("name") or "", directory)
            if not name or name in _SKIP_DIR_NAMES or name.startswith(".") or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    def _has_delta_log(self, entries: List[Dict[str, Any]], directory: str) -> bool:
        return has_delta_log(entries, directory)

    async def _discover_tables(
        self, workspace_id: str, lakehouse_id: str, default_schema: str
    ) -> List[Tuple[str, str, str]]:
        """Name tables from DFS. Do not delta_scan. Do not list each table's files."""
        tables_dir = f"{lakehouse_id}/Tables"
        children = self._dir_names(
            await self._dfs_list(workspace_id, tables_dir), tables_dir
        )
        found: List[Tuple[str, str, str]] = []
        for name in children:
            child_dir = f"{tables_dir}/{name}"
            nested = await self._dfs_list(workspace_id, child_dir)
            if self._has_delta_log(nested, child_dir):
                found.append(
                    (
                        default_schema,
                        name,
                        abfss_table_path(workspace_id, lakehouse_id, name),
                    )
                )
                continue
            for table in self._dir_names(nested, child_dir):
                found.append(
                    (
                        name,
                        table,
                        abfss_table_path(workspace_id, lakehouse_id, name, table),
                    )
                )
        return found

    def _drop_registered(self, con: duckdb.DuckDBPyConnection) -> None:
        for schema, table in self._registered:
            con.execute(f"DROP VIEW IF EXISTS {_ident(schema)}.{_ident(table)}")
        for schema in {s for s, _t in self._registered} - {"main"} - _SYSTEM_SCHEMAS:
            con.execute(f"DROP SCHEMA IF EXISTS {_ident(schema)} CASCADE")
        self._registered = []

    def _sync_catalog(
        self,
        key: tuple[str, str],
        tables: List[Tuple[str, str, str]],
        default_schema: str,
    ) -> None:
        with self._lock:
            if self._con is not None:
                self._drop_registered(self._connect())
            else:
                self._registered = []
            self._tables = tables
            self._default_schema = default_schema
            self._catalog_key = key

    def _register_needed(self, wanted: List[Tuple[str, str]]) -> None:
        con = self._connect()
        self._refresh_azure_secret(con)
        have = set(self._registered)
        by_key = {(schema, name): path for schema, name, path in self._tables}
        for schema, table in wanted:
            if (schema, table) in have:
                continue
            path = by_key.get((schema, table))
            if path is None:
                continue
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {_ident(schema)}")
            con.execute(
                f"CREATE OR REPLACE VIEW {_ident(schema)}.{_ident(table)} AS "
                f"SELECT * FROM delta_scan({_sql_quote(path)})"
            )
            self._registered.append((schema, table))
            have.add((schema, table))

    def _tables_for_sql(self, sql: str) -> List[Tuple[str, str]]:
        refs = referenced_tables(sql, self._default_schema)
        body = _strip_comments_and_strings(sql)
        catalog = self._snapshot_tables()
        wanted: List[Tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for schema, name, _path in catalog:
            key = (schema, name)
            quoted = f"{_ident(schema)}.{_ident(name)}"
            dotted = f"{schema}.{name}"
            if key in refs or quoted in body or dotted in body:
                if key not in seen:
                    seen.add(key)
                    wanted.append(key)
        for key in refs:
            if key not in seen:
                wanted.append(key)
        return wanted

    def _is_catalog(self, key: tuple[str, str]) -> bool:
        with self._lock:
            return self._catalog_key == key

    async def _ensure_catalog(
        self, workspace_id: str, lakehouse_id: str, *, force: bool = False
    ) -> None:
        key = (workspace_id, lakehouse_id)
        if not force and self._is_catalog(key):
            return
        lakehouse = await self.api_client.get(
            f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}"
        )
        default_schema = (lakehouse.get("properties") or {}).get("defaultSchema") or "dbo"
        tables = await self._discover_tables(workspace_id, lakehouse_id, default_schema)
        await asyncio.to_thread(self._sync_catalog, key, tables, default_schema)

    def _fetch(
        self, sql: str, max_rows: Optional[int]
    ) -> tuple[List[Dict[str, Any]], bool]:
        con = self._connect()
        self._refresh_azure_secret(con)
        result = con.execute(sql)
        description = result.description
        if not description:
            return [], False
        columns = [col[0] for col in description]
        if max_rows is None:
            rows = result.fetchall()
            truncated = False
        else:
            rows = result.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
        records = [dict(zip(columns, (_jsonish(v) for v in row))) for row in rows]
        return records, truncated

    def _run_with_views(
        self,
        wanted: List[Tuple[str, str]],
        sql: str,
        max_rows: Optional[int],
    ) -> tuple[List[Dict[str, Any]], bool]:
        with self._lock:
            self._register_needed(wanted)
            return self._fetch(sql, max_rows)

    def _snapshot_tables(self) -> List[Tuple[str, str, str]]:
        with self._lock:
            return list(self._tables)

    async def _table_size_bytes(
        self, workspace_id: str, lakehouse_id: str, schema: str, table: str
    ) -> int:
        key = (workspace_id, lakehouse_id, schema, table)
        cached = self._table_size_cache.get(key)
        if cached is not None:
            return cached
        directory = f"{lakehouse_id}/Tables/{schema}/{table}"
        entries = await self._dfs_list(workspace_id, directory, recursive=True)
        total = sum(
            int(entry.get("contentLength") or 0)
            for entry in entries
            if not is_dfs_directory(entry)
        )
        self._table_size_cache[key] = total
        return total

    async def _enforce_scan_cap(
        self,
        workspace_id: str,
        lakehouse_id: str,
        wanted: List[Tuple[str, str]],
        confirm_large_scan: bool = False,
    ) -> None:
        if MAX_QUERY_SCAN_BYTES <= 0 or not wanted or confirm_large_scan:
            return
        sizes: List[Tuple[str, str, int]] = []
        total = 0
        for schema, table in wanted:
            size = await self._table_size_bytes(workspace_id, lakehouse_id, schema, table)
            sizes.append((schema, table, size))
            total += size
        if total <= MAX_QUERY_SCAN_BYTES:
            return
        cap_mb = MAX_QUERY_SCAN_BYTES / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        table_lines = "\n".join(
            f"  - {schema}.{table}: {size / (1024 * 1024):.2f} MB"
            for schema, table, size in sizes
        )
        raise Exception(
            "Query blocked by the data-volume guard: this would scan more data than the "
            "configured cap allows.\n"
            f"Total referenced table size: {total_mb:.2f} MB, cap: {cap_mb:.2f} MB "
            "(set FABRIC_MAX_QUERY_SCAN_MB to change the cap).\n"
            f"Referenced tables:\n{table_lines}\n"
            "Narrow the query with a WHERE filter, or use Fabric's SQL endpoint or a notebook "
            "for large scans instead.\n"
            "HIGH RISK OVERRIDE: confirm_large_scan=true bypasses this guard and must only be "
            "set after a human user has explicitly reviewed and approved this exact size/table "
            "breakdown in the conversation. Never set it on the calling agent's own judgement."
        )

    async def execute_query(
        self,
        workspace_id: str,
        lakehouse_id: str,
        query: str,
        max_rows: Optional[int] = MAX_RESULT_ROWS,
        confirm_large_scan: bool = False,
    ) -> List[Dict[str, Any]]:
        await self._ensure_catalog(workspace_id, lakehouse_id)
        wanted = self._tables_for_sql(query)
        await self._enforce_scan_cap(workspace_id, lakehouse_id, wanted, confirm_large_scan)
        rows, _truncated = await asyncio.to_thread(
            self._run_with_views, wanted, query, max_rows
        )
        return rows

    async def execute_query_capped(
        self,
        workspace_id: str,
        lakehouse_id: str,
        query: str,
        max_rows: int = MAX_RESULT_ROWS,
        confirm_large_scan: bool = False,
    ) -> tuple[List[Dict[str, Any]], bool]:
        await self._ensure_catalog(workspace_id, lakehouse_id)
        wanted = self._tables_for_sql(query)
        await self._enforce_scan_cap(workspace_id, lakehouse_id, wanted, confirm_large_scan)
        return await asyncio.to_thread(self._run_with_views, wanted, query, max_rows)

    async def list_tables(self, workspace_id: str, lakehouse_id: str) -> List[Dict[str, Any]]:
        await self._ensure_catalog(workspace_id, lakehouse_id, force=True)
        return [
            {
                "schema": schema,
                "name": name,
                "type": "BASE TABLE",
                "full_name": f"{schema}.{name}",
            }
            for schema, name, _path in self._snapshot_tables()
        ]

    async def describe_table(
        self, workspace_id: str, lakehouse_id: str, qualified_sql: str
    ) -> List[Dict[str, Any]]:
        await self._ensure_catalog(workspace_id, lakehouse_id)
        schema, table = parse_qualified_ident(qualified_sql)
        wanted: List[Tuple[str, str]] = []
        if table:
            wanted.append((schema or self._default_schema, table))
        rows, _ = await asyncio.to_thread(
            self._run_with_views, wanted, f"DESCRIBE {qualified_sql}", None
        )
        columns: List[Dict[str, Any]] = []
        for i, row in enumerate(rows, start=1):
            null_raw = row.get("null")
            if isinstance(null_raw, bool):
                nullable = null_raw
            else:
                nullable = str(null_raw or "YES").upper() in ("YES", "TRUE", "1")
            columns.append(
                {
                    "name": row.get("column_name") or row.get("column"),
                    "data_type": row.get("column_type") or row.get("data_type"),
                    "is_nullable": nullable,
                    "is_primary_key": bool(row.get("key")),
                    "default_value": row.get("default"),
                    "max_length": None,
                    "precision": None,
                    "scale": None,
                    "position": i,
                }
            )
        return columns
