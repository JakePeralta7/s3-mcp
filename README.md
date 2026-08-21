# s3-mcp

A generic S3-protocol MCP server (stdio) that works against **any** S3-compatible
endpoint — [RustFS](https://rustfs.com), MinIO, Cloudflare R2, Backblaze B2, AWS S3 — not just AWS.

Single Python package, one boto3 client created at startup, configuration via
environment variables only. Built on the official [`mcp`](https://pypi.org/project/mcp/)
SDK (high-level server API) and `boto3`.

## Configuration (environment variables)

| Variable                | Required | Default     | Description                                                                 |
| ----------------------- | -------- | ----------- | --------------------------------------------------------------------------- |
| `S3_ENDPOINT_URL`       | no       | *(unset)*   | S3-compatible endpoint; omit to target real AWS                             |
| `AWS_REGION`            | no       | `us-east-1` | Region for signing and bucket creation                                      |
| `AWS_ACCESS_KEY_ID`     | yes      | —           | Access key                                                                  |
| `AWS_SECRET_ACCESS_KEY` | yes      | —           | Secret key                                                                  |
| `S3_PATH_STYLE`         | no       | `true`      | Path-style addressing (`https://host/bucket/key`) — required by most non-AWS stores |
| `S3_TLS_INSECURE`       | no       | `false`     | `true` disables TLS certificate verification (self-signed endpoints)        |

Booleans accept `true/false/1/0/yes/no` (case-insensitive). Missing credentials
or invalid booleans abort startup with a clear error.

## Tools

| Tool                                                             | Read-only hint |
| ---------------------------------------------------------------- | -------------- |
| `list_buckets()`                                                 | yes            |
| `list_objects(bucket, prefix="", max_keys=1000)`                 | yes            |
| `get_object(bucket, key)` → text if UTF-8 else `{base64, content_type}` | yes     |
| `stat_object(bucket, key)`                                       | yes            |
| `presign_get(bucket, key, expires_s=3600)`                       | yes            |
| `put_object(bucket, key, body, base64=false)`                    | no             |
| `delete_object(bucket, key)`                                     | no             |
| `copy_object(src_bucket, src_key, dst_bucket, dst_key)`          | no             |
| `create_bucket(bucket)`                                          | no             |
| `delete_bucket(bucket)`                                          | no             |
| `presign_put(bucket, key, expires_s=3600)`                       | no             |

Tool errors are raised as short `RuntimeError` messages and surfaced by MCP as
error results. Presigned URLs are capped at 7 days (604800 s), per SigV4.

## Example configurations

### RustFS with a self-signed certificate

```json
{
  "mcpServers": {
    "s3-mcp": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "S3_ENDPOINT_URL=https://rustfs.local:9000",
        "-e", "S3_TLS_INSECURE=true",
        "-e", "AWS_REGION=us-east-1",
        "-e", "AWS_ACCESS_KEY_ID",
        "-e", "AWS_SECRET_ACCESS_KEY",
        "ghcr.io/jakeperalta7/s3-mcp:latest"
      ]
    }
  }
}
```

`-e VAR` without a value passes the variable through from your shell, so
credentials never appear in the config file.

### MinIO

```json
{
  "mcpServers": {
    "s3-mcp": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "S3_ENDPOINT_URL=http://localhost:9000",
        "-e", "AWS_REGION=us-east-1",
        "-e", "AWS_ACCESS_KEY_ID",
        "-e", "AWS_SECRET_ACCESS_KEY",
        "ghcr.io/jakeperalta7/s3-mcp:latest"
      ]
    }
  }
}
```

### AWS S3

Omit `S3_ENDPOINT_URL`; standard AWS credential/region resolution applies:

```json
{
  "mcpServers": {
    "s3-mcp": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "AWS_REGION",
        "-e", "AWS_ACCESS_KEY_ID",
        "-e", "AWS_SECRET_ACCESS_KEY",
        "ghcr.io/jakeperalta7/s3-mcp:latest"
      ]
    }
  }
}
```

### Without Docker (uv)

```bash
uvx --from git+https://github.com/JakePeralta7/s3-mcp s3-mcp
```

```json
{
  "mcpServers": {
    "s3-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/JakePeralta7/s3-mcp", "s3-mcp"],
      "env": {
        "S3_ENDPOINT_URL": "http://localhost:9000",
        "AWS_ACCESS_KEY_ID": "...",
        "AWS_SECRET_ACCESS_KEY": "..."
      }
    }
  }
}
```

## Development

```bash
uv sync --group dev   # install deps into .venv
uv run pytest -q      # unit tests (boto3 mocked, no network)
docker build -t s3-mcp:dev .
```

Entrypoint: `python -m s3_mcp` (stdio transport only).

## Releasing

Every push to `main` runs tests, then publishes
`ghcr.io/<owner>/<repo>:<version>` + `:latest`, where `<version>` is read from
`version` in `pyproject.toml`. To release, bump the version and merge to main.
Git tags are not used.

## License

[MIT](LICENSE)
