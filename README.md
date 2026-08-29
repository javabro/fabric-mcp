# Fabric MCP Server

MCP server for exploring Microsoft Fabric lakehouses: workspaces, tables, samples, and SELECT queries.

Catalog uses the Fabric REST API. Table data is read with **DuckDB** (`delta_scan` + Azure storage token) from OneLake Delta. Custom SQL is SELECT-only. See `PLAN.md`.

## Prerequisites

- Python 3.12+
- Azure CLI (`az login` on the same tenant as Fabric)
- An MCP client (Cursor / VS Code Copilot)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
az login
```

Service principal (optional): copy `.env.example` to `.env` and set `FABRIC_CLIENT_ID`, `FABRIC_CLIENT_SECRET`, `FABRIC_TENANT_ID`.

### Run the MCP server

For stdio transport (recommended for VS Code):
```bash
python main.py
```

For development/testing with interactive mode:
```bash
fastmcp dev main:mcp
```

Or simply:
```bash
python main.py
```

## Available Tools

### `list_workspaces()`
Lists all Microsoft Fabric workspaces accessible to the authenticated user.

**Returns:**
```json
{
  "workspaces": [
    {"id": "workspace-id", "name": "Workspace Name"}
  ]
}
```

### `list_lakehouses(workspace_id: str)`
Lists all lakehouses in a specific workspace.

**Parameters:**
- `workspace_id` (str): The ID of the workspace

**Returns:**
```json
{
  "lakehouses": [
    {"id": "lakehouse-id", "name": "Lakehouse Name"}
  ]
}
```

### `get_lakehouse_tables(workspace_id: str, lakehouse_id: str)`
Get all tables in a lakehouse. Works with both schema-enabled and schema-less lakehouses (OneLake `Tables/` listing).

**Parameters:**
- `workspace_id` (str): The ID of the workspace
- `lakehouse_id` (str): The ID of the lakehouse

**Returns:**
```json
{
  "tables": [
    {
      "schema": "bronze",
      "name": "Customer",
      "type": "BASE TABLE",
      "full_name": "bronze.Customer"
    }
  ]
}
```

### `get_table_schema(workspace_id: str, lakehouse_id: str, table_name: str)`
Get detailed schema information for a table including column definitions.

**Parameters:**
- `workspace_id` (str): The ID of the workspace
- `lakehouse_id` (str): The ID of the lakehouse
- `table_name` (str): The name of the table

**Returns:**
```json
{
  "table_name": "Customer",
  "columns": [
    {
      "name": "CustomerID",
      "data_type": "int",
      "is_nullable": false,
      "position": 1
    }
  ]
}
```

### `get_table_sample_data(workspace_id: str, lakehouse_id: str, table_name: str, limit: int = 10)`
Get sample data from a table.

**Parameters:**
- `workspace_id` (str): The ID of the workspace
- `lakehouse_id` (str): The ID of the lakehouse
- `table_name` (str): The name of the table
- `limit` (int): Number of rows to return (default: 10)

**Returns:**
```json
{
  "table_name": "Customer",
  "sample_rows": [{"CustomerID": 1, "Name": "John"}],
  "row_count": 10
}
```

### `execute_custom_sql_query(workspace_id: str, lakehouse_id: str, query: str)`
Run a DuckDB **SELECT** (joins, aggregations, CTEs). Writes and multi-statement batches are rejected. Results cap at 1000 rows.

**Returns:**
```json
{
  "query": "SELECT * FROM bronze.Customer",
  "success": true,
  "row_count": 100,
  "results": [{"CustomerID": 1, "Name": "John"}]
}
```

## Integration with GitHub Copilot

### Method 1: Using mcp.json (Recommended if you have MCP extension)

1. The `mcp.json` file in this directory is already configured
2. If you have the VS Code MCP extension installed, you should see "Run" buttons in the file
3. Click "Run" to start the server
4. The server will automatically integrate with GitHub Copilot

### Method 2: Manual VS Code Settings Configuration

1. Open VS Code Settings (JSON):
   - Press `Ctrl+Shift+P` (Windows) or `Cmd+Shift+P` (Mac)
   - Type "Preferences: Open User Settings (JSON)"
   - Press Enter

2. Add this configuration:
```json
{
  "github.copilot.chat.mcp.enabled": true,
  "github.copilot.chat.mcp.servers": {
    "fabric": {
      "command": "${workspaceFolder}/.venv/bin/python",
      "args": ["main.py"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

3. Reload VS Code:
   - Press `Ctrl+Shift+P`
   - Type "Developer: Reload Window"
   - Press Enter

### Testing the Integration

Once configured, open GitHub Copilot Chat and ask questions like:
- "List all my Fabric workspaces"
- "Show me the lakehouses in workspace XYZ"
- "What Microsoft Fabric resources do I have access to?"

Copilot will automatically use your MCP tools to answer these questions.

## Development

### Testing tools locally

You can test the MCP server locally by running:

```bash
python main.py
```

Or use the FastMCP CLI for interactive testing:

```bash
fastmcp dev main:mcp
```

This will start an interactive session where you can call tools directly.

### Adding new tools

To add a new tool, use the `@mcp.tool()` decorator:

```python
@mcp.tool()
async def my_new_tool(param: str) -> dict:
    """
    Tool description for Copilot.
    
    Args:
        param: Parameter description
    
    Returns:
        dict: Response data
    """
    # Implementation
    return {"result": "data"}
```

## Security Notes

- Never commit `.env` file to version control
- Use service principal authentication with minimum required permissions
- Rotate secrets regularly
- Consider using Azure Key Vault for production deployments

## Troubleshooting

### Token acquisition fails
- Run `az account show` and confirm the tenant matches Fabric.
- `az login --tenant <fabric-tenant-id>`
- Workspace Viewer (or higher) is required to list items. OneLake **file** reads can still 403 if OneLake security roles block the identity.

### DuckDB / OneLake read fails
- First run downloads DuckDB `azure` and `delta` extensions (needs network).
- Use workspace/lakehouse **GUIDs** from the list tools, not display names.
- If you see "no files in log segment", retry; that was a 2026 OneLake list-API bug (mostly fixed; GUID paths avoid it).

### Tools not appearing
- MCP command must be `.venv/bin/python` (see `.vscode/mcp.json`).
- Restart the MCP server after `pip install`.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
