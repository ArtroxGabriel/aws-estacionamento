"""Config_Loader and AWS client factory tests.

Feature: python-ocr-worker, Property 10: Missing-variable reporting is complete.
Feature: python-ocr-worker, Property 12: Each service client resolves its own endpoint.
"""

from __future__ import annotations

import pytest
from fakes import BASE_ENV
from hypothesis import given
from hypothesis import strategies as st

from config import REDACTED, ConfigError, load_config, redact_url
from storage import clients

ALWAYS_REQUIRED = (
    "AWS_REGION",
    "RABBITMQ_URL",
    "DATABASE_URL",
    "S3_BUCKET_NAME",
    "DYNAMODB_TABLE_NAME",
)


def env_without(*names: str) -> dict[str, str]:
    return {k: v for k, v in BASE_ENV.items() if k not in names}


def test_static_credentials_are_optional():
    """AWS Academy EC2 instances authenticate through the instance profile
    (LabRole), so static keys must not be mandatory."""
    cfg = load_config(env_without("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"))

    assert cfg.aws_access_key_id is None
    assert cfg.aws_secret_access_key is None
    assert cfg.aws_session_token is None


@pytest.mark.parametrize("present", ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
def test_half_a_key_pair_is_rejected(present):
    absent = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"} - {present}
    with pytest.raises(ConfigError) as err:
        load_config(env_without(*absent))
    assert err.value.missing == sorted(absent)


def test_session_token_is_read():
    cfg = load_config({**BASE_ENV, "AWS_SESSION_TOKEN": "tok"})
    assert cfg.aws_session_token == "tok"


def test_session_token_reaches_boto3():
    """AWS Academy hands out temporary credentials; without the session token
    every AWS call is rejected."""
    cfg = load_config({**BASE_ENV, "AWS_SESSION_TOKEN": "tok"})

    creds = clients.build_boto3_session(cfg).get_credentials()

    assert (creds.access_key, creds.secret_key, creds.token) == ("test", "test", "tok")


@given(st.sets(st.sampled_from(ALWAYS_REQUIRED), min_size=1))
def test_every_missing_variable_is_reported(missing):
    with pytest.raises(ConfigError) as err:
        load_config(env_without(*missing))
    assert set(err.value.missing) == missing


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("S3_ENDPOINT_URL", "localhost:9000"),
        ("DYNAMODB_ENDPOINT_URL", "dynamodb-local:8000"),
        ("DATABASE_URL", "mysql://u:p@h/db"),
        ("RABBITMQ_URL", "http://localhost:5672"),
        ("RABBITMQ_URL", "rabbitmq:5672"),
    ],
)
def test_malformed_urls_are_rejected(name, value):
    with pytest.raises(ConfigError) as err:
        load_config({**BASE_ENV, name: value})
    assert err.value.invalid == [name]


@pytest.mark.parametrize("url", ["amqp://u:p@rabbitmq:5672/", "amqps://u:p@broker.example/vh"])
def test_amqp_and_amqps_are_accepted(url):
    assert load_config({**BASE_ENV, "RABBITMQ_URL": url}).rabbitmq_url == url


def test_queue_names_derive_from_the_main_queue():
    cfg = load_config(BASE_ENV)

    assert cfg.rabbitmq_queue_name == "ocr-processamento-fila"
    assert cfg.retry_queue_name == "ocr-processamento-fila-retry"
    assert cfg.dlq_name == "ocr-processamento-fila-dlq"

    renamed = load_config({**BASE_ENV, "RABBITMQ_QUEUE_NAME": "fila-x"})
    assert (renamed.retry_queue_name, renamed.dlq_name) == ("fila-x-retry", "fila-x-dlq")


def test_broker_credentials_never_reach_the_logs():
    cfg = load_config({**BASE_ENV, "RABBITMQ_URL": "amqp://admin:s3cr3t@rabbitmq:5672/vh"})

    described = redact_url(cfg.rabbitmq_url)

    assert described == "rabbitmq:5672/vh"
    assert "s3cr3t" not in described and "admin" not in described
    assert redact_url("amqp://u:p@h:notaport/") == REDACTED


def test_each_service_has_its_own_endpoint():
    cfg = load_config(BASE_ENV)

    assert clients.s3_client(cfg).meta.endpoint_url == "http://localhost:9000"
    assert clients.dynamodb_client(cfg).meta.endpoint_url == "http://localhost:8000"


def test_s3_uses_path_style_addressing_for_local_storage():
    """A local S3-compatible storage does not resolve ``bucket.host``."""
    cfg = load_config(BASE_ENV)

    assert clients.s3_client(cfg).meta.config.s3["addressing_style"] == "path"


def test_services_fall_back_to_real_aws_independently(monkeypatch):
    # boto3 itself honors AWS_ENDPOINT_URL from the process environment.
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    only_s3_local = load_config({**BASE_ENV, "DYNAMODB_ENDPOINT_URL": ""})

    assert clients.s3_client(only_s3_local).meta.endpoint_url == "http://localhost:9000"
    dynamodb = clients.dynamodb_client(only_s3_local)
    assert dynamodb.meta.endpoint_url.endswith(".amazonaws.com")
    assert dynamodb.meta.region_name == "us-east-1"

    only_dynamodb_local = load_config({**BASE_ENV, "S3_ENDPOINT_URL": ""})
    assert clients.s3_client(only_dynamodb_local).meta.endpoint_url.endswith(".amazonaws.com")


def test_removed_settings_are_ignored_not_required():
    """Leftovers of the SQS/Rekognition/Redis era must not stop the Worker."""
    cfg = load_config(
        {
            **BASE_ENV,
            "SQS_QUEUE_URL": "not-a-url",
            "AWS_ENDPOINT_URL": "not-a-url",
            "OCR_ENGINE": "rekognition",
            "REDIS_URL": "not-a-url",
        }
    )

    for removed in ("sqs_queue_url", "aws_endpoint_url", "ocr_engine", "redis_url"):
        assert not hasattr(cfg, removed)
