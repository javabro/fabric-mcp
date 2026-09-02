# Fabric MCP — plan

Last updated: 2026-08-27

## Goal

Live lakehouse explorer for an AI chat: list workspaces/lakehouses, list tables, peek at rows, join tables, run a custom **SELECT**. Lakehouses only.

## Locked decisions

| Decision | Choice |
|---|---|
| Query engine | **DuckDB** on OneLake Delta via `delta_scan` (local compute, no Fabric Spark/SQL CUs) |
| OneLake files | Storage token `https://storage.azure.com/.default` as DuckDB Azure `ACCESS_TOKEN` (`ACCOUNT_NAME 'onelake'`) |
| Table discovery | OneLake DFS list of `Tables/` (schema-less and schema-enabled). Not a recursive Parquet listing |
| Paths | GUIDs in `abfss://` (friendly names hit a 2026 OneLake `startFrom` list bug) |
| SQL dialect | DuckDB (`LIMIT`, not T-SQL `TOP`) |
| Custom SQL | **SELECT-only** (validator in this server) |
| Auth | **`az login`** via Azure CLI / `DefaultAzureCredential`. SPN if `FABRIC_CLIENT_SECRET` is set |
| REST | `https://api.fabric.microsoft.com/v1` with Fabric token (`https://api.fabric.microsoft.com/.default`) |
| ODBC / T-SQL / Spark / Livy / warehouses / community `onelake` extension | Out of scope |

The community `onelake` extension (`INSTALL onelake FROM community`) has no build for DuckDB 1.5.5 on `osx_arm64` (404). **azure + delta + `delta_scan`** is the implementation.

```
AI chat → MCP tools → FabricMCPService
  → FabricAPIClient        REST workspaces / lakehouses
  → FabricDuckDBClient     DFS list Tables/, views over delta_scan(abfss GUID path)
```

## Auth (no extra Entra app for local use)

Same `az login` mints two tokens:

1. Fabric API — list workspaces/lakehouses
2. Storage — read Delta files and list OneLake DFS

DuckDB gets the storage token via `CREATE OR REPLACE SECRET` (`PROVIDER ACCESS_TOKEN`) before each query (~1h expiry). SPN uses the same path through `azure-identity`.

## How a query runs

1. DFS `GET https://onelake.dfs.fabric.microsoft.com/{workspaceId}?resource=filesystem&directory={lakehouseId}/Tables&recursive=false`
2. Name tables from that listing (schema-less: `_delta_log` on the folder; schema-enabled: child folders). Do **not** `delta_scan` every table.
3. On SELECT / DESCRIBE / sample, `CREATE VIEW` + `delta_scan` **only** for tables the SQL names.
4. Custom SQL must pass `assert_select_only`

DuckDB range-reads the Delta log and needed Parquet. It does not copy the lakehouse locally.

## SELECT-only

Allow `SELECT` and `WITH … SELECT`. Reject writes/DDL/`EXEC`/`COPY`/`ATTACH`, `SELECT INTO`, multi-statement batches. Do not block `DESC` (needed for `ORDER BY x DESC`). Not a “starts with SELECT” check.

## Todos

1. SELECT-only validator — **done** (`sql_validator.py`)
2. DuckDB client (`delta_scan` + Azure secret + DFS listing, lazy views) — **done** (`fabric_duckdb_client.py`)
3. azure-identity for REST + storage tokens — **done** (`fabric_auth.py`)
4. REST `continuationToken` pagination — **done**
5. Runtime: drop pyodbc/msal, add duckdb + azure-identity + pytz, point MCP at `.venv` — **done**

## Limits (by design)

- DuckDB runs on the laptop. Samples and small/medium joins are the point. Huge unfiltered joins will pull a lot of Parquet over the network.
- SQL endpoint views and RLS/CLS/OLS are **not** applied (raw Delta).
- Sample is first N rows (`LIMIT`), not random.
- Custom SELECT results capped at 1000 rows.
- Data-volume guard: before `execute_query`/`execute_query_capped` run, referenced tables' total on-disk size is checked (cheap recursive OneLake listing, no data read) against `FABRIC_MAX_QUERY_SCAN_MB` (default 10 MB, `0` disables). Over cap raises with a size/table breakdown instead of scanning. `LIMIT` does not bypass this — DuckDB still reads whole Delta files. Bypass via `confirm_large_scan=True`, only after a human approves the reported breakdown.

## Ideas (later)

- Random sample / `TABLESAMPLE`
- Warehouse tools
- Community `onelake` `ATTACH` if they publish a 1.5.x `osx_arm64` build
