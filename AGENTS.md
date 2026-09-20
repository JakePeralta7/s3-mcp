# AGENTS.md

## Commands

- Install deps: `uv sync --group dev` (creates `.venv`; Python >= 3.12)
- Tests: `uv run pytest -q` — single test: `uv run pytest tests/test_tools.py -k name`
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .` (check only: `uv run ruff format --check .`)
- Type check: `uv run pyright`
- Build image: `docker build -t s3-mcp:dev .`
- Run server: `uv run python -m s3_mcp` (fails fast with `RuntimeError` unless
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` are set)
- Read-only mode: set `S3_READ_ONLY=true` to disable write tools (put_object,
  delete_object, copy_object, create_bucket, delete_bucket, presign_put)
- Committed `pyrightconfig.json` points Pyright at `.venv`: until `uv sync`
  has run, editors show false "Import could not be resolved" errors for
  boto3/mcp/pytest — install deps before trusting diagnostics.
- Dev shell is Windows PowerShell: inline `python -c "..."` breaks on quoting —
  write a temp `.py` file instead.

## Layout / architecture

- The whole server is one module, `src/s3_mcp/server.py`. Keep it that way:
  env parsing (`load_settings`) -> boto3 client factory (`create_client`) ->
  plain `do_*` helpers -> 11 `@mcp.tool` registrations -> `main()`.
- Entrypoint chain: `python -m s3_mcp` -> `__main__.py` -> `main()` builds ONE
  boto3 client from env, then `mcp.run(transport="stdio")`.
- Tools are thin wrappers over `do_*` helpers that take the client as first
  arg. New tools must follow this split; tests call helpers directly with a
  MagicMock client and never go through async MCP machinery.
- The client/region live in module globals (`_client`, `_region`), set by
  `main()` or injected in tests via `server.set_client(client, region)`.
  Never build clients at import time.
- The stdlib module is imported as `import base64 as b64` because `put_object`
  has a parameter literally named `base64`.
- **Correlation IDs**: Each tool call gets a 12-char hex correlation ID
  (`uuid.uuid4().hex[:12]`) for request tracing. Included in structured logs.
- **Read-only mode**: Controlled by `S3_READ_ONLY` env var. When enabled,
  write tools are not registered at all (hidden from MCP clients).
- **Structured logging**: Logs include `correlation_id`, tool name, and
  sanitized arguments (secrets redacted). Output goes to stderr for stdio transport.

## mcp SDK v2 pitfalls (`mcp>=2,<3`)

- Import is `from mcp.server import MCPServer`. `mcp.server.fastmcp.FastMCP`
  was removed in v2 — old tutorials/snippets will not import.
- Pydantic fields are snake_case (`is_error`, `structured_content`,
  `server_info`); the JSON wire stays camelCase via aliases. When inspecting
  annotations use `model_dump(by_alias=True)` (Python attr is
  `read_only_hint`, wire key is `readOnlyHint`).
- A dict return from a tool reaches test clients as
  `structured_content == {"result": ...}`.
- Plain dicts are accepted for `@mcp.tool(annotations={...})`;
  `ToolAnnotations` from `mcp_types` also works.
- Errors: helpers are wrapped in `@s3_errors`, which converts
  ClientError/BotoCoreError/OSError/ValueError into `RuntimeError(short
  message)` (MCP turns that into an isError result). Raise `ValueError`
  inside helpers for validation failures (presign bounds >604800s, bad base64).
- Tool annotations use `ToolAnnotations` from `mcp.types` (e.g.,
  `READ_ONLY = ToolAnnotations(read_only_hint=True)`).

## Testing rules

- Unit tests must not touch the network — mock the client. Fixtures live in
  the test files (no conftest.py): `test_tools.py` uses `server.set_client`.
- Live checks against a real endpoint are manual only: spawn
  `docker run -i --rm s3-mcp:dev` via `mcp.client.stdio.StdioServerParameters`
  with `-e VAR=...` args. Never hardcode credentials; pass them through env.

## Packaging / release gotchas

- `README.md` is referenced by hatchling AND copied in the Dockerfile builder
  stage — renaming/removing it breaks both builds.
- `uv.lock` is committed; re-run `uv sync` after touching dependencies so it
  updates.
- `.github/workflows/release.yml`: every push to main runs pytest, then builds
  and pushes `ghcr.io/<lowercased repo>:<pyproject version>` plus `:latest`.
  Releasing = bump `version` in pyproject.toml and push to main. Git tags are
  NOT used for releases and trigger nothing.
- Docker image uses `python:3.12-slim-trixie` (Debian 13) base with uv pinned
  by SHA256 for reproducible builds.
- CI runs on Python 3.12 and 3.13 with lint (ruff), format (ruff), and type
  check (pyright) gates in addition to tests.