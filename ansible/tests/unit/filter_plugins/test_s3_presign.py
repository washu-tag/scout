#!/usr/bin/env python3
"""
Unit tests for the s3_presign filter plugin.

Run from ansible/: pytest tests/unit/filter_plugins/test_s3_presign.py -v
"""

import datetime
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

filter_plugins_path = Path(__file__).parent.parent.parent.parent / "filter_plugins"
sys.path.insert(0, str(filter_plugins_path))

import pytest
from s3_presign import s3_presigned_url

# The worked example from the AWS S3 docs, "Authenticating Requests: Using Query
# Parameters (AWS Signature Version 4)".
AWS_EXAMPLE = dict(
    uri="s3://examplebucket/test.txt",
    region="us-east-1",
    access_key_id="AKIAIOSFODNN7EXAMPLE",
    secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    expires=86400,
    now=datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc),
)


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


class TestS3PresignedUrl:
    def test_matches_aws_documented_signature(self):
        url = s3_presigned_url(**AWS_EXAMPLE)
        assert url.startswith("https://examplebucket.s3.amazonaws.com/test.txt?")
        q = _query(url)
        assert (
            q["X-Amz-Credential"]
            == "AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request"
        )
        assert q["X-Amz-Date"] == "20130524T000000Z"
        assert q["X-Amz-Expires"] == "86400"
        assert q["X-Amz-SignedHeaders"] == "host"
        assert (
            q["X-Amz-Signature"]
            == "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"
        )

    def test_regional_virtual_hosted_endpoint(self):
        url = s3_presigned_url(**{**AWS_EXAMPLE, "region": "us-east-2"})
        assert url.startswith(
            "https://examplebucket.s3.us-east-2.amazonaws.com/test.txt?"
        )

    def test_dotted_bucket_uses_path_style(self):
        url = s3_presigned_url(
            **{**AWS_EXAMPLE, "uri": "s3://my.bucket/a/b.jar", "region": "us-east-2"}
        )
        assert url.startswith("https://s3.us-east-2.amazonaws.com/my.bucket/a/b.jar?")

    def test_key_is_uri_encoded_but_keeps_slashes(self):
        url = s3_presigned_url(
            **{**AWS_EXAMPLE, "uri": "s3://examplebucket/plugins/my plugin+1.jar"}
        )
        assert urlsplit(url).path == "/plugins/my%20plugin%2B1.jar"

    def test_session_token_is_signed_into_the_query(self):
        with_token = s3_presigned_url(**{**AWS_EXAMPLE, "session_token": "tok/en=="})
        assert _query(with_token)["X-Amz-Security-Token"] == "tok/en=="
        assert (
            _query(with_token)["X-Amz-Signature"]
            != _query(s3_presigned_url(**AWS_EXAMPLE))["X-Amz-Signature"]
        )

    @pytest.mark.parametrize(
        "uri", ["https://bucket/key", "s3://bucket", "s3://bucket/", "s3:///key"]
    )
    def test_rejects_malformed_uris(self, uri):
        with pytest.raises(ValueError):
            s3_presigned_url(**{**AWS_EXAMPLE, "uri": uri})

    def test_requires_credentials(self):
        with pytest.raises(ValueError):
            s3_presigned_url(**{**AWS_EXAMPLE, "secret_access_key": ""})
