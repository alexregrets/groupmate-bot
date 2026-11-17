"""Synthetic replacements for the original RUZ fixtures and cache tests."""
import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp
import pytest

from groupmate import clock, ruz, schedule, storage


@pytest.mark.parametrize("filename,day", [("ruz_w43.json", date(2026, 10, 19)),
    ("ruz_w44.json", date(2026, 10, 26)), ("ruz_w45.json", date(2026, 11, 2))])
def test_parse_synthetic_week(filename, day):
    rows = json.loads((Path(__file__).parent / "fixtures" / filename).read_text(encoding="utf-8"))
    week = ruz.parse_week(rows, day)
    assert len(week) == 7
    assert week[day.isoformat()]
    assert ruz.parse_week(list(reversed(rows)), day) == week
    assert all(set(("pair", "start", "end", "kind", "subject", "who")) <= lesson.keys()
               for lessons in week.values() for lesson in lessons)
    assert len(week[day.isoformat()][0]["who"]) == 2


def test_outage_is_unknown_not_empty_and_uses_persistent_cache(monkeypatch):
    day = date(2026, 11, 17)
    fetch = AsyncMock(side_effect=aiohttp.ClientError("offline"))
    monkeypatch.setattr(ruz, "fetch_week", fetch)
    provider = ruz.RuzScheduleProvider(12345)
    assert asyncio.run(provider.week(day)) is None
    assert asyncio.run(provider.week(day)) is None
    fetch.assert_awaited_once()
    old = {day.isoformat(): [dict(subject="Логика роботов", start="08:30", end="10:00", who=[])]}
    storage.put("ruz:12345:2026-11-16", {"stamp": 0, "days": old})
    assert asyncio.run(ruz.RuzScheduleProvider(12345).week(day)) == old
    assert asyncio.run(ruz.RuzScheduleProvider(54321).week(day)) is None


def test_success_cache_and_ttl(monkeypatch):
    day = date(2026, 11, 17)
    stamp = [datetime(2026, 11, 17, 10, tzinfo=clock.TIMEZONE)]
    monkeypatch.setattr(clock, "now", lambda: stamp[0])
    week = {day.isoformat(): []}
    fetch = AsyncMock(return_value=week)
    monkeypatch.setattr(ruz, "fetch_week", fetch)
    provider = ruz.RuzScheduleProvider(12345, refresh_minutes=10)
    for _ in range(2):
        assert asyncio.run(provider.week(day)) == week
    fetch.assert_awaited_once_with(day, 12345)
    stamp[0] += timedelta(minutes=11)
    asyncio.run(provider.week(day))
    assert fetch.await_count == 2


def test_replacement_metadata_and_html_escape():
    row = dict(date="2026-11-17", beginLesson="10:00", endLesson="11:30", discipline="Логика роботов",
               kindOfWork="Практическое занятие", lecturer="Иванов И.И.", auditorium="А-12",
               status="<замена>", note="Примечание", replaces="Старая пара")
    lessons = ruz.parse_week([row], date(2026, 11, 17))["2026-11-17"]
    assert lessons[0]["kind"] == "семинар"
    text = schedule.format_day(date(2026, 11, 17), lessons)
    assert "&lt;замена&gt;" in text and "Примечание" in text and "Старая пара" in text
