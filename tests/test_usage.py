"""Adapted rolling-window accounting and balance alert tests, plus generic budgets."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from groupmate import ai_client, clock, usage
from groupmate.config import Config


def cfg(**kw):
    return Config("token", -200, ai_api_key="test-key", ai_api_url="https://api.example.test/v1/chat/completions",
                  ai_model="test-model", **kw)


def test_record_windows(monkeypatch):
    now = datetime(2026, 11, 17, 1, tzinfo=clock.TIMEZONE)
    stamp = [now]
    monkeypatch.setattr(clock, "now", lambda: stamp[0])
    for age in (0, 2, 8, 31):
        stamp[0] = now - timedelta(days=age)
        usage.record("chat", "model", dict(prompt_tokens=10, completion_tokens=3, cost=.1))
    stamp[0] = now - timedelta(hours=2)
    usage.record("mail_card", "model", dict(prompt_tokens=4, completion_tokens=1))
    result = usage.summary(now)
    for window, count in (("сегодня", 1), ("7 дней", 2), ("30 дней", 3)):
        row = result[window]["chat"]
        assert row["requests"] == count and row["prompt_tokens"] == count * 10
        assert row["completion_tokens"] == count * 3 and row["cost"] == pytest.approx(count * .1)
    assert "mail_card" not in result["сегодня"]
    assert result["7 дней"]["mail_card"]["cost"] is None


def test_unknown_cost_never_reported_as_free_or_partial_sum():
    usage.record("chat", "model", {"cost": .1})
    usage.record("chat", "model", None)
    assert usage.summary()["сегодня"]["chat"]["cost"] is None
    assert "стоимость неизвестна" in usage.format_summary()
    assert not usage.allowed(cfg())


def test_token_price_estimate_and_budget():
    config = cfg(ai_input_usd_per_million=2, ai_output_usd_per_million=4, ai_daily_limit_usd=.01)
    usage.record("chat", "model", dict(prompt_tokens=1000, completion_tokens=2000), config)
    assert usage.summary()["сегодня"]["chat"]["cost"] == pytest.approx(.01)
    assert not usage.allowed(config)


class Response:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        if self.status >= 400:
            import aiohttp
            raise aiohttp.ClientError("failed")

    async def json(self):
        return self.data


class Session(Response):
    def __init__(self, response):
        self.response, self.calls = response, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    get = post


def install(monkeypatch, response):
    session = Session(response)
    monkeypatch.setattr(ai_client.aiohttp, "ClientSession", lambda **kw: session)
    return session


@pytest.mark.parametrize("kind", ["chat", "mail_card", "homework"])
@pytest.mark.parametrize("openrouter", [True, False])
def test_all_requests_accounted_with_optional_provider_usage(monkeypatch, kind, openrouter):
    session = install(monkeypatch, Response(dict(choices=[dict(message=dict(content="ok"))], usage=dict(cost=.02))))
    config = cfg()
    if openrouter:
        config = replace(config, ai_api_url="https://openrouter.ai/api/v1/chat/completions")
    assert asyncio.run(ai_client.request(config, kind, [{"role": "user", "content": "q"}])) == "ok"
    assert ("usage" in session.calls[0][1]["json"]) == openrouter
    assert usage.summary()["сегодня"][kind]["cost"] == .02


def test_disabled_ai_never_opens_session(monkeypatch):
    monkeypatch.setattr(ai_client.aiohttp, "ClientSession", lambda **kw: pytest.fail("network attempted"))
    assert asyncio.run(ai_client.ask(Config("token", -200), "hello")) is None


def test_budget_blocks_before_request(monkeypatch):
    config = cfg(ai_daily_limit_usd=.01)
    usage.record("chat", "model", {"cost": .02})
    monkeypatch.setattr(ai_client.aiohttp, "ClientSession", lambda **kw: pytest.fail("budget bypass"))
    assert asyncio.run(ai_client.ask(config, "q")) is None


def test_key_status_and_persisted_alert(monkeypatch):
    config = replace(cfg(admin_chat_id=10), ai_api_url="https://openrouter.ai/api/v1/chat/completions")
    session = install(monkeypatch, Response({"data": {"limit": 4, "limit_remaining": .03}}))
    assert asyncio.run(usage.key_status(config))["limit_remaining"] == .03
    assert session.calls[0][0] == "https://openrouter.ai/api/v1/key"
    sender = NS(send_message=AsyncMock())
    for _ in range(2):
        asyncio.run(usage.check_alert(sender, config))
    sender.send_message.assert_awaited_once()
    assert "$0.03" in sender.send_message.call_args.args[1]


def test_generic_endpoint_local_alert(monkeypatch):
    config = cfg(admin_chat_id=10, ai_daily_limit_usd=.1)
    usage.record("chat", "model", {"cost": .09})
    sender = NS(send_message=AsyncMock())
    asyncio.run(usage.check_alert(sender, config))
    sender.send_message.assert_awaited_once()
    assert "$0.01" in sender.send_message.call_args.args[1]


@pytest.mark.parametrize("content", ["not json", "[]", "null", "42"])
def test_bad_card_uses_fallback(monkeypatch, content):
    install(monkeypatch, Response({"choices": [{"message": {"content": content}}], "usage": {"cost": .01}}))
    mail = NS(subject="Логика", from_="teacher@example.test", text="text", html="", date=clock.now())
    assert asyncio.run(ai_client.make_mail_card(mail, cfg())) is None


def test_missing_cost_stops_followup_request(monkeypatch):
    session = install(monkeypatch, Response({"choices": [{"message": {"content": "ok"}}]}))
    assert asyncio.run(ai_client.ask(cfg(), "q")) == "ok"
    assert asyncio.run(ai_client.ask(cfg(), "q2")) is None
    assert len(session.calls) == 1


def test_invalid_top_level_response_is_fallback_and_accounted(monkeypatch):
    install(monkeypatch, Response([]))
    assert asyncio.run(ai_client.ask(cfg(), "q")) is None
    assert usage.summary()["сегодня"]["chat"]["cost"] is None
