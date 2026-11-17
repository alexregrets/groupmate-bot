"""Independent contract tests for the quiet-group release of 17 November."""
import asyncio
import html
import re
from dataclasses import fields, replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from groupmate import mail_watcher


def card(**changes):
    result = dict(subject_short="Практика", teacher="Иванов", deadline=None,
                  summary="Разбор темы", tasks=[])
    result.update(changes)
    return result


def render(value, source="main"):
    bot = NS(send_message=AsyncMock())
    msg = NS(uid="42", subject="Исходная тема", from_="teacher@example.test",
             text="Полное письмо", html="", attachments=[],
             date=datetime(2026, 11, 17, 10, 45))
    asyncio.run(mail_watcher.send_mail_message(
        bot, -100, msg, 650, preview=True, 
        with_attachments=False, source_key=source, ai_card=value))
    bot.send_message.assert_awaited_once()
    call = bot.send_message.call_args
    assert call.args[0] == -100
    assert call.kwargs["message_thread_id"] == 650
    return call.args[1], call.kwargs["reply_markup"]


def assert_safe_fragment(value):
    # html.unescape alone accepts incomplete entities, so check their syntax too.
    assert not re.search(r"&(?!amp;|lt;|gt;|quot;|#x27;)", value)
    assert "<" not in value and ">" not in value


def test_card_constants_and_exact_buttons():
    assert mail_watcher.CARD_MAX_TASKS == 3
    assert mail_watcher.CARD_TASK_CHARS == 140
    text, keyboard = render(card(tasks=[dict(what="Кейс", due="18.11")]))
    assert text == "<b>Практика</b> · Иванов\n• Кейс · до 18.11"
    assert [(b.text, b.callback_data) for b in keyboard.inline_keyboard[0]] == [
        ("Читать целиком", "mo:42:-1:main"), ("промпт на старт", "ms:main:42")]
    assert len(keyboard.inline_keyboard) == 1


@pytest.mark.parametrize("size", [0, 1, 3, 4, 11])
def test_card_task_count_uses_contract_not_implementation_constant(size):
    text, _ = render(card(tasks=[dict(what=f"Задача {i}") for i in range(size)]))
    expected = ["<b>Практика</b> · Иванов"]
    expected += [f"• Задача {i}" for i in range(min(size, 3))]
    if size > 3:
        expected += [f"• …и ещё {size - 3}"]
    elif size == 0:
        expected += ["Разбор темы"]
    assert text.splitlines() == expected


@pytest.mark.parametrize("field", ["subject_short", "teacher", "deadline", "summary", "what", "due"])
@pytest.mark.parametrize("payload", ['<&"\'', "&" * 1000, "<" * 1000, "😀" * 5000],
                         ids=["markup", "amp-boundary", "lt-boundary", "astral"])
def test_every_field_is_escaped_and_card_fits_telegram(field, payload):
    value = card()
    if field in ("what", "due"):
        value["tasks"] = [dict(what="Задача", due=None)]
        value["tasks"][0][field] = payload
    else:
        value[field] = payload
    text, _ = render(value)
    assert len(html.unescape(text).encode("utf-16-le")) // 2 <= 4096
    assert len(text) <= 4096
    assert text.count("<b>") == text.count("</b>") == 1
    assert_safe_fragment(text.replace("<b>", "").replace("</b>", ""))
    if field in ("summary", "what", "due"):
        assert len(text.splitlines()[1].removeprefix("• ")) <= 140


@pytest.mark.parametrize("tasks", [None, [], "missing"])
def test_summary_when_tasks_absent_or_null(tasks):
    value = card(tasks=tasks, summary="<&" * 1000)
    if tasks == "missing":
        value.pop("tasks")
    text, _ = render(value)
    lines = text.splitlines()
    assert len(lines) == 2
    assert 0 < len(lines[1]) <= 140
    assert_safe_fragment(lines[1])


@pytest.mark.parametrize("due", [None, "", "   ", "18.11", 18, False])
def test_optional_due_normalized_without_crash(due):
    text, _ = render(card(deadline=" 18.11 ", tasks=[dict(what=None, due=due)]))
    assert "None" not in text
    assert text.startswith("<b>Практика</b> · Иванов · до 18.11\n")
    assert_safe_fragment(text.split("\n", 1)[1])


def test_whitespace_deadline_and_matching_trimmed_due():
    text, _ = render(card(deadline=" \t ", tasks=[dict(what="Кейс", due=None)]))
    assert text == "<b>Практика</b> · Иванов\n• Кейс"
    text, _ = render(card(deadline=" 18.11 ", tasks=[dict(what="Кейс", due="18.11  ")]))
    assert text == "<b>Практика</b> · Иванов · до 18.11\n• Кейс"


@pytest.mark.parametrize("field", ["subject_short", "teacher"])
def test_missing_header_field_does_not_abort_delivery(field):
    value = card()
    value.pop(field)
    text, _ = render(value)
    assert text.startswith("<b>")


@pytest.mark.parametrize("field", ["subject_short", "teacher"])
@pytest.mark.parametrize("empty", [None, "", "   "])
def test_empty_header_field_has_readable_fallback(field, empty):
    text, _ = render(card(**{field: empty}))
    subject, teacher = text.splitlines()[0].split(" · ")
    assert subject.removeprefix("<b>").removesuffix("</b>").strip() not in ("", "None")
    assert teacher.strip() not in ("", "None")


@pytest.mark.parametrize("tasks", [
    "bad",
    {"what": "bad"},
    7,
])
def test_invalid_task_container_does_not_abort_delivery(tasks):
    text, _ = render(card(tasks=tasks))
    assert text.startswith("<b>Практика</b> · Иванов")


@pytest.mark.parametrize("entry", [None, "bad", 7])
def test_invalid_task_entry_does_not_abort_delivery(entry):
    text, _ = render(card(tasks=[entry, dict(what="Кейс")]))
    assert "• Кейс" in text


def test_due_matches_deadline_case_insensitively():
    text, _ = render(card(deadline="ПЯТНИЦА", tasks=[dict(what="Кейс", due="пятница")]))
    assert text.splitlines() == ["<b>Практика</b> · Иванов · до ПЯТНИЦА", "• Кейс"]


def test_due_matches_deadline_with_equivalent_whitespace():
    text, _ = render(card(deadline="18 ноября", tasks=[dict(what="Кейс", due="18  ноября")]))
    assert text.splitlines() == ["<b>Практика</b> · Иванов · до 18 ноября", "• Кейс"]


@pytest.mark.parametrize("field", ["subject_short", "teacher", "deadline", "summary", "what", "due"])
def test_card_field_newlines_do_not_create_extra_lines(field):
    value = card()
    if field in ("what", "due"):
        value["tasks"] = [dict(what="Кейс")]
        value["tasks"][0][field] = "строка\nещё строка"
    else:
        value[field] = "строка\nещё строка"
    text, _ = render(value)
    assert len(text.splitlines()) == 2


