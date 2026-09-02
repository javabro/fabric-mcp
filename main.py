"""Fabric MCP Server - Model Context Protocol server for Microsoft Fabric

This MCP server provides AI assistants with tools to explore Microsoft Fabric
workspaces and lakehouses. Catalog calls use the Fabric REST API. Table data is
queried with DuckDB against OneLake Delta tables (SELECT-only).

Available Tools:
- list_workspaces: Discover all accessible Fabric workspaces
- list_lakehouses: List lakehouses within a specific workspace
- get_lakehouse_tables: Enumerate all tables in a lakehouse (supports schema-enabled lakehouses)
- get_table_schema: Retrieve detailed column metadata from tables
- get_table_sample_data: Sample data from tables for exploration and understanding
- execute_custom_sql_query: Run a SELECT (DuckDB SQL) against lakehouse Delta tables

Authentication: Azure CLI (`az login`) or service principal via .env
Backend: FastMCP stdio + DuckDB (azure + delta_scan)
"""

from typing import Any
from fastmcp import FastMCP

from fabric_mcp_service import FabricMCPService

# Initialize FastMCP server
mcp = FastMCP("Fabric MCP Server")

# Initialize service layer that contains all business logic
service = FabricMCPService()


# ============================================================================
# MCP Tools - Fabric Workspace and Lakehouse Operations
# ============================================================================

@mcp.tool()
async def list_workspaces() -> dict[str, Any]:
    """List Microsoft Fabric workspaces (id + name). Call this first; later tools need the workspace GUID, not the display name (e.g. not "GLB-Storage-DEV").

    Use for Fabric / OneLake / lakehouse questions when the workspace UUID is unknown.

    Returns:
        dict: {"workspaces": [{"id": str, "name": str}, ...]}
    """
    return await service.list_workspaces()


@mcp.tool()
async def list_lakehouses(workspace_id: str) -> dict[str, Any]:
    """List Microsoft Fabric lakehouses in a workspace. workspace_id is the GUID from list_workspaces, not the workspace display name.

    Returns lakehouse GUIDs required by table and SQL tools.

    Args:
        workspace_id: Fabric workspace GUID from list_workspaces (UUID, not the name).

    Returns:
        dict: {"lakehouses": [{"id": str, "name": str}, ...]}
    """
    return await service.list_lakehouses(workspace_id)


@mcp.tool()
async def get_lakehouse_tables(workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
    """List OneLake Delta tables in a Microsoft Fabric lakehouse. Returns full_name (schema.table, e.g. dbo.zfa_glossary) for schema/sample/SQL tools.

    Use when you need table names. Skip if you already have schema.table. Lists OneLake folders only; does not open Delta files.

    Args:
        workspace_id: Fabric workspace GUID from list_workspaces.
        lakehouse_id: Fabric lakehouse GUID from list_lakehouses.

    Returns:
        dict: {"tables": [{"schema": str, "name": str, "type": str, "full_name": str}, ...]}
        Use full_name with get_table_schema, get_table_sample_data, and execute_custom_sql_query.
    """
    return await service.get_lakehouse_tables(workspace_id, lakehouse_id)


# ============================================================================
# MCP Tools - SQL Query Operations
# ============================================================================

@mcp.tool()
async def get_table_schema(workspace_id: str, lakehouse_id: str, table_name: str) -> dict[str, Any]:
    """Column names and types for one Microsoft Fabric lakehouse table. table_name must be full_name from get_lakehouse_tables (e.g. dbo.zfa_glossary), not an unqualified name.

    Use before writing DuckDB SQL if column names are unknown.

    Args:
        workspace_id: Fabric workspace GUID from list_workspaces.
        lakehouse_id: Fabric lakehouse GUID from list_lakehouses.
        table_name: schema.table (full_name), e.g. "dbo.zfa_glossary" or "silver.customers".

    Returns:
        dict with "table_name" and "columns" (name, data_type, is_nullable, position, ...).
    """
    return await service.get_table_schema(workspace_id, lakehouse_id, table_name)


@mcp.tool()
async def get_table_sample_data(
    workspace_id: str,
    lakehouse_id: str,
    table_name: str,
    limit: int = 10,
    confirm_large_scan: bool = False
) -> dict[str, Any]:
    """First N rows from a Microsoft Fabric lakehouse Delta table (DuckDB LIMIT, not a random sample). table_name must be schema.table (e.g. dbo.zfa_glossary).

    Use to peek at values. For COUNT, JOIN, WHERE, GROUP BY, or filtered extracts use execute_custom_sql_query.

    A data-volume guard blocks the call up front (before any Parquet is read) if the table
    exceeds ~10 MB on disk (env var FABRIC_MAX_QUERY_SCAN_MB). `limit` does not avoid this check:
    DuckDB must still read whole Delta files regardless of LIMIT. The blocked-call error reports
    the exact size.

    HIGH RISK: confirm_large_scan=true bypasses the guard and must only be set to true after a
    human has explicitly reviewed and approved the exact size/table breakdown from a prior
    blocked call's error. Never set this automatically on the agent's own judgement.

    Args:
        workspace_id: Fabric workspace GUID from list_workspaces.
        lakehouse_id: Fabric lakehouse GUID from list_lakehouses.
        table_name: schema.table (full_name), e.g. "dbo.zfa_glossary".
        limit: Row cap (default 10).
        confirm_large_scan: HIGH RISK. Only set true after explicit human approval of a prior
            blocked call's size/table breakdown.

    Returns:
        dict with "table_name", "sample_rows", and "row_count".
    """
    return await service.get_table_sample_data(
        workspace_id, lakehouse_id, table_name, limit, confirm_large_scan
    )


@mcp.tool()
async def execute_custom_sql_query(
    workspace_id: str,
    lakehouse_id: str,
    query: str,
    confirm_large_scan: bool = False
) -> dict[str, Any]:
    """DuckDB SELECT on Microsoft Fabric OneLake Delta (local DuckDB, not Spark or T-SQL). IDs are GUIDs from list_workspaces/list_lakehouses. Quote tables as "schema"."table" (e.g. SELECT COUNT(*) FROM "dbo"."zfa_glossary"). Use LIMIT, not TOP.

    Joins, WHERE, GROUP BY, and aggregations are allowed. Writes, DDL, COPY, ATTACH, and multi-statement batches are rejected. Results cap at 1000 rows.

    Use for row counts, filters, joins, and analysis. Do not use to list workspaces. Prefer get_table_sample_data only for a quick unfiltered peek.

    A data-volume guard blocks the call up front (before any Parquet is read) if the referenced
    tables exceed ~10 MB combined on disk (env var FABRIC_MAX_QUERY_SCAN_MB). A LIMIT in the query
    does not avoid this check: DuckDB must still read whole Delta files regardless of LIMIT. The
    blocked-call error reports the exact size and a per-table breakdown.

    HIGH RISK: confirm_large_scan=true bypasses the guard and must only be set to true after a
    human has explicitly reviewed and approved the exact size/table breakdown from a prior
    blocked call's error. Never set this automatically on the agent's own judgement.

    Args:
        workspace_id: Fabric workspace GUID from list_workspaces.
        lakehouse_id: Fabric lakehouse GUID from list_lakehouses.
        query: One DuckDB SELECT or WITH…SELECT. Example: SELECT COUNT(*) AS n FROM "dbo"."zfa_glossary"
        confirm_large_scan: HIGH RISK. Only set true after explicit human approval of a prior
            blocked call's size/table breakdown.

    Returns:
        dict: success, query, row_count, results; truncated=true if the 1000-row cap applied.
    """
    return await service.execute_custom_sql_query(
        workspace_id, lakehouse_id, query, confirm_large_scan
    )


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    mcp.run()
