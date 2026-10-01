"""Config_Loader and AWS client factory tests.

Feature: python-ocr-worker, Property 10: Missing-variable reporting is complete.
Feature: python-ocr-worker, Property 12: Uniform endpoint resolution across AWS
clients.
"""

from __future__ import annotations

import pytest
from fakes import BASE_ENV
from hypothesis import given
from hypothesis import strategies as st

from config import ConfigError, load_config
from storage import clients

ALWAYS_REQUIRED = (
    "AWS_REGION",
    "SQS_QUEUE_URL",
    "DATABASE_URL",
    "REDIS_URL",
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
        ("AWS_ENDPOINT_URL", "localhost:4566"),
        ("REDIS_URL", "localhost:6379"),
        ("DATABASE_URL", "mysql://u:p@h/db"),
        ("SQS_QUEUE_URL", "ocr-processamento-fila"),
    ],
)
def test_malformed_urls_are_rejected(name, value):
    with pytest.raises(ConfigError) as err:
        load_config({**BASE_ENV, name: value})
    assert err.value.invalid == [name]


def build_all(cfg):
    return [clients.s3_client(cfg), clients.sqs_client(cfg), clients.dynamodb_client(cfg)]


def test_all_clients_share_the_custom_endpoint():
    cfg = load_config(BASE_ENV)

    assert {c.meta.endpoint_url for c in build_all(cfg)} == {"http://localhost:4566"}


def test_all_clients_use_default_aws_endpoints_without_override(monkeypatch):
    # boto3 itself honors AWS_ENDPOINT_URL from the process environment.
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    cfg = load_config({**BASE_ENV, "AWS_ENDPOINT_URL": ""})

    for client in build_all(cfg):
        assert client.meta.endpoint_url.endswith(".amazonaws.com")
        assert client.meta.region_name == "us-east-1"
