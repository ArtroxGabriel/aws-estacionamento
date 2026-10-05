"""Configuration loading and validation for the Python OCR Worker.

The Config_Loader (Req 14, Req 15) reads all configuration from environment
variables at startup and fails fast — before the polling loop begins — when a
required variable is missing/empty or a URL-shaped variable is malformed. It
never embeds secrets in code and never emits credential values in logs
(``redact`` provides a fixed marker for that purpose).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

__all__ = ["Config", "ConfigError", "load_config", "redact", "REDACTED"]

# Fixed redaction marker substituted for any secret value in log output (Req 14.5).
REDACTED = "***REDACTED***"

# Required environment variables that must be present and non-empty (Req 14.2).
# Static AWS credentials are optional: without them boto3 falls back to its
# default credential chain (e.g. the EC2 instance profile / LabRole in AWS
# Academy). When supplied, the key id and secret must come as a pair.
_REQUIRED_VARS = (
    "AWS_REGION",
    "SQS_QUEUE_URL",
    "DATABASE_URL",
    "REDIS_URL",
    "S3_BUCKET_NAME",
    "DYNAMODB_TABLE_NAME",
)

# Variables that must parse as URLs / connection strings when present.
# Each maps to the set of acceptable URL schemes.
_HTTP_SCHEMES = frozenset({"http", "https"})
_DB_SCHEMES = frozenset({"postgres", "postgresql"})
_REDIS_SCHEMES = frozenset({"redis", "rediss"})

# OCR engines: Tesseract everywhere; Amazon Rekognition only on real AWS (the
# local emulator does not implement it), always with Tesseract as fallback.
_OCR_ENGINES = frozenset({"tesseract", "rekognition"})


@dataclass(frozen=True)
class Config:
    """Validated, immutable runtime configuration.

    ``aws_endpoint_url`` is ``None`` when unset/empty, which directs the AWS
    clients to the default AWS endpoints for ``aws_region`` (Req 15.2).
    """

    aws_endpoint_url: str | None  # None => default AWS endpoints
    aws_region: str
    aws_access_key_id: str | None  # None => default boto3 credential chain
    aws_secret_access_key: str | None
    aws_session_token: str | None  # temporary credentials (AWS Academy)
    sqs_queue_url: str
    database_url: str
    redis_url: str
    s3_bucket_name: str
    dynamodb_table_name: str
    ocr_engine: str = "tesseract"  # "tesseract" | "rekognition" (AWS only)


class ConfigError(Exception):
    """Raised at startup when configuration is missing/empty or malformed.

    Collects *every* problem rather than failing on the first one, so the
    operator sees the full list of missing variables (Req 14.2, Property 10)
    and every invalid URL/connection string (Req 14.4, 15.3, 15.4) at once.
    """

    def __init__(
        self,
        missing: list[str] | None = None,
        invalid: list[str] | None = None,
    ) -> None:
        self.missing: list[str] = list(missing or [])
        self.invalid: list[str] = list(invalid or [])
        parts: list[str] = []
        if self.missing:
            parts.append("missing/empty required variables: " + ", ".join(self.missing))
        if self.invalid:
            parts.append("malformed variables: " + ", ".join(self.invalid))
        message = "invalid worker configuration"
        if parts:
            message = f"{message}: {'; '.join(parts)}"
        super().__init__(message)


def redact(value: str) -> str:
    """Return a fixed redaction marker in place of a secret value (Req 14.5).

    Used whenever credentials (``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``,
    or credentials embedded in ``DATABASE_URL`` / ``REDIS_URL``) might otherwise
    appear in log output. The marker is a constant, so the original secret
    substring can never leak through.
    """

    return REDACTED


def _clean(raw: str | None) -> str:
    """Normalize an env value to a stripped string ("" when absent)."""

    if raw is None:
        return ""
    return raw.strip()


def _has_scheme(value: str, allowed: frozenset[str]) -> bool:
    """True iff ``value`` parses as a URL with a host and an allowed scheme."""

    try:
        parsed = urlparse(value)
    except ValueError, TypeError:
        return False
    if parsed.scheme.lower() not in allowed:
        return False
    # A well-formed URL/connection string must carry a network location.
    return bool(parsed.netloc)


def load_config(env: Mapping[str, str]) -> Config:
    """Read and validate configuration from ``env`` (Req 14.1).

    Reads every variable listed in Req 14.1. ``AWS_ENDPOINT_URL`` is optional
    (absent/empty => default AWS endpoints, Req 14.3/15.2). Raises
    :class:`ConfigError` listing every missing/empty required variable
    (Req 14.2) and every malformed URL/connection string (Req 14.4, 15.3), and
    aborts when both the endpoint and the region are absent (Req 15.4). On
    success the loop may start; on failure the caller must exit non-zero.
    """

    # Read all Req 14.1 variables.
    endpoint = _clean(env.get("AWS_ENDPOINT_URL"))
    region = _clean(env.get("AWS_REGION"))
    access_key = _clean(env.get("AWS_ACCESS_KEY_ID"))
    secret_key = _clean(env.get("AWS_SECRET_ACCESS_KEY"))
    session_token = _clean(env.get("AWS_SESSION_TOKEN"))
    sqs_queue_url = _clean(env.get("SQS_QUEUE_URL"))
    database_url = _clean(env.get("DATABASE_URL"))
    redis_url = _clean(env.get("REDIS_URL"))
    s3_bucket_name = _clean(env.get("S3_BUCKET_NAME"))
    dynamodb_table_name = _clean(env.get("DYNAMODB_TABLE_NAME"))

    values = {
        "AWS_REGION": region,
        "SQS_QUEUE_URL": sqs_queue_url,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "S3_BUCKET_NAME": s3_bucket_name,
        "DYNAMODB_TABLE_NAME": dynamodb_table_name,
    }

    # Collect every missing/empty required variable (not first-fail, Req 14.2).
    missing = [name for name in _REQUIRED_VARS if not values[name]]
    # Half a key pair is a misconfiguration: report the absent half.
    if access_key and not secret_key:
        missing.append("AWS_SECRET_ACCESS_KEY")
    if secret_key and not access_key:
        missing.append("AWS_ACCESS_KEY_ID")

    # Collect every malformed URL/connection string among the present values
    # (Req 14.4, 15.3). Only validate a value when it is non-empty so a missing
    # var is reported once (as missing) rather than twice.
    invalid: list[str] = []
    if sqs_queue_url and not _has_scheme(sqs_queue_url, _HTTP_SCHEMES):
        invalid.append("SQS_QUEUE_URL")
    if database_url and not _has_scheme(database_url, _DB_SCHEMES):
        invalid.append("DATABASE_URL")
    if redis_url and not _has_scheme(redis_url, _REDIS_SCHEMES):
        invalid.append("REDIS_URL")
    # AWS_ENDPOINT_URL is optional, but when present it must be http/https
    # (Req 15.3).
    if endpoint and not _has_scheme(endpoint, _HTTP_SCHEMES):
        invalid.append("AWS_ENDPOINT_URL")

    ocr_engine = _clean(env.get("OCR_ENGINE")).lower() or "tesseract"
    if ocr_engine not in _OCR_ENGINES:
        invalid.append("OCR_ENGINE")

    if missing or invalid:
        raise ConfigError(missing=missing, invalid=invalid)

    return Config(
        aws_endpoint_url=endpoint or None,
        aws_region=region,
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        aws_session_token=session_token or None,
        sqs_queue_url=sqs_queue_url,
        database_url=database_url,
        redis_url=redis_url,
        s3_bucket_name=s3_bucket_name,
        dynamodb_table_name=dynamodb_table_name,
        ocr_engine=ocr_engine,
    )
