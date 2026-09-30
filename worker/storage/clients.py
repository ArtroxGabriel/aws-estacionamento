"""AWS client factory for the Python OCR Worker.

This module is the single place that builds the boto3 session and the S3, SQS,
and DynamoDB clients, applying the *identical* endpoint resolution to every
client (Req 15.1, 15.5). Centralizing construction here guarantees no service
client can drift onto a different endpoint than the others: they all resolve
``endpoint_url`` from ``cfg.aws_endpoint_url or None``.

Endpoint resolution (Req 15.1, 15.2):

- ``cfg.aws_endpoint_url`` is a non-empty URL (e.g. Floci at
  ``http://localhost:4566``) => every client targets that endpoint.
- ``cfg.aws_endpoint_url`` is ``None`` => ``endpoint_url=None`` directs every
  client to the default AWS endpoints for ``cfg.aws_region``.

Region and credentials always come from ``cfg`` so the Worker runs identically
against Floci and real AWS Academy, driven entirely by configuration.
"""

from __future__ import annotations

import boto3

from config import Config

__all__ = [
    "build_boto3_session",
    "s3_client",
    "sqs_client",
    "dynamodb_client",
]


def build_boto3_session(cfg: Config) -> boto3.Session:
    """Build a boto3 session using the region and credentials from ``cfg``.

    The session carries the credentials and region so that every client
    created from it (S3, SQS, DynamoDB) shares the same authentication and
    regional configuration (Req 15.2). The session token is forwarded for the
    temporary credentials AWS Academy issues; when no static keys are
    configured (all ``None``) boto3 resolves credentials through its default
    chain, e.g. the EC2 instance profile. The endpoint is *not* set on the
    session; it is applied per client so the resolution is identical and
    explicit for each service (Req 15.5).
    """

    return boto3.Session(
        aws_access_key_id=cfg.aws_access_key_id,
        aws_secret_access_key=cfg.aws_secret_access_key,
        aws_session_token=cfg.aws_session_token,
        region_name=cfg.aws_region,
    )


def s3_client(cfg: Config):
    """Build the S3 client with the shared endpoint resolution (Req 15.1, 15.5).

    ``endpoint_url=cfg.aws_endpoint_url or None`` — a non-empty endpoint targets
    the emulator/custom endpoint, ``None`` falls back to the default AWS
    endpoints for the configured region.
    """

    session = build_boto3_session(cfg)
    return session.client("s3", endpoint_url=cfg.aws_endpoint_url or None)


def sqs_client(cfg: Config):
    """Build the SQS client with the shared endpoint resolution (Req 15.1, 15.5).

    Uses the same ``endpoint_url=cfg.aws_endpoint_url or None`` resolution as
    every other client so no client targets a different endpoint.
    """

    session = build_boto3_session(cfg)
    return session.client("sqs", endpoint_url=cfg.aws_endpoint_url or None)


def dynamodb_client(cfg: Config):
    """Build the DynamoDB client with the shared endpoint resolution (Req 15.1, 15.5).

    Uses the same ``endpoint_url=cfg.aws_endpoint_url or None`` resolution as
    every other client so no client targets a different endpoint.
    """

    session = build_boto3_session(cfg)
    return session.client("dynamodb", endpoint_url=cfg.aws_endpoint_url or None)
