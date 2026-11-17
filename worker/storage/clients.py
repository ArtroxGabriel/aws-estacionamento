"""AWS client factory for the Python OCR Worker.

This module is the single place that builds the boto3 session and the S3 and
DynamoDB clients. Each service has its own endpoint, so the storage can be a
local S3-compatible container (MinIO/RustFS) while the audit table lives in
DynamoDB Local, and both can independently fall back to the real AWS:

- ``cfg.s3_endpoint_url`` / ``cfg.dynamodb_endpoint_url`` set => that client
  targets the given endpoint (e.g. ``http://rustfs:9000``).
- ``None`` => ``endpoint_url=None`` directs the client to the default AWS
  endpoint for ``cfg.aws_region``.

Region and credentials always come from ``cfg``, so the Worker runs identically
against local containers and real AWS, driven entirely by configuration.
"""

from __future__ import annotations

import boto3
from botocore.config import Config as BotoConfig

from config import Config

__all__ = [
    "build_boto3_session",
    "s3_client",
    "dynamodb_client",
]


def build_boto3_session(cfg: Config) -> boto3.Session:
    """Build a boto3 session using the region and credentials from ``cfg``.

    The session carries the credentials and region so that every client created
    from it shares the same authentication and regional configuration. The
    session token is forwarded for the temporary credentials AWS Academy issues;
    when no static keys are configured (all ``None``) boto3 resolves credentials
    through its default chain, e.g. the EC2 instance profile. The endpoint is
    *not* set on the session; it is applied per client.
    """

    return boto3.Session(
        aws_access_key_id=cfg.aws_access_key_id,
        aws_secret_access_key=cfg.aws_secret_access_key,
        aws_session_token=cfg.aws_session_token,
        region_name=cfg.aws_region,
    )


def s3_client(cfg: Config):
    """Build the S3 client for ``cfg.s3_endpoint_url`` (``None`` => real AWS).

    Path-style addressing (``host/bucket/key``): a local S3-compatible storage
    does not resolve ``bucket.host``.
    """

    session = build_boto3_session(cfg)
    return session.client(
        "s3",
        endpoint_url=cfg.s3_endpoint_url,
        config=BotoConfig(s3={"addressing_style": "path"}),
    )


def dynamodb_client(cfg: Config):
    """Build the DynamoDB client for ``cfg.dynamodb_endpoint_url`` (``None`` => real AWS)."""

    session = build_boto3_session(cfg)
    return session.client("dynamodb", endpoint_url=cfg.dynamodb_endpoint_url)
