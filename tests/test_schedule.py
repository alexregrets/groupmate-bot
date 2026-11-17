"""Adapted date, weekday and week-navigation contracts using synthetic schedules."""
import asyncio
import json
from datetime import date, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from groupmate import bot, clock, schedule
from groupmate.config import Config


@pytest.mark.parametrize("query,expected", [
    ("сегодня", "2026-11-17"), ("завтра", "2026-11-18"), ("вт", "2026-11-17"),
    ("пн", "2026-11-23"), ("2026-12-01", "2026-12-01"), ("01.01", "2027-01-01"),
    ("25.11.2026", "2026-11-25"), ("31.02", None), ("мусор", None)])
def test_parse_day(query, expected):
    result = schedule.parse_day(query, date(2026, 11, 17))
    assert (result.isoformat() if result else None) == expected


def test_json_unknown_and_explicit_empty(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({"days": {"2026-11-17": [], "2026-11-18": [
        dict(start="10:00", end="11:30", subject="Логика роботов", teacher="Иванов И.И.", room="А-12")],
        "2026-11-25": []}}), encoding="utf-8")
    provider = schedule.JsonScheduleProvider(path)
    week = asyncio.run(provider.week(date(2026, 11, 17)))
    assert len(week) == 2
    assert asyncio.run(schedule.day_lessons(provider, date(2026, 11, 17))) == []
    assert asyncio.run(schedule.day_lessons(provider, date(2026, 11, 19))) is None
    assert "Данных" in schedule.format_day(date(2026, 11, 19), None)
    assert "Пар нет" in schedule.format_day(date(2026, 11, 17), [])


def test_missing_json_is_unknown(tmp_path):
    assert asyncio.run(schedule.JsonScheduleProvider(tmp_path / "absent.json").week(date(2026, 11, 17))) is None


@pytest.mark.parametrize("lesson", [dict(subject="нет времени"), dict(start="25:00", end="26:00", subject="X"),
                                   dict(start="12:00", end="10:00", subject="X")])
def test_invalid_json_rejected(tmp_path, lesson):
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({"days": {"2026-11-17": [lesson]}}), encoding="utf-8")
    with pytest.raises(ValueError):
        asyncio.run(schedule.JsonScheduleProvider(path).week(date(2026, 11, 17)))


def test_schedule_html_safe():
    text = schedule.format_day(date(2026, 11, 17), [dict(start="10:00", end="11:00", subject="<b>x</b>",
        who=[dict(teacher="<&", room="<a>")], status="<замена>", note="<текст>", replaces="<пара>")])
    assert text.count("<b>") == 1
    assert "&lt;b&gt;" in text and "&lt;замена&gt;" in text


def test_schedule_week_alias(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: date(2026, 11, 17))
    provider = NS(week=AsyncMock(return_value={}))
    router = bot.build_router(Config("token", -200), provider, "studybot", 900)
    handler = next(h.callback for h in router.message.handlers if h.callback.__name__ == "handle_schedule")
    message = NS(text="/schedule неделя", chat=NS(id=-200), message_thread_id=None,
                 from_user=NS(id=10), bot=NS(send_message=AsyncMock()))
    asyncio.run(handler(message))
    assert message.bot.send_message.await_count == 7
