"""Adapted morning/evening ping behavior without private calendars or weather."""
import asyncio
from datetime import date, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from groupmate import clock, homework, pings
from groupmate.config import Config


@pytest.mark.parametrize("morning", [True, False])
def test_ping_contains_classes_homework_and_escaped_html(morning):
    day = date(2026, 11, 17)
    homework.add(-200, day, "Сдать <кейс>", "почта", "Логика")
    provider = NS(week=AsyncMock(return_value={day.isoformat(): [
        dict(start="08:30", end="10:00", subject="<Роботы>", room="А-12", teacher="Иванов И.И.")]}))
    text = asyncio.run(pings.build_ping(provider, -200, day, morning))
    assert ("Сегодня" if morning else "Завтра") in text
    assert "&lt;Роботы&gt;" in text and "&lt;кейс&gt;" in text
    assert "08:30-10:00" in text


def test_unknown_day_with_homework_never_says_no_classes():
    day = date(2026, 11, 17)
    homework.add(-200, day, "Кейс", "почта")
    provider = NS(week=AsyncMock(return_value=None))
    text = asyncio.run(pings.build_ping(provider, -200, day, True))
    assert "Данных" in text and "Пар нет" not in text


@pytest.mark.parametrize("week", [None, {}])
def test_empty_ping_is_silent(week):
    assert asyncio.run(pings.build_ping(NS(week=AsyncMock(return_value=week)), -200, date(2026, 11, 17), True)) is None


def test_tick_deduplicates_and_targets_correct_day(monkeypatch):
    day = date(2026, 11, 17)
    provider = NS(week=AsyncMock(return_value={
        "2026-11-17": [dict(start="08:30", end="10:00", subject="Утренний предмет")],
        "2026-11-18": [dict(start="10:00", end="11:30", subject="Завтрашний предмет")]}))
    config = Config("token", -200, ping_thread_id=5)
    sender = NS(send_message=AsyncMock())
    for hour in (8, 8, 20, 20):
        stamp = datetime(2026, 11, 17, hour, tzinfo=clock.TIMEZONE)
        asyncio.run(pings.tick(sender, config, provider, stamp))
    assert sender.send_message.await_count == 2
    assert "Утренний предмет" in sender.send_message.call_args_list[0].args[1]
    assert "Завтрашний предмет" in sender.send_message.call_args_list[1].args[1]
    assert all(c.kwargs["message_thread_id"] == 5 for c in sender.send_message.call_args_list)


def test_chunks_preserve_html_entities_and_content():
    import html
    raw = "<&😀" * 10000
    text = html.escape(raw)
    chunks = pings.text_chunks(text)
    assert all(len(chunk) <= 3500 for chunk in chunks)
    assert "".join(html.unescape(c) for c in chunks) == raw


def test_astral_text_respects_telegram_utf16_limit():
    chunks = pings.text_chunks("😀" * 3000)
    assert all(len(c.encode("utf-16-le")) // 2 <= 4096 for c in chunks)
    assert "".join(chunks) == "😀" * 3000


def test_restart_at_evening_does_not_send_stale_morning_ping():
    sender = NS(send_message=AsyncMock())
    provider = NS(week=AsyncMock(return_value={
        "2026-11-17": [dict(start="08:30", end="10:00", subject="Утренний предмет")],
        "2026-11-18": [dict(start="10:00", end="11:30", subject="Завтрашний предмет")]}))
    asyncio.run(pings.tick(sender, Config("token", -200), provider,
                         datetime(2026, 11, 17, 20, tzinfo=clock.TIMEZONE)))
    sender.send_message.assert_awaited_once()
    assert "Завтрашний предмет" in sender.send_message.call_args.args[1]


def test_timezone_midnight_and_seconds_until():
    clock.configure("UTC")
    now = datetime(2026, 11, 17, 23, 50, tzinfo=clock.TIMEZONE)
    assert clock.seconds_until(clock.parse_time("00:10"), now) == 1200
    assert clock.parse_time("") is None
