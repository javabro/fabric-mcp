# Agent notes

Guidance for AI coding agents working in this repo. See `PLAN.md` for architecture/decisions and `CONTRIBUTING.md` for PR process.

## Conventions & gotchas

- Use workspace/lakehouse **GUIDs** everywhere, not display names — friendly-name `abfss://` paths hit a 2026 OneLake `startFrom` list bug.
- `sql_validator.assert_select_only` allows `SELECT`/`WITH…SELECT` only; don't loosen it to a "starts with SELECT" check, and don't block `DESC` (needed for `ORDER BY x DESC`).
- Table discovery (`_discover_tables`) only lists OneLake folders to name tables — it must not `delta_scan` or list every table's files. That cost lives in `_table_size_bytes` / the data-volume guard instead, and only runs for the tables a query actually references.
- `execute_query` / `execute_query_capped` enforce a data-volume guard (`_enforce_scan_cap`) before scanning: total on-disk size of referenced tables vs. `FABRIC_MAX_QUERY_SCAN_MB` (default 10 MB, `0` disables). `describe_table`/`list_tables` are intentionally exempt — they only touch cheap schema metadata. Never pass `confirm_large_scan=True` on your own judgement; only after a human has explicitly reviewed and approved the exact size/table breakdown from a prior blocked call.
- Auth is `az login` / `DefaultAzureCredential` by default; no device-code flow (see `fabric_auth.py`).
