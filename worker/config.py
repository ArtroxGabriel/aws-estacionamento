"""Configuration loading and validation for the Python OCR Worker.

The Config_Loader reads all configuration from environment variables at startup
and fails fast — before the consumer starts — when a required variable is
missing/empty or a URL-shaped variable is malformed. It never embeds secrets in
code and never emits credential values in logs (``redact`` and
``redact_url`` hide them).

Each backing service has its own endpoint: the S3-compatible storage
(``S3_ENDPOINT_URL``) and DynamoDB (``DYNAMODB_ENDPOINT_URL``) may point at local
containers, while an empty value means the real AWS endpoint.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

__all__ = [
    "Config",
    "ConfigError",
    "DEFAULT_QUEUE_NAME",
    "REDACTED",
    "load_config",
    "redact",
    "redact_url",
]

# Fixed redaction marker substituted for any secret value in log output.
REDACTED = "***REDACTED***"

DEFAULT_QUEUE_NAME = "ocr-processamento-fila"

# Required environment variables that must be present and non-empty.
# Static AWS credentials are optional: without them boto3 falls back to its
# default credential chain (e.g. the EC2 instance profile / LabRole in AWS
# Academy). When supplied, the key id and secret must come as a pair.
_REQUIRED_VARS = (
    "AWS_REGION",
    "RABBITMQ_URL",
    "DATABASE_URL",
    "S3_BUCKET_NAME",
    "DYNAMODB_TABLE_NAME",
)

# Variables that must parse as URLs / connection strings when present.
# Each maps to the set of acceptable URL schemes.
_HTTP_SCHEMES = frozenset({"http", "https"})
_DB_SCHEMES = frozenset({"postgres", "postgresql"})
_AMQP_SCHEMES = frozenset({"amqp", "amqps"})


@dataclass(frozen=True)
class Config:
    """Validated, immutable runtime configuration.

    ``s3_endpoint_url`` and ``dynamodb_endpoint_url`` are ``None`` when
    unset/empty, which directs the client to the default AWS endpoint for
    ``aws_region``.
    """

    aws_region: str
    aws_access_key_id: str | None  # None => default boto3 credential chain
    aws_secret_access_key: str | None
    aws_session_token: str | None  # temporary credentials (AWS Academy)
    s3_endpoint_url: str | None  # None => real AWS S3
    s3_bucket_name: str
    dynamodb_endpoint_url: str | None  # None => real AWS DynamoDB
    dynamodb_table_name: str
    rabbitmq_url: str
    rabbitmq_queue_name: str
    database_url: str
    # Credentials used only by the DynamoDB client (real AWS, e.g. AWS Academy)
    # while S3 keeps the ones above (local RustFS). None => same as above.
    dynamodb_access_key_id: str | None = None
    dynamodb_secret_access_key: str | None = None
    dynamodb_session_token: str | None = None

    @property
    def retry_queue_name(self) -> str:
        """Holding queue: messages wait here (TTL) before returning to the main queue."""
        return f"{self.rabbitmq_queue_name}-retry"

    @property
    def dlq_name(self) -> str:
        """Dead-letter queue for messages the Worker gave up on."""
        return f"{self.rabbitmq_queue_name}-dlq"


class ConfigError(Exception):
    """Raised at startup when configuration is missing/empty or malformed.

    Collects *every* problem rather than failing on the first one, so the
    operator sees the full list of missing variables and every invalid
    URL/connection string at once.
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
    """Return a fixed redaction marker in place of a secret value.

    The marker is a constant, so the original secret substring can never leak
    through.
    """

    return REDACTED


def redact_url(url: str) -> str:
    """Describe a connection URL without its credentials (``host:port/vhost``)."""

    try:
        parsed = urlparse(url)
        host = parsed.hostname or "?"
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return REDACTED
    return f"{host}{port}{parsed.path}"


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
    """Read and validate configuration from ``env``.

    ``S3_ENDPOINT_URL`` and ``DYNAMODB_ENDPOINT_URL`` are optional (absent/empty
    => real AWS endpoints). Raises :class:`ConfigError` listing every
    missing/empty required variable and every malformed URL/connection string.
    On success the consumer may start; on failure the caller must exit non-zero.
    """

    region = _clean(env.get("AWS_REGION"))
    access_key = _clean(env.get("AWS_ACCESS_KEY_ID"))
    secret_key = _clean(env.get("AWS_SECRET_ACCESS_KEY"))
    session_token = _clean(env.get("AWS_SESSION_TOKEN"))
    rabbitmq_url = _clean(env.get("RABBITMQ_URL"))
    queue_name = _clean(env.get("RABBITMQ_QUEUE_NAME")) or DEFAULT_QUEUE_NAME
    database_url = _clean(env.get("DATABASE_URL"))
    s3_endpoint = _clean(env.get("S3_ENDPOINT_URL"))
    s3_bucket_name = _clean(env.get("S3_BUCKET_NAME"))
    dynamodb_endpoint = _clean(env.get("DYNAMODB_ENDPOINT_URL"))
    dynamodb_table_name = _clean(env.get("DYNAMODB_TABLE_NAME"))
    dynamodb_access_key = _clean(env.get("DYNAMODB_ACCESS_KEY_ID"))
    dynamodb_secret_key = _clean(env.get("DYNAMODB_SECRET_ACCESS_KEY"))
    dynamodb_session_token = _clean(env.get("DYNAMODB_SESSION_TOKEN"))

    values = {
        "AWS_REGION": region,
        "RABBITMQ_URL": rabbitmq_url,
        "DATABASE_URL": database_url,
        "S3_BUCKET_NAME": s3_bucket_name,
        "DYNAMODB_TABLE_NAME": dynamodb_table_name,
    }

    # Collect every missing/empty required variable (not first-fail).
    missing = [name for name in _REQUIRED_VARS if not values[name]]
    # Half a key pair is a misconfiguration: report the absent half.
    if access_key and not secret_key:
        missing.append("AWS_SECRET_ACCESS_KEY")
    if secret_key and not access_key:
        missing.append("AWS_ACCESS_KEY_ID")
    if dynamodb_access_key and not dynamodb_secret_key:
        missing.append("DYNAMODB_SECRET_ACCESS_KEY")
    if dynamodb_secret_key and not dynamodb_access_key:
        missing.append("DYNAMODB_ACCESS_KEY_ID")

    # Collect every malformed URL/connection string among the present values.
    # Only validate a value when it is non-empty so a missing var is reported
    # once (as missing) rather than twice.
    invalid: list[str] = []
    if rabbitmq_url and not _has_scheme(rabbitmq_url, _AMQP_SCHEMES):
        invalid.append("RABBITMQ_URL")
    if database_url and not _has_scheme(database_url, _DB_SCHEMES):
        invalid.append("DATABASE_URL")
    if s3_endpoint and not _has_scheme(s3_endpoint, _HTTP_SCHEMES):
        invalid.append("S3_ENDPOINT_URL")
    if dynamodb_endpoint and not _has_scheme(dynamodb_endpoint, _HTTP_SCHEMES):
        invalid.append("DYNAMODB_ENDPOINT_URL")

    if missing or invalid:
        raise ConfigError(missing=missing, invalid=invalid)

    return Config(
        aws_region=region,
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        aws_session_token=session_token or None,
        s3_endpoint_url=s3_endpoint or None,
        s3_bucket_name=s3_bucket_name,
        dynamodb_endpoint_url=dynamodb_endpoint or None,
        dynamodb_table_name=dynamodb_table_name,
        rabbitmq_url=rabbitmq_url,
        rabbitmq_queue_name=queue_name,
        database_url=database_url,
        dynamodb_access_key_id=dynamodb_access_key or None,
        dynamodb_secret_access_key=dynamodb_secret_key or None,
        dynamodb_session_token=dynamodb_session_token or None,
    )
