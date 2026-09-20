"""s3-mcp: a generic S3-protocol MCP server over stdio.

Works against any S3-compatible endpoint (RustFS, MinIO, R2, B2, AWS).
Configuration comes exclusively from environment variables; a single boto3
client is created at startup.
"""

from __future__ import annotations

import base64 as b64
import logging
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

import boto3
import structlog
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

# SigV4 presigned URLs cap at 7 days.
_MAX_PRESIGN_S = 604800

READ_ONLY = ToolAnnotations(read_only_hint=True)

# Correlation ID for request tracing
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Global read-only mode flag (set in main() after loading settings)
READ_ONLY_MODE = False

# Structured logger (configured in main())
logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def _add_correlation_id(
    _: Any, __: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Add correlation_id from ContextVar to log event."""
    cid = correlation_id.get()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


@dataclass(frozen=True)
class Settings:
    endpoint_url: str | None
    region: str
    access_key_id: str
    secret_access_key: str
    path_style: bool
    tls_insecure: bool
    ca_bundle: str | None
    read_only: bool = False


def env_bool(name: str, env: Mapping[str, str], default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise RuntimeError(f"Invalid boolean for {name}: {raw!r} (use true/false/1/0)")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    access_key_id = env.get("AWS_ACCESS_KEY_ID", "").strip()
    secret_access_key = env.get("AWS_SECRET_ACCESS_KEY", "").strip()
    if not access_key_id:
        raise RuntimeError("AWS_ACCESS_KEY_ID is required")
    if not secret_access_key:
        raise RuntimeError("AWS_SECRET_ACCESS_KEY is required")
    endpoint_url = env.get("S3_ENDPOINT_URL", "").strip() or None
    region = env.get("AWS_REGION", "").strip() or "us-east-1"
    ca_bundle = env.get("SSL_CERT_FILE", "").strip() or None
    return Settings(
        endpoint_url=endpoint_url,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        path_style=env_bool("S3_PATH_STYLE", env, True),
        tls_insecure=env_bool("S3_TLS_INSECURE", env, False),
        ca_bundle=ca_bundle,
        read_only=env_bool("S3_READ_ONLY", env, False),
    )


def create_client(settings: Settings) -> Any:
    config = BotoConfig(
        region_name=settings.region,
        s3={"addressing_style": "path" if settings.path_style else "auto"},
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=10,
        read_timeout=120,
    )
    # Determine verify parameter: custom CA bundle path, or boolean based on tls_insecure
    verify: str | bool = settings.ca_bundle or (not settings.tls_insecure)
    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        config=config,
        verify=verify,
    )


_client: Any = None
_region: str = "us-east-1"


def set_client(client: Any, region: str | None = None) -> None:
    global _client, _region
    _client = client
    if region is not None:
        _region = region


def get_client() -> Any:
    global _client, _region
    if _client is None:
        settings = load_settings()
        _client = create_client(settings)
        _region = settings.region
    return _client


def new_correlation_id() -> str:
    """Generate a new correlation ID for request tracing."""
    return uuid.uuid4().hex[:12]


def set_correlation_id(cid: str | None = None) -> str:
    """Set and return a correlation ID (generates new if not provided)."""
    cid = cid or new_correlation_id()
    correlation_id.set(cid)
    return cid


def get_correlation_id() -> str | None:
    """Get the current correlation ID."""
    return correlation_id.get()


def _safe_args(fn: Callable[..., Any], args: tuple, kwargs: dict) -> dict:
    """Return a safe representation of args for logging (redact sensitive data by param name)."""
    import inspect

    sig = inspect.signature(fn)
    param_names = list(sig.parameters.keys())
    sensitive_params = {
        "secret_access_key",
        "access_key_id",
        "secret_key",
        "password",
        "token",
        "secret",
    }

    safe = {}
    # Positional args mapped to parameter names (skip 'client' and 'self')
    for i, arg in enumerate(args):
        if i < len(param_names):
            param_name = param_names[i]
        else:
            param_name = f"arg_{i}"
        if param_name in sensitive_params:
            safe[param_name] = "[REDACTED]"
        else:
            safe[param_name] = str(arg)[:200]
    # Keyword args
    for k, v in kwargs.items():
        if k in sensitive_params:
            safe[k] = "[REDACTED]"
        else:
            safe[k] = str(v)[:200]
    return safe


def s3_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Convert boto3/network/decoding errors into RuntimeError (MCP isError)."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        _ = set_correlation_id()
        logger.debug(
            "tool_call_start",
            tool=fn.__name__,
            args=_safe_args(fn, args, kwargs),
        )
        try:
            result = fn(*args, **kwargs)
            logger.debug(
                "tool_call_end",
                tool=fn.__name__,
                success=True,
            )
            return result
        except (ClientError, BotoCoreError, OSError, ValueError) as exc:
            message = str(exc).strip() or exc.__class__.__name__
            if isinstance(exc, ClientError):
                error = (exc.response or {}).get("Error", {})
                code = error.get("Code", "")
                text = error.get("Message", "")
                message = f"{code}: {text}".strip(": ") or message
            logger.error(
                "tool_call_error",
                tool=fn.__name__,
                error=message,
            )
            raise RuntimeError(message) from exc

    return wrapper


# --- plain helpers (client passed in; unit-testable without async) ---------


@s3_errors
def do_list_buckets(client: Any) -> list[dict[str, Any]]:
    buckets = []
    for item in client.list_buckets().get("Buckets", []):
        bucket: dict[str, Any] = {"name": item["Name"]}
        if item.get("CreationDate") is not None:
            bucket["creation_date"] = item["CreationDate"].isoformat()
        buckets.append(bucket)
    return buckets


@s3_errors
def do_list_objects(
    client: Any, bucket: str, prefix: str = "", max_keys: int = 1000
) -> dict[str, Any]:
    max_keys = max(1, min(int(max_keys), 1000))
    resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=max_keys)
    objects = [
        {
            "key": obj["Key"],
            "size": obj.get("Size", 0),
            "last_modified": obj["LastModified"].isoformat(),
            **({"etag": obj["ETag"]} if obj.get("ETag") else {}),
        }
        for obj in resp.get("Contents", [])
    ]
    return {
        "bucket": bucket,
        "prefix": prefix,
        "key_count": len(objects),
        "objects": objects,
        "truncated": bool(resp.get("IsTruncated", False)),
    }


@s3_errors
def do_get_object(client: Any, bucket: str, key: str) -> dict[str, Any]:
    resp = client.get_object(Bucket=bucket, Key=key)
    body: bytes = resp["Body"].read()
    content_type = resp.get("ContentType") or "application/octet-stream"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "encoding": "base64",
            "content_type": content_type,
            "size": len(body),
            "base64": b64.b64encode(body).decode("ascii"),
        }
    return {
        "encoding": "utf-8",
        "content_type": content_type,
        "size": len(body),
        "text": text,
    }


@s3_errors
def do_stat_object(client: Any, bucket: str, key: str) -> dict[str, Any]:
    resp = client.head_object(Bucket=bucket, Key=key)
    info: dict[str, Any] = {
        "bucket": bucket,
        "key": key,
        "size": resp.get("ContentLength", 0),
        "etag": resp.get("ETag"),
        "last_modified": (resp["LastModified"].isoformat() if resp.get("LastModified") else None),
    }
    if resp.get("ContentType"):
        info["content_type"] = resp["ContentType"]
    return {k: v for k, v in info.items() if v is not None}


def _presign(client: Any, method: str, bucket: str, key: str, expires_s: int) -> str:
    expires_s = int(expires_s)
    if not 1 <= expires_s <= _MAX_PRESIGN_S:
        raise ValueError(f"expires_s must be between 1 and {_MAX_PRESIGN_S}")
    return client.generate_presigned_url(
        method, Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires_s
    )


@s3_errors
def do_presign_get(client: Any, bucket: str, key: str, expires_s: int = 3600) -> str:
    return _presign(client, "get_object", bucket, key, expires_s)


@s3_errors
def do_presign_put(client: Any, bucket: str, key: str, expires_s: int = 3600) -> str:
    return _presign(client, "put_object", bucket, key, expires_s)


@s3_errors
def do_put_object(
    client: Any, bucket: str, key: str, body: str, base64: bool = False
) -> dict[str, Any]:
    data = b64.b64decode(body, validate=True) if base64 else body.encode("utf-8")
    client.put_object(Bucket=bucket, Key=key, Body=data)
    return {"bucket": bucket, "key": key, "size": len(data)}


@s3_errors
def do_delete_object(client: Any, bucket: str, key: str) -> dict[str, Any]:
    client.delete_object(Bucket=bucket, Key=key)
    return {"deleted": True, "bucket": bucket, "key": key}


@s3_errors
def do_copy_object(
    client: Any, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str
) -> dict[str, Any]:
    client.copy_object(
        Bucket=dst_bucket,
        Key=dst_key,
        CopySource={"Bucket": src_bucket, "Key": src_key},
    )
    return {
        "copied": True,
        "source": f"{src_bucket}/{src_key}",
        "destination": f"{dst_bucket}/{dst_key}",
    }


@s3_errors
def do_create_bucket(client: Any, bucket: str, region: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"Bucket": bucket}
    # AWS rejects CreateBucket without a LocationConstraint outside us-east-1;
    # third-party stores ignore it.
    if region and region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    client.create_bucket(**kwargs)
    return {"created": True, "bucket": bucket}


@s3_errors
def do_delete_bucket(client: Any, bucket: str) -> dict[str, Any]:
    client.delete_bucket(Bucket=bucket)
    return {"deleted": True, "bucket": bucket}


# --- MCP tool registration --------------------------------------------------

mcp = MCPServer("s3-mcp")


@mcp.tool(annotations=READ_ONLY)
def list_buckets() -> list[dict[str, Any]]:
    """List all buckets accessible with the configured credentials."""
    return do_list_buckets(get_client())


@mcp.tool(annotations=READ_ONLY)
def list_objects(bucket: str, prefix: str = "", max_keys: int = 1000) -> dict[str, Any]:
    """List objects in a bucket (single page, up to max_keys).

    Returns objects with key/size/last_modified plus a truncated flag when
    more results exist.
    """
    return do_list_objects(get_client(), bucket, prefix, max_keys)


@mcp.tool(annotations=READ_ONLY)
def get_object(bucket: str, key: str) -> dict[str, Any]:
    """Read an object. UTF-8 content returns as text; anything else as base64."""
    return do_get_object(get_client(), bucket, key)


@mcp.tool(annotations=READ_ONLY)
def stat_object(bucket: str, key: str) -> dict[str, Any]:
    """Get object metadata (size, etag, content type, last modified)."""
    return do_stat_object(get_client(), bucket, key)


@mcp.tool(annotations=READ_ONLY)
def presign_get(bucket: str, key: str, expires_s: int = 3600) -> str:
    """Generate a pre-signed GET URL valid for expires_s seconds (max 604800)."""
    return do_presign_get(get_client(), bucket, key, expires_s)


def _register_presign_put():
    if env_bool("S3_READ_ONLY", os.environ, False):
        logger.info(
            "skipping_write_tool",
            tool="presign_put",
            reason="read_only_mode",
        )
        return

    @mcp.tool()
    def presign_put(bucket: str, key: str, expires_s: int = 3600) -> str:
        """Generate a pre-signed PUT URL valid for expires_s seconds (max 604800)."""
        return do_presign_put(get_client(), bucket, key, expires_s)


_register_presign_put()


def _register_put_object():
    if env_bool("S3_READ_ONLY", os.environ, False):
        logger.info(
            "skipping_write_tool",
            tool="put_object",
            reason="read_only_mode",
        )
        return

    @mcp.tool()
    def put_object(bucket: str, key: str, body: str, base64: bool = False) -> dict[str, Any]:
        """Write an object. Set base64=true when body is base64-encoded binary."""
        return do_put_object(get_client(), bucket, key, body, base64)


_register_put_object()


def _register_delete_object():
    if env_bool("S3_READ_ONLY", os.environ, False):
        logger.info(
            "skipping_write_tool",
            tool="delete_object",
            reason="read_only_mode",
        )
        return

    @mcp.tool()
    def delete_object(bucket: str, key: str) -> dict[str, Any]:
        """Delete an object from a bucket."""
        return do_delete_object(get_client(), bucket, key)


_register_delete_object()


def _register_copy_object():
    if env_bool("S3_READ_ONLY", os.environ, False):
        logger.info(
            "skipping_write_tool",
            tool="copy_object",
            reason="read_only_mode",
        )
        return

    @mcp.tool()
    def copy_object(src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> dict[str, Any]:
        """Copy an object within or across buckets on the same endpoint."""
        return do_copy_object(get_client(), src_bucket, src_key, dst_bucket, dst_key)


_register_copy_object()


def _register_create_bucket():
    if env_bool("S3_READ_ONLY", os.environ, False):
        logger.info(
            "skipping_write_tool",
            tool="create_bucket",
            reason="read_only_mode",
        )
        return

    @mcp.tool()
    def create_bucket(bucket: str) -> dict[str, Any]:
        """Create a bucket in the configured region."""
        return do_create_bucket(get_client(), bucket, _region)


_register_create_bucket()


def _register_delete_bucket():
    if env_bool("S3_READ_ONLY", os.environ, False):
        logger.info(
            "skipping_write_tool",
            tool="delete_bucket",
            reason="read_only_mode",
        )
        return

    @mcp.tool()
    def delete_bucket(bucket: str) -> dict[str, Any]:
        """Delete a bucket (must be empty)."""
        return do_delete_bucket(get_client(), bucket)


_register_delete_bucket()


def main() -> None:
    global _client, _region, READ_ONLY_MODE
    settings = load_settings()
    READ_ONLY_MODE = settings.read_only

    # Configure structlog for JSON output to stderr (never stdout for stdio transport)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    # Ensure stdlib logging goes to stderr
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    logger.info("server_start", read_only=READ_ONLY_MODE)

    _client = create_client(settings)
    _region = settings.region
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
