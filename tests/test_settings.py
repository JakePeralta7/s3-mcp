import pytest

from s3_mcp.server import Settings, env_bool, load_settings


def base_env(**overrides):
    env = {
        "AWS_ACCESS_KEY_ID": "AKIDEXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "secret",
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


class TestLoadSettings:
    def test_defaults(self):
        s = load_settings(base_env())
        assert s == Settings(
            endpoint_url=None,
            region="us-east-1",
            access_key_id="AKIDEXAMPLE",
            secret_access_key="secret",
            path_style=True,
            tls_insecure=False,
        )

    def test_full_custom(self):
        s = load_settings(
            base_env(
                S3_ENDPOINT_URL="https://rustfs.local:9000",
                AWS_REGION="eu-west-1",
                S3_PATH_STYLE="false",
                S3_TLS_INSECURE="true",
            )
        )
        assert s.endpoint_url == "https://rustfs.local:9000"
        assert s.region == "eu-west-1"
        assert s.path_style is False
        assert s.tls_insecure is True

    def test_blank_endpoint_is_none(self):
        s = load_settings(base_env(S3_ENDPOINT_URL="   "))
        assert s.endpoint_url is None

    @pytest.mark.parametrize("value", ["true", "TRUE", "1", "Yes", "on"])
    def test_bool_true_variants(self, value):
        assert load_settings(base_env(S3_PATH_STYLE=value)).path_style is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "No", "off"])
    def test_bool_false_variants(self, value):
        assert load_settings(base_env(S3_PATH_STYLE=value)).path_style is False

    def test_invalid_bool_raises(self):
        with pytest.raises(RuntimeError, match="S3_TLS_INSECURE"):
            load_settings(base_env(S3_TLS_INSECURE="maybe"))

    def test_missing_access_key_raises(self):
        with pytest.raises(RuntimeError, match="AWS_ACCESS_KEY_ID"):
            load_settings({"AWS_SECRET_ACCESS_KEY": "secret"})

    def test_missing_secret_key_raises(self):
        with pytest.raises(RuntimeError, match="AWS_SECRET_ACCESS_KEY"):
            load_settings({"AWS_ACCESS_KEY_ID": "AKIDEXAMPLE"})

    def test_blank_creds_raise(self):
        with pytest.raises(RuntimeError, match="AWS_ACCESS_KEY_ID"):
            load_settings(base_env(AWS_ACCESS_KEY_ID="  "))


class TestEnvBool:
    def test_unset_returns_default(self):
        assert env_bool("NOPE", {}, True) is True
        assert env_bool("NOPE", {}, False) is False

    def test_blank_returns_default(self):
        assert env_bool("NOPE", {"NOPE": ""}, True) is True


def test_settings_frozen():
    s = load_settings(base_env())
    with pytest.raises(Exception):
        s.region = "other"  # type: ignore[misc]
