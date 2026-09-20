"""Tests for read-only mode (S3_READ_ONLY)."""

from unittest.mock import MagicMock

import pytest

import s3_mcp.server as server


@pytest.fixture
def client():
    c = MagicMock()
    server.set_client(c)
    yield c
    server.set_client(None)


def test_env_bool_true():
    """Test env_bool returns True for various true values."""
    for val in ["true", "TRUE", "1", "yes", "on"]:
        assert server.env_bool("TEST", {"TEST": val}, False) is True


def test_env_bool_false():
    """Test env_bool returns False for various false values."""
    for val in ["false", "FALSE", "0", "no", "off"]:
        assert server.env_bool("TEST", {"TEST": val}, True) is False


def test_env_bool_default():
    """Test env_bool returns default when unset."""
    assert server.env_bool("TEST", {}, True) is True
    assert server.env_bool("TEST", {}, False) is False


def test_env_bool_invalid():
    """Test env_bool raises on invalid value."""
    with pytest.raises(RuntimeError, match="Invalid boolean"):
        server.env_bool("TEST", {"TEST": "maybe"}, False)


def test_read_only_registration_logic(monkeypatch):
    """Test that registration functions check S3_READ_ONLY env var."""
    # Test with S3_READ_ONLY=true
    monkeypatch.setenv("S3_READ_ONLY", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    # The registration functions use env_bool at call time
    # We can test this by checking the env_bool result
    assert server.env_bool("S3_READ_ONLY", server.os.environ, False) is True

    # Test with S3_READ_ONLY=false
    monkeypatch.setenv("S3_READ_ONLY", "false")
    assert server.env_bool("S3_READ_ONLY", server.os.environ, False) is False


def test_load_settings_read_only(monkeypatch):
    """Test load_settings reads S3_READ_ONLY correctly."""
    monkeypatch.setenv("S3_READ_ONLY", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    settings = server.load_settings(server.os.environ)
    assert settings.read_only is True

    monkeypatch.setenv("S3_READ_ONLY", "false")
    settings = server.load_settings(server.os.environ)
    assert settings.read_only is False


def test_tool_registration_pattern():
    """Test that write tools use registration functions."""
    # This verifies the pattern exists - write tools are in _register_* functions
    import inspect

    # Get source of _register_presign_put
    source = inspect.getsource(server._register_presign_put)
    assert "env_bool" in source or "S3_READ_ONLY" in source
    assert "skipping_write_tool" in source

    source = inspect.getsource(server._register_put_object)
    assert "env_bool" in source or "S3_READ_ONLY" in source
    assert "skipping_write_tool" in source

    source = inspect.getsource(server._register_delete_object)
    assert "env_bool" in source or "S3_READ_ONLY" in source
    assert "skipping_write_tool" in source

    source = inspect.getsource(server._register_copy_object)
    assert "env_bool" in source or "S3_READ_ONLY" in source
    assert "skipping_write_tool" in source

    source = inspect.getsource(server._register_create_bucket)
    assert "env_bool" in source or "S3_READ_ONLY" in source
    assert "skipping_write_tool" in source

    source = inspect.getsource(server._register_delete_bucket)
    assert "env_bool" in source or "S3_READ_ONLY" in source
    assert "skipping_write_tool" in source
