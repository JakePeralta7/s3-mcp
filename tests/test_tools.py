import base64
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

import s3_mcp.server as server
from s3_mcp.server import (
    do_copy_object,
    do_create_bucket,
    do_delete_bucket,
    do_delete_object,
    do_get_object,
    do_list_buckets,
    do_list_objects,
    do_presign_get,
    do_presign_put,
    do_put_object,
    do_stat_object,
)


class FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


@pytest.fixture
def client():
    c = MagicMock()
    server.set_client(c, region="eu-west-1")
    yield c
    server.set_client(None)


NOW = datetime(2026, 8, 21, tzinfo=UTC)


class TestListBuckets:
    def test_shape(self, client):
        client.list_buckets.return_value = {
            "Buckets": [
                {"Name": "a", "CreationDate": NOW},
                {"Name": "b"},
            ]
        }
        result = do_list_buckets(client)
        assert result == [
            {"name": "a", "creation_date": NOW.isoformat()},
            {"name": "b"},
        ]

    def test_client_error_wrapped(self, client):
        client.list_buckets.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "ListBuckets"
        )
        with pytest.raises(RuntimeError, match="AccessDenied: nope"):
            do_list_buckets(client)


class TestListObjects:
    def test_shape(self, client):
        client.list_objects_v2.return_value = {
            "Contents": [{"Key": "dir/a.txt", "Size": 3, "LastModified": NOW, "ETag": '"abc"'}],
            "IsTruncated": True,
        }
        result = do_list_objects(client, "bucket", prefix="dir/", max_keys=10)
        assert result["objects"] == [
            {
                "key": "dir/a.txt",
                "size": 3,
                "last_modified": NOW.isoformat(),
                "etag": '"abc"',
            }
        ]
        assert result["truncated"] is True
        assert result["key_count"] == 1
        kwargs = client.list_objects_v2.call_args.kwargs
        assert kwargs == {"Bucket": "bucket", "Prefix": "dir/", "MaxKeys": 10}

    def test_max_keys_clamped(self, client):
        client.list_objects_v2.return_value = {}
        do_list_objects(client, "bucket", max_keys=5000)
        assert client.list_objects_v2.call_args.kwargs["MaxKeys"] == 1000
        do_list_objects(client, "bucket", max_keys=0)
        assert client.list_objects_v2.call_args.kwargs["MaxKeys"] == 1


class TestGetObject:
    def test_utf8_text(self, client):
        client.get_object.return_value = {
            "Body": FakeBody("héllo".encode()),
            "ContentType": "text/plain",
        }
        result = do_get_object(client, "bucket", "key")
        assert result == {
            "encoding": "utf-8",
            "content_type": "text/plain",
            "size": 6,
            "text": "héllo",
        }

    def test_binary_base64(self, client):
        raw = bytes(range(256))
        client.get_object.return_value = {
            "Body": FakeBody(raw),
            "ContentType": "application/octet-stream",
        }
        result = do_get_object(client, "bucket", "key")
        assert result["encoding"] == "base64"
        assert base64.b64decode(result["base64"]) == raw
        assert result["size"] == 256
        assert "text" not in result

    def test_missing_content_type_defaults(self, client):
        client.get_object.return_value = {"Body": FakeBody(b"x")}
        result = do_get_object(client, "bucket", "key")
        assert result["content_type"] == "application/octet-stream"


class TestStatObject:
    def test_shape(self, client):
        client.head_object.return_value = {
            "ContentLength": 42,
            "ContentType": "application/json",
            "ETag": '"e"',
            "LastModified": NOW,
        }
        result = do_stat_object(client, "bucket", "key")
        assert result == {
            "bucket": "bucket",
            "key": "key",
            "size": 42,
            "etag": '"e"',
            "last_modified": NOW.isoformat(),
            "content_type": "application/json",
        }


class TestPresign:
    def test_get(self, client):
        client.generate_presigned_url.return_value = "https://signed"
        url = do_presign_get(client, "bucket", "key", 60)
        assert url == "https://signed"
        args, kwargs = client.generate_presigned_url.call_args
        assert args[0] == "get_object"
        assert kwargs["Params"] == {"Bucket": "bucket", "Key": "key"}
        assert kwargs["ExpiresIn"] == 60

    def test_put(self, client):
        client.generate_presigned_url.return_value = "https://signed"
        do_presign_put(client, "bucket", "key", 30)
        assert client.generate_presigned_url.call_args.args[0] == "put_object"

    @pytest.mark.parametrize("expires_s", [0, -5, 604801])
    def test_out_of_range_raises(self, client, expires_s):
        with pytest.raises(RuntimeError, match="expires_s"):
            do_presign_get(client, "bucket", "key", expires_s)


class TestPutObject:
    def test_text_body_encoded_utf8(self, client):
        result = do_put_object(client, "bucket", "key", "héllo")
        kwargs = client.put_object.call_args.kwargs
        assert kwargs["Body"] == "héllo".encode()
        assert result["size"] == 6

    def test_base64_decoded(self, client):
        raw = b"\x00\x01\xff"
        body = base64.b64encode(raw).decode()
        result = do_put_object(client, "bucket", "key", body, base64=True)
        assert client.put_object.call_args.kwargs["Body"] == raw
        assert result["size"] == 3

    def test_invalid_base64_raises_runtime_error(self, client):
        with pytest.raises(RuntimeError):
            do_put_object(client, "bucket", "key", "!!!not-b64!!!", base64=True)


class TestDeleteAndCopy:
    def test_delete_object(self, client):
        result = do_delete_object(client, "bucket", "key")
        client.delete_object.assert_called_once_with(Bucket="bucket", Key="key")
        assert result["deleted"] is True

    def test_delete_bucket(self, client):
        result = do_delete_bucket(client, "bucket")
        client.delete_bucket.assert_called_once_with(Bucket="bucket")
        assert result["deleted"] is True

    def test_copy_object_source_mapping(self, client):
        result = do_copy_object(client, "src", "a.txt", "dst", "b.txt")
        client.copy_object.assert_called_once_with(
            Bucket="dst", Key="b.txt", CopySource={"Bucket": "src", "Key": "a.txt"}
        )
        assert result["copied"] is True


class TestCreateBucket:
    def test_us_east_1_no_constraint(self, client):
        do_create_bucket(client, "bucket", "us-east-1")
        client.create_bucket.assert_called_once_with(Bucket="bucket")

    def test_other_region_adds_constraint(self, client):
        do_create_bucket(client, "bucket", "eu-west-1")
        client.create_bucket.assert_called_once_with(
            Bucket="bucket",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
        )


class TestErrorWrapping:
    def test_boto_core_error_wrapped(self, client):
        from botocore.exceptions import EndpointConnectionError

        client.list_objects_v2.side_effect = EndpointConnectionError(endpoint_url="http://x")
        with pytest.raises(RuntimeError):
            do_list_objects(client, "bucket")

    def test_os_error_wrapped(self, client):
        client.list_buckets.side_effect = OSError("connection reset")
        with pytest.raises(RuntimeError, match="connection reset"):
            do_list_buckets(client)

    def test_runtime_error_not_double_wrapped_message_kept(self, client):
        client.list_buckets.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "ListBuckets"
        )
        with pytest.raises(RuntimeError) as exc_info:
            do_list_buckets(client)
        assert not isinstance(exc_info.value.__cause__, RuntimeError)
