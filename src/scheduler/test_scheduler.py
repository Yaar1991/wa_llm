import pytest
from pydantic import ValidationError

from config import Settings
from scheduler import SUMMARY_JOB_ID, create_summary_scheduler


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        db_uri="postgresql+asyncpg://u:p@localhost/db",
        whatsapp_host="http://localhost:3000",
        **overrides,
    )


async def _job() -> None:
    pass


def test_scheduler_uses_cron_and_timezone():
    scheduler = create_summary_scheduler(
        _settings(summary_cron="30 20 * * 0-4", summary_timezone="Asia/Jerusalem"),
        _job,
    )

    assert scheduler is not None
    job = scheduler.get_job(SUMMARY_JOB_ID)
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["minute"] == "30"
    assert fields["hour"] == "20"
    assert str(job.trigger.timezone) == "Asia/Jerusalem"


def test_empty_cron_disables_scheduler():
    assert create_summary_scheduler(_settings(summary_cron=""), _job) is None


def test_invalid_cron_is_rejected():
    with pytest.raises(ValidationError, match="SUMMARY_CRON"):
        _settings(summary_cron="every day")


def test_invalid_timezone_is_rejected():
    with pytest.raises(ValidationError, match="SUMMARY_TIMEZONE"):
        _settings(summary_timezone="Mars/Olympus")
