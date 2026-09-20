# syntax=docker/dockerfile:1

FROM python:3.12-slim-trixie AS builder
WORKDIR /app

# Install uv (pinned by SHA256 for reproducibility)
COPY --from=ghcr.io/astral-sh/uv@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /uvx /bin/

# Dependencies layer (cached independently of app source).
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --target=/install "mcp>=2,<3" "boto3>=1.34" "structlog>=24.1"

# App layer.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-deps --target=/install .


FROM python:3.12-slim-trixie
LABEL org.opencontainers.image.title="s3-mcp" \
      org.opencontainers.image.description="Generic S3-protocol MCP server (stdio) for any S3-compatible endpoint." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/JakePeralta7/s3-mcp"
RUN groupadd --system app && useradd --system --gid app --home-dir /nonexistent app
COPY --from=builder /install/ /usr/local/lib/python3.12/site-packages/
ENV PYTHONUNBUFFERED=1
USER app
ENTRYPOINT ["python", "-m", "s3_mcp"]