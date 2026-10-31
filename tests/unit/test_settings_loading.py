"""`load_settings`: the environment-to-attribute contract of ``docs/07`` §1.1.

The mapping test is the one the stage's "done when" names. The rest cover the two behaviours that make the
mapping tolerable to live with: every bad variable is reported at once, and secrets never reach a log.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from auto_html_report.domain.errors import ConfigurationError
from auto_html_report.infrastructure.settings import Settings, load_settings


def test_load_settings_with_an_empty_environment_should_equal_the_declared_defaults() -> None:
    # given no environment at all
    # when settings are loaded
    # then they equal a directly constructed Settings: the documented defaults live in one place, and
    # parsing cannot quietly disagree with them
    assert load_settings({}) == Settings()


def test_load_settings_should_read_every_documented_variable() -> None:
    # given one value for every variable in the table
    environ = {
        "AUTO_HTML_REPORT_OUTPUT_KIND": "local",
        "AUTO_HTML_REPORT_OUTPUT_DIR": "/tmp/somewhere",
        "AUTO_HTML_REPORT_OUTPUT_S3_BUCKET": "bucket",
        "AUTO_HTML_REPORT_OUTPUT_S3_PREFIX": "reports",
        "AUTO_HTML_REPORT_OUTPUT_S3_SSE": "AES256",
        "AUTO_HTML_REPORT_OUTPUT_S3_TIMEOUT_SECONDS": "12.5",
        "AUTO_HTML_REPORT_EVENT_STORE": "postgres",
        "AUTO_HTML_REPORT_SQLITE_PATH": "/tmp/events.db",
        "AUTO_HTML_REPORT_POSTGRES_DSN": "postgresql://user@host/db",
        "AUTO_HTML_REPORT_EVENT_MAX_ATTEMPTS": "5",
        "AUTO_HTML_REPORT_MAX_WORKERS": "3",
        "AUTO_HTML_REPORT_MAX_CONCURRENT_REPORTS": "2",
        "AUTO_HTML_REPORT_PROCESSING_LIMIT": "25",
        "AUTO_HTML_REPORT_BLOCK_TIMEOUT_SECONDS": "12.5",
        "AUTO_HTML_REPORT_BLOCK_RETRIES": "0",
        "AUTO_HTML_REPORT_RETRY_BASE_DELAY_SECONDS": "0.25",
        "AUTO_HTML_REPORT_RETRYABLE_EXCEPTIONS": "KeyError, TooManyRequests",
        "AUTO_HTML_REPORT_LOG_LEVEL": "DEBUG",
        "AUTO_HTML_REPORT_LOG_FORMAT": "console",
        "AUTO_HTML_REPORT_ERROR_DETAIL": "detailed",
        "AUTO_HTML_REPORT_FAIL_ON_BLOCK_ERROR": "false",
        "AUTO_HTML_REPORT_HTTP_BASE_URL": "https://api.example",
        "AUTO_HTML_REPORT_HTTP_API_TOKEN": "s3cret",
        "AUTO_HTML_REPORT_HTTP_MAX_REDIRECTS": "2",
    }

    # when they are parsed
    settings = load_settings(environ)

    # then every documented attribute holds the documented value
    assert settings.output_kind == "local"
    assert settings.output_dir == Path("/tmp/somewhere")
    assert settings.output_s3_bucket == "bucket"
    assert settings.output_s3_prefix == "reports"
    assert settings.output_s3_sse == "AES256"
    assert settings.output_s3_timeout_seconds == 12.5
    assert settings.event_store == "postgres"
    assert settings.sqlite_path == Path("/tmp/events.db")
    assert settings.postgres_dsn == "postgresql://user@host/db"
    assert settings.event_max_attempts == 5
    assert settings.max_workers == 3
    assert settings.processing_limit == 25
    assert settings.max_concurrent_reports == 2
    assert settings.block_timeout_seconds == 12.5
    assert settings.block_retries == 0
    assert settings.retry_base_delay_seconds == 0.25
    assert settings.retryable_exceptions == ("KeyError", "TooManyRequests")
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "console"
    assert settings.error_detail == "detailed"
    assert settings.fail_on_block_error is False
    assert settings.http_base_url == "https://api.example"
    assert settings.http_api_token == "s3cret"
    assert settings.http_max_redirects == 2


def test_load_settings_should_report_every_invalid_variable_at_once() -> None:
    # given three unrelated typos
    environ = {
        "AUTO_HTML_REPORT_MAX_WORKERS": "many",
        "AUTO_HTML_REPORT_BLOCK_TIMEOUT_SECONDS": "soon",
        "AUTO_HTML_REPORT_LOG_FORMAT": "xml",
    }

    # when they are parsed
    # then one exception names all three: a CI job that fails four times to reveal four typos is a bad job
    with pytest.raises(ConfigurationError) as failure:
        load_settings(environ)

    message = str(failure.value)
    assert "AUTO_HTML_REPORT_MAX_WORKERS" in message
    assert "AUTO_HTML_REPORT_BLOCK_TIMEOUT_SECONDS" in message
    assert "AUTO_HTML_REPORT_LOG_FORMAT" in message


def test_load_settings_with_s3_output_but_no_bucket_should_fail() -> None:
    # given a destination that cannot work
    # when settings are loaded
    # then the cross-field requirement is caught at start-up, not at the first write
    with pytest.raises(ConfigurationError, match="OUTPUT_S3_BUCKET is required"):
        load_settings({"AUTO_HTML_REPORT_OUTPUT_KIND": "s3"})


def test_load_settings_with_postgres_but_no_dsn_should_fail() -> None:
    # given a store that cannot connect
    with pytest.raises(ConfigurationError, match="POSTGRES_DSN is required"):
        load_settings({"AUTO_HTML_REPORT_EVENT_STORE": "postgres"})


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "Infinity"])
def test_load_settings_should_reject_a_non_finite_number(value: str) -> None:
    # given NaN or infinity for a float setting
    # when settings are loaded
    # then it is refused. Both pass every `<` comparison, and both disable a safety net: a NaN budget makes
    # `consumed > budget` always false, and an infinite one makes the hang guard unreachable
    with pytest.raises(ConfigurationError, match="finite"):
        load_settings({"AUTO_HTML_REPORT_BLOCK_TIMEOUT_SECONDS": value})


def test_load_settings_should_reject_a_non_finite_retry_delay() -> None:
    # given a NaN backoff base, which would make the first retry raise from time.sleep
    with pytest.raises(ConfigurationError, match="finite"):
        load_settings({"AUTO_HTML_REPORT_RETRY_BASE_DELAY_SECONDS": "nan"})


def test_load_settings_should_validate_and_normalise_the_log_level() -> None:
    # given a lower-case level
    # then it is accepted and stored canonically, matching how boolean values are handled
    assert load_settings({"AUTO_HTML_REPORT_LOG_LEVEL": "debug"}).log_level == "DEBUG"


def test_load_settings_with_an_unknown_log_level_should_fail_at_load() -> None:
    # given a level logging does not know
    # then the loader reports it -- rather than letting it through to fail later inside configure_logging,
    # which is where it used to surface as a raw ValueError
    with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
        load_settings({"AUTO_HTML_REPORT_LOG_LEVEL": "CHATTY"})


@pytest.mark.parametrize("value", ["JSON", "Console", " console "])
def test_load_settings_should_treat_enum_values_case_insensitively(value: str) -> None:
    # given an enum-like value in the wrong case, which boolean settings already accepted
    # then it is accepted and normalised to the canonical lower-case form
    assert load_settings({"AUTO_HTML_REPORT_LOG_FORMAT": value}).log_format == value.strip().lower()


@pytest.mark.parametrize(
    "environ",
    [
        {"AUTO_HTML_REPORT_MAX_WORKERS": "0"},
        {"AUTO_HTML_REPORT_BLOCK_TIMEOUT_SECONDS": "0"},
        {"AUTO_HTML_REPORT_MAX_CONCURRENT_REPORTS": "0"},
        {"AUTO_HTML_REPORT_EVENT_MAX_ATTEMPTS": "0"},
        {"AUTO_HTML_REPORT_BLOCK_RETRIES": "-1"},
        {"AUTO_HTML_REPORT_HTTP_MAX_REDIRECTS": "-1"},
    ],
)
def test_load_settings_with_a_nonsensical_number_should_fail(environ: dict[str, str]) -> None:
    # given a zero or negative value where one is meaningless
    # when settings are loaded
    # then it is refused: zero workers or a zero timeout is a configuration bug, not a request for
    # unlimited throughput
    with pytest.raises(ConfigurationError):
        load_settings(environ)


def test_load_settings_should_allow_zero_retries() -> None:
    # given a retry count of zero, which is a legitimate preference rather than a bug
    # then it is accepted -- "no retries" is a decision, unlike "no timeout"
    assert load_settings({"AUTO_HTML_REPORT_BLOCK_RETRIES": "0"}).block_retries == 0


def test_load_settings_with_a_non_boolean_should_fail() -> None:
    with pytest.raises(ConfigurationError, match="not a boolean"):
        load_settings({"AUTO_HTML_REPORT_FAIL_ON_BLOCK_ERROR": "maybe"})


def test_load_settings_should_ignore_unrelated_environment_variables() -> None:
    # given a normal shell with a hundred variables in it
    # when settings are loaded
    # then only AUTO_HTML_REPORT_* is considered, and nothing else is treated as an error
    assert load_settings({"PATH": "/usr/bin", "HOME": "/root"}) == Settings()


def test_repr_should_redact_anything_secret_shaped() -> None:
    # given settings carrying a token and a connection string
    settings = load_settings(
        {
            "AUTO_HTML_REPORT_HTTP_API_TOKEN": "s3cret",
            "AUTO_HTML_REPORT_POSTGRES_DSN": "postgresql://user:password@host/db",
        }
    )

    # when the settings are logged, which the first production debugging session will do
    # then the secrets are not in the output, and nothing that was redacted leaks through a nested repr
    text = repr(settings)
    assert "s3cret" not in text
    assert "password" not in text
    assert text.count("***") == 2


def test_repr_should_still_show_ordinary_values() -> None:
    # given settings with nothing secret in them
    # then the repr is useful, because a redacted-everything repr helps nobody
    text = repr(load_settings({"AUTO_HTML_REPORT_MAX_WORKERS": "3"}))
    assert "max_workers=3" in text


def test_load_settings_with_an_excessive_retry_count_should_be_refused() -> None:
    # given a retry count that would overflow the exponential backoff before any work starts
    # when it is loaded
    # then it is a configuration error at the edge, not an OverflowError from inside the executor
    with pytest.raises(ConfigurationError, match="at most 10"):
        load_settings({"AUTO_HTML_REPORT_BLOCK_RETRIES": "2000"})


def test_load_settings_with_an_excessive_attempt_limit_should_be_refused() -> None:
    # given an attempt limit beyond what SQLite's INTEGER can bind
    with pytest.raises(ConfigurationError, match="at most 1000"):
        load_settings({"AUTO_HTML_REPORT_EVENT_MAX_ATTEMPTS": "100000"})


def test_load_settings_with_an_excessive_processing_limit_should_be_refused() -> None:
    # given a batch ceiling large enough to hold a whole backlog in memory
    with pytest.raises(ConfigurationError, match="at most 10000"):
        load_settings({"AUTO_HTML_REPORT_PROCESSING_LIMIT": "100000"})


@pytest.mark.parametrize(
    ("variable", "value", "bound"),
    [
        ("AUTO_HTML_REPORT_MAX_WORKERS", "65", "64"),
        ("AUTO_HTML_REPORT_MAX_CONCURRENT_REPORTS", "33", "32"),
    ],
)
def test_load_settings_with_an_excessive_concurrency_should_be_refused(
    variable: str, value: str, bound: str
) -> None:
    # given a concurrency that would multiply the per-report block pool into hundreds of threads and
    # in-flight HTTP calls
    # when it is loaded
    # then it is refused at the edge with the bound in the message
    with pytest.raises(ConfigurationError, match=f"at most {bound}"):
        load_settings({variable: value})


def test_processing_limit_should_be_keyword_only() -> None:
    # given a field added to a public dataclass that already had a positional shape
    # then it is keyword-only, so every positional argument after it keeps its meaning (the same rule
    # ReportDefinition.event_id documents)
    fields = {field.name: field for field in dataclasses.fields(Settings)}
    assert fields["processing_limit"].kw_only is True


def test_the_s3_timeout_should_default_to_thirty_seconds() -> None:
    # given no configuration for the S3 transport timeout
    # then the documented default applies
    assert Settings().output_s3_timeout_seconds == 30.0


@pytest.mark.parametrize(
    ("value", "problem"), [("0", "at least"), ("nan", "finite"), ("301", "at most")]
)
def test_an_invalid_s3_timeout_should_be_refused(value: str, problem: str) -> None:
    # given a timeout that is zero, non-finite or beyond the documented maximum
    # when it is loaded
    # then it is refused with the bound in the message
    with pytest.raises(ConfigurationError, match=problem):
        load_settings({"AUTO_HTML_REPORT_OUTPUT_S3_TIMEOUT_SECONDS": value})
