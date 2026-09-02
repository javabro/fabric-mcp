"""Service layer for Fabric MCP tools."""

import string
from typing import Any

from dotenv import load_dotenv

from fabric_api_client import FabricAPIClient
from fabric_auth import create_auth_provider_from_env
from fabric_duckdb_client import FabricDuckDBClient
from sql_validator import assert_select_only


class FabricMCPService:
    def __init__(self) -> None:
        load_dotenv()
        self.auth_provider = create_auth_provider_from_env()
        self.fabric_api = FabricAPIClient(self.auth_provider)
        self.fabric_sql = FabricDuckDBClient(self.auth_provider, self.fabric_api)

    @staticmethod
    def _validate_sql_identifier(identifier: str) -> None:
        if not identifier:
            raise ValueError("Identifier cannot be empty")
        allowed_chars = set(string.ascii_letters + string.digits + "_-")
        invalid_chars = set(identifier) - allowed_chars
        if invalid_chars:
            raise ValueError(f"Invalid characters in identifier: {invalid_chars}")

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        FabricMCPService._validate_sql_identifier(identifier)
        return '"' + identifier.replace('"', '""') + '"'

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValueError(f"Limit must be an integer, got {type(limit).__name__}")
        if limit <= 0:
            raise ValueError(f"Limit must be a positive integer, got {limit}")

    def _qualify_table(self, table_name: str) -> str:
        dot_count = table_name.count(".")
        if dot_count == 1:
            schema_name, table_only = table_name.split(".", 1)
            self._validate_sql_identifier(schema_name)
            self._validate_sql_identifier(table_only)
            return f"{self._quote_identifier(schema_name)}.{self._quote_identifier(table_only)}"
        if dot_count == 0:
            self._validate_sql_identifier(table_name)
            return self._quote_identifier(table_name)
        raise ValueError(
            f"Invalid table name format. Expected 'table' or 'schema.table', got: '{table_name}'"
        )

    async def list_workspaces(self) -> dict[str, Any]:
        data = await self.fabric_api.get("/workspaces")
        workspaces = [{"id": w["id"], "name": w["displayName"]} for w in data.get("value", [])]
        return {"workspaces": workspaces}

    async def list_lakehouses(self, workspace_id: str) -> dict[str, Any]:
        data = await self.fabric_api.get(f"/workspaces/{workspace_id}/lakehouses")
        lakehouses = [
            {"id": lh["id"], "name": lh["displayName"]} for lh in data.get("value", [])
        ]
        return {"lakehouses": lakehouses}

    async def get_lakehouse_tables(self, workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
        try:
            tables = await self.fabric_sql.list_tables(workspace_id, lakehouse_id)
            return {"tables": tables}
        except Exception as e:  # pragma: no cover
            return {"tables": [], "error": f"Failed to retrieve tables: {str(e)}"}

    async def get_table_schema(
        self, workspace_id: str, lakehouse_id: str, table_name: str
    ) -> dict[str, Any]:
        try:
            qualified = self._qualify_table(table_name)
        except ValueError as e:
            return {"table_name": table_name, "columns": [], "error": str(e)}
        try:
            columns = await self.fabric_sql.describe_table(
                workspace_id, lakehouse_id, qualified
            )
            return {"table_name": table_name, "columns": columns}
        except Exception as e:  # pragma: no cover
            return {"table_name": table_name, "columns": [], "error": str(e)}

    async def get_table_sample_data(
        self,
        workspace_id: str,
        lakehouse_id: str,
        table_name: str,
        limit: int = 10,
        confirm_large_scan: bool = False,
    ) -> dict[str, Any]:
        try:
            self._validate_limit(limit)
            qualified = self._qualify_table(table_name)
        except ValueError as e:
            return {
                "table_name": table_name,
                "sample_rows": [],
                "row_count": 0,
                "error": str(e),
            }
        sample_query = f"SELECT * FROM {qualified} LIMIT {limit}"
        try:
            results = await self.fabric_sql.execute_query(
                workspace_id,
                lakehouse_id,
                sample_query,
                max_rows=limit,
                confirm_large_scan=confirm_large_scan,
            )
            return {
                "table_name": table_name,
                "sample_rows": results,
                "row_count": len(results),
            }
        except Exception as e:  # pragma: no cover
            return {
                "table_name": table_name,
                "sample_rows": [],
                "row_count": 0,
                "error": f"Failed to get sample data: {str(e)}",
            }

    async def execute_custom_sql_query(
        self,
        workspace_id: str,
        lakehouse_id: str,
        query: str,
        confirm_large_scan: bool = False,
    ) -> dict[str, Any]:
        try:
            assert_select_only(query)
        except ValueError as e:
            return {
                "query": query,
                "success": False,
                "error": str(e),
                "results": [],
            }
        try:
            results, truncated = await self.fabric_sql.execute_query_capped(
                workspace_id, lakehouse_id, query, confirm_large_scan=confirm_large_scan
            )
            payload: dict[str, Any] = {
                "query": query,
                "success": True,
                "row_count": len(results),
                "results": results,
            }
            if truncated:
                payload["truncated"] = True
                payload["message"] = "Result truncated to 1000 rows. Add LIMIT in the query."
            return payload
        except Exception as e:  # pragma: no cover
            return {"query": query, "success": False, "error": str(e), "results": []}
