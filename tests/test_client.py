from typing import Any
from unittest.mock import MagicMock

import s3_mcp.server as server
from s3_mcp.server import Settings, create_client


def make_settings(**overrides: Any) -> Settings:
    params: dict[str, Any] = dict(
        endpoint_url=None,
        region="us-east-1",
        access_key_id="AKIDEXAMPLE",
        secret_access_key="secret",
        path_style=True,
        tls_insecure=False,
        ca_bundle=None,
    )
    params.update(overrides)
    return Settings(**params)


def patch_boto3(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(server.boto3, "client", fake)
    return fake


class TestCreateClient:
    def test_defaults_aws(self, monkeypatch):
        fake = patch_boto3(monkeypatch)
        create_client(make_settings())
        kwargs = fake.call_args.kwargs
        assert kwargs["endpoint_url"] is None
        assert kwargs["verify"] is True
        assert kwargs["config"].s3["addressing_style"] == "path"
        assert kwargs["config"].region_name == "us-east-1"

    def test_endpoint_and_region_passthrough(self, monkeypatch):
        fake = patch_boto3(monkeypatch)
        create_client(make_settings(endpoint_url="http://localhost:9000", region="eu-west-1"))
        kwargs = fake.call_args.kwargs
        assert kwargs["endpoint_url"] == "http://localhost:9000"
        assert kwargs["config"].region_name == "eu-west-1"

    def test_tls_insecure_disables_verify(self, monkeypatch):
        fake = patch_boto3(monkeypatch)
        create_client(make_settings(tls_insecure=True))
        assert fake.call_args.kwargs["verify"] is False

    def test_virtual_host_style_when_not_path(self, monkeypatch):
        fake = patch_boto3(monkeypatch)
        create_client(make_settings(path_style=False))
        assert fake.call_args.kwargs["config"].s3["addressing_style"] == "auto"

    def test_ca_bundle_path_used_as_verify(self, monkeypatch):
        fake = patch_boto3(monkeypatch)
        create_client(make_settings(ca_bundle="/etc/ssl/certs/ca-bundle.pem"))
        assert fake.call_args.kwargs["verify"] == "/etc/ssl/certs/ca-bundle.pem"
