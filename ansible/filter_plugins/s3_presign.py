#!/usr/bin/env python3
"""Custom Jinja2 filters for ``s3://bucket/key`` URIs.

``s3_presigned_url`` turns the URI plus static credentials into a short-lived
HTTPS GET URL (AWS SigV4, query auth), computed entirely on the controller. The
host that downloads the object then needs no AWS CLI, boto3, or credentials.

``s3_object_path`` gives ``bucket/key``, encoded for a URL path, for mirroring
the object at the same path elsewhere.
"""

import datetime
import hashlib
import hmac
from urllib.parse import quote, urlsplit


def _hmac(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _parse_s3_uri(uri):
    parts = urlsplit(uri)
    key = parts.path.lstrip("/")
    if parts.scheme != "s3" or not parts.netloc or not key:
        raise ValueError("expected s3://bucket/key, got %r" % uri)
    return parts.netloc, key


def _encode_path(path):
    return quote(path, safe="/~")


def s3_object_path(uri):
    """``bucket/key`` of ``uri``, URI-encoded except for slashes."""
    return _encode_path("%s/%s" % _parse_s3_uri(uri))


def s3_presigned_url(
    uri,
    region,
    access_key_id,
    secret_access_key,
    expires=300,
    now=None,
):
    """Presigned GET URL for ``uri`` valid for ``expires`` seconds.

    Virtual-hosted style, except for bucket names containing dots, which break
    the wildcard TLS certificate and so use path style.
    """
    if not (region and access_key_id and secret_access_key):
        raise ValueError("s3_presigned_url: region and credentials are required")
    bucket, key = _parse_s3_uri(uri)

    if "." in bucket:
        host = "s3.%s.amazonaws.com" % region
        path = "/%s/%s" % (bucket, key)
    else:
        host = (
            "%s.s3.amazonaws.com" % bucket
            if region == "us-east-1"
            else "%s.s3.%s.amazonaws.com" % (bucket, region)
        )
        path = "/" + key
    canonical_uri = _encode_path(path)

    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    scope = "%s/%s/s3/aws4_request" % (date_stamp, region)

    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": "%s/%s" % (access_key_id, scope),
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(int(expires)),
        "X-Amz-SignedHeaders": "host",
    }
    query = "&".join(
        "%s=%s" % (quote(k, safe="~"), quote(v, safe="~"))
        for k, v in sorted(params.items())
    )

    canonical_request = "\n".join(
        ["GET", canonical_uri, query, "host:" + host, "", "host", "UNSIGNED-PAYLOAD"]
    )
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _hmac(
        _hmac(
            _hmac(
                _hmac(("AWS4" + secret_access_key).encode("utf-8"), date_stamp), region
            ),
            "s3",
        ),
        "aws4_request",
    )
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return "https://%s%s?%s&X-Amz-Signature=%s" % (
        host,
        canonical_uri,
        query,
        signature,
    )


class FilterModule(object):
    """Ansible filter plugin class."""

    def filters(self):
        return {
            "s3_object_path": s3_object_path,
            "s3_presigned_url": s3_presigned_url,
        }
