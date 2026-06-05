"""Critical tests: config loads and the naming helpers follow the standard (Section L.6)."""

import pytest

from config import settings
from src.common.errors import ConfigError


def test_project_slug_is_marketpulse():
    assert settings.PROJECT == "marketpulse"


def test_active_env_defaults_to_local(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert settings.active_env() == "local"


@pytest.mark.parametrize("value", ["aws", "local", "AWS", "Local"])
def test_active_env_accepts_valid_values(monkeypatch, value):
    monkeypatch.setenv("APP_ENV", value)
    assert settings.active_env() == value.lower()


def test_active_env_rejects_invalid_value(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ConfigError):
        settings.active_env()


def test_active_env_treats_empty_as_default(monkeypatch):
    # An empty value (e.g. a stray `APP_ENV=` in .env) must fall back to the default,
    # not become "" and break — this is why .env.example can show empty keys safely.
    monkeypatch.setenv("APP_ENV", "")
    assert settings.active_env() == "local"


def test_environment_defaults_to_dev(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert settings.environment() == "dev"


def test_bucket_name_follows_naming_standard(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    assert settings.bucket_name("bronze") == "marketpulse-dev-bucket-bronze"
    assert settings.bucket_name("silver") == "marketpulse-dev-bucket-silver"


def test_aws_region_defaults_to_us_east_1(monkeypatch):
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    assert settings.aws_region() == "us-east-1"


def test_env_files_autoload_local_mode(monkeypatch):
    """Auto-load reads config/environments/local.env (LocalStack endpoint) by default."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    settings._load_env_files()  # idempotent re-load; defaults to the local mode file
    assert settings.active_env() == "local"
    assert settings.aws_endpoint_url() == "http://localhost:4566"
