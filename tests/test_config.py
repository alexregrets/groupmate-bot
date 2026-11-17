import os
from pathlib import Path

import pytest

from groupmate.config import load_config


@pytest.fixture(autouse=True)
def clean_configuration(monkeypatch):
    example = Path(__file__).resolve().parent.parent / ".env.example"
    keys = [line.split("=", 1)[0] for line in example.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_GROUP_CHAT_ID", "-200")


def test_defaults_ai_and_chat_extraction_disabled_mail_optional():
    config = load_config()
    assert not config.ai_enabled and not config.ai_replies and not config.hw_from_chat
    assert config.mail_sources == () and config.hw_from_mail and config.radar
    assert config.schedule_provider == "json" and config.ruz_group_id == 0


@pytest.mark.parametrize("name,value", [("PING_THREAD_ID", "-1"), ("MAIL_THREAD_ID", "0"), ("MORNING_PING", "25:00"),
    ("EVENING_PING", "no"), ("SCHEDULE_PROVIDER", "missing"), ("SCHEDULE_PROVIDER", "ruz"),
    ("AI_REPLIES", "yes"), ("AI_DAILY_LIMIT_USD", "-1"), ("AI_DAILY_LIMIT_USD", "nan"),
    ("COST_ALERT_USD", "inf"), ("AI_INPUT_USD_PER_MILLION", "-1")])
def test_invalid_settings_fail_at_start(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises((ValueError, RuntimeError)):
        load_config()


def test_multiple_mailboxes_targets_and_secrets_not_in_repr(monkeypatch):
    monkeypatch.setenv("IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("IMAP_USER", "group@example.test")
    monkeypatch.setenv("IMAP_PASSWORD", "super-secret-test")
    monkeypatch.setenv("PING_THREAD_ID", "5")
    monkeypatch.setenv("MAIL_SOURCES", '[{"key":"extra","imap_host":"host","imap_user":"extra@example.test",'
        '"imap_password":"another-secret-test","targets":[{"chat_id":-300,"thread_id":7}]}]')
    cfg = load_config()
    assert len(cfg.mail_sources) == 2
    assert cfg.mail_sources[0].targets[0].thread_id == 5
    assert cfg.source_by_key("extra").targets[0].chat_id == -300
    assert cfg.source_by_key("absent") is None
    assert "super-secret-test" not in repr(cfg) and "another-secret-test" not in repr(cfg)


def test_duplicate_source_key_rejected(monkeypatch):
    monkeypatch.setenv("MAIL_SOURCES", '[{"key":"extra","imap_host":"h","imap_user":"u","imap_password":"p"},'
        '{"key":"extra","imap_host":"h","imap_user":"u","imap_password":"p"}]')
    with pytest.raises(ValueError, match="Ключи"):
        load_config()


def test_incomplete_ai_configuration_rejected(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "test-key")
    with pytest.raises(ValueError, match="AI_API_URL"):
        load_config()
