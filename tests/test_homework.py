"""Adapted homework persistence, source deduplication and radar contracts."""
import asyncio
from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from groupmate import clock, homework, storage, study
from groupmate.config import Config


def test_sourced_homework_and_chat_isolation():
    day = date(2026, 11, 17)
    first = homework.add(-200, day, "Решить кейс", "чат", "Логика", "mail:main:42:0")
    assert homework.add(-200, day, "Решить кейс", "чат", "Логика", "mail:main:42:0") == first
    other = homework.add(-300, day, "Другой кейс", "чат", "Логика", "mail:main:42:0")
    assert other != first
    assert len(homework.for_day(-200, day)) == 1
    assert "Другой кейс" not in homework.format_items(homework.for_day(-200, day))
    assert not homework.mark_done(-300, first, 10)
    assert homework.mark_done(-200, first, 10)
    assert homework.for_day(-200, day, 10) == []
    assert len(homework.for_day(-200, day, 11)) == 1
    assert len(homework.for_day(-200, day)) == 1


def test_homework_html_safety():
    day = date(2026, 11, 17)
    homework.add(-200, day, "если a < b", "чат", "<предмет>")
    text = homework.format_items(homework.for_day(-200, day))
    assert "&lt;предмет&gt;" in text and "&lt; b" in text


def test_mail_ingest_all_tasks_not_just_card_preview():
    tasks = [{"what": f"Кейс {i}", "due": "2026-11-17"} for i in range(8)]
    card = dict(subject_short="Логика роботов", tasks=tasks)
    assert len(study.ingest(-200, card, "mail:main:42", date(2026, 11, 16))) == 8
    study.ingest(-200, card, "mail:main:42", date(2026, 11, 16))
    assert len(homework.for_day(-200, date(2026, 11, 17))) == 8


@pytest.mark.parametrize("tasks", [None, "broken", [None, {}, {"what": "нет срока"}], [{"what": 123, "due": "2026-11-17"}]])
def test_malformed_tasks_never_invent_deadline(tasks):
    assert study.ingest(-200, {"tasks": tasks}, "mail:main:42") == []


def test_fallback_explicit_dates_only():
    text = "Сдать кейс до 18.11\nПодготовить доклад когда-нибудь\nВстреча 19.11\nРешить задачу до 2026-11-20"
    tasks = study.fallback_tasks(text, "Логика", date(2026, 11, 17))
    assert [t["due"] for t in tasks] == ["2026-11-18", "2026-11-20"]


def test_radar_deduplicated_without_model_and_completion_is_personal():
    day = date(2026, 11, 17)
    homework.add(-200, day, "Сдать кейс", "почта", "Логика")
    cfg = Config("token", -200, ping_thread_id=5)
    sender = NS(send_message=AsyncMock())
    asyncio.run(study.radar(sender, cfg, day))
    asyncio.run(study.radar(sender, cfg, day))
    sender.send_message.assert_awaited_once()
    call = sender.send_message.call_args
    assert "сегодня" in call.args[1] and "Сдать кейс" in call.args[1]
    assert call.kwargs["message_thread_id"] == 5
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.startswith("done:")


def test_radar_failure_retries():
    day = date(2026, 11, 17)
    homework.add(-200, day, "Кейс", "почта")
    cfg = Config("token", -200)
    sender = NS(send_message=AsyncMock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError):
        asyncio.run(study.radar(sender, cfg, day))
    sender.send_message.side_effect = None
    asyncio.run(study.radar(sender, cfg, day))
    assert sender.send_message.await_count == 2


def test_radar_routes_extra_mailbox_chat_to_its_topic():
    from groupmate.config import MailSource, MailTarget
    day = date(2026, 11, 17)
    source = MailSource("extra", "host", "user", "pass", targets=(MailTarget(-300, 7),))
    cfg = Config("token", -200, mail_sources=(source,), ping_thread_id=5)
    homework.add(-300, day, "Чужой чату основной группы кейс", "почта")
    sender = NS(send_message=AsyncMock())
    asyncio.run(study.radar(sender, cfg, day))
    sender.send_message.assert_awaited_once()
    assert sender.send_message.call_args.args[0] == -300
    assert sender.send_message.call_args.kwargs["message_thread_id"] == 7


def test_format_items_limit_points_to_hw():
    items = [{"id": i, "due_date": "2026-11-18", "subject": "Предмет", "text": f"Задание {i}"} for i in range(8)]
    text = homework.format_items(items, limit=5)
    assert text.count("· Предмет: Задание") == 5
    assert text.endswith("…и ещё 3, всё списком: /hw")
    assert homework.format_items(items).count("· Предмет: Задание") == 8
