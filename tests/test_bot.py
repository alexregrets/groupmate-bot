import asyncio
import html
from dataclasses import replace
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from groupmate import ai_client, bot, homework, storage
from groupmate.config import Config, MailSource, MailTarget


def config(**kwargs):
    return Config("token", -200, **kwargs)


def message(text="hello", chat_id=-200, thread_id=None, user_id=10, private=False):
    return NS(text=text, chat=NS(id=chat_id, type="private" if private else "supergroup"),
        message_thread_id=thread_id, message_id=42, from_user=NS(id=user_id, is_bot=False),
        bot=NS(send_message=AsyncMock()), reply_to_message=None)


def handler(config, name):
    router = bot.build_router(config, NS(week=AsyncMock(return_value={})), "studybot", 900)
    return next(h.callback for h in [*router.message.handlers, *router.callback_query.handlers] if h.callback.__name__ == name)


@pytest.mark.parametrize("enabled,text,expected", [
    (False, "@studybot hello", 0), (True, "hello", 0), (True, "@studybot hello", 1),
    (True, "@STUDYBOT hello", 1), (True, "@studybot_extra hello", 0)])
def test_ai_only_direct_and_opted_in(monkeypatch, enabled, text, expected):
    ask = AsyncMock(return_value="a < b")
    monkeypatch.setattr(ai_client, "ask", ask)
    msg = message(text)
    asyncio.run(handler(config(ai_replies=enabled), "handle_group_text")(msg))
    assert ask.await_count == expected
    assert msg.bot.send_message.await_count == expected
    if expected:
        assert "&lt;" in msg.bot.send_message.call_args.args[1]


def test_ai_reply_to_own_message_and_other_chat_blocked(monkeypatch):
    ask = AsyncMock(return_value="ok")
    monkeypatch.setattr(ai_client, "ask", ask)
    msg = message()
    msg.reply_to_message = NS(from_user=NS(id=900))
    asyncio.run(handler(config(ai_replies=True), "handle_group_text")(msg))
    assert ask.await_count == 1
    msg.chat.id = -300
    asyncio.run(handler(config(ai_replies=True), "handle_group_text")(msg))
    assert ask.await_count == 1


@pytest.mark.parametrize("admin,user,private,expected", [(10, 10, True, 1), (10, 11, True, 0), (10, 10, False, 0)])
def test_cost_only_admin_in_private(admin, user, private, expected):
    msg = message("/cost", user_id=user, private=private)
    asyncio.run(handler(config(admin_chat_id=admin), "handle_cost")(msg))
    assert msg.bot.send_message.await_count == expected


def test_full_mail_and_start_prompt_preserve_long_text_and_no_attachments():
    src = MailSource("main", "host", "user", "pass", targets=(MailTarget(-200, 5),))
    storage.put("mail:main:42", dict(subject="<Тема>", sender="teacher@example.test", body="<&😀" * 5000))
    msg = message(thread_id=5)
    callback = NS(message=msg, data="mo:42:-1:main", answer=AsyncMock())
    handle = handler(config(mail_sources=(src,)), "handle_mail_button")
    asyncio.run(handle(callback))
    calls = msg.bot.send_message.call_args_list
    assert len(calls) > 2
    content = "".join(html.unescape(c.args[1].replace("<b>", "").replace("</b>", "")) for c in calls)
    assert "<&😀" * 5000 in content
    assert all(c.kwargs["message_thread_id"] == 5 for c in calls)
    assert all(len(c.args[1]) <= 3500 for c in calls)
    callback.data = "ms:main:42"
    msg.bot.send_message.reset_mock()
    asyncio.run(handle(callback))
    assert "первый шаг" in msg.bot.send_message.call_args_list[0].args[1]


@pytest.mark.parametrize("chat,thread,key", [(-300, 5, "main"), (-200, 7, "main"), (-200, 5, "other")])
def test_foreign_source_chat_and_thread_are_denied(chat, thread, key):
    src = MailSource("main", "host", "user", "pass", targets=(MailTarget(-200, 5),))
    msg = message(chat_id=chat, thread_id=thread)
    callback = NS(message=msg, data=f"mo:42:-1:{key}", answer=AsyncMock())
    asyncio.run(handler(config(mail_sources=(src,)), "handle_mail_button")(callback))
    msg.bot.send_message.assert_not_awaited()
    assert callback.answer.call_args.kwargs["show_alert"]


def test_homework_commands_and_completion(monkeypatch):
    from groupmate import clock
    monkeypatch.setattr(clock, "today", lambda: date(2026, 11, 17))
    cfg = config()
    msg = message("/hw завтра прочитать главу 3")
    handle = handler(cfg, "handle_homework")
    asyncio.run(handle(msg))
    task = homework.for_day(-200, date(2026, 11, 18))[0]
    assert task["text"] == "прочитать главу 3"
    callback = NS(message=msg, data=f"done:{task['id']}", from_user=NS(id=10), answer=AsyncMock())
    asyncio.run(handler(cfg, "handle_done")(callback))
    msg.text = "/hw"
    msg.bot.send_message.reset_mock()
    asyncio.run(handle(msg))
    assert "не записана" in msg.bot.send_message.call_args.args[1]
    assert homework.for_day(-200, date(2026, 11, 18), 11)


def test_chat_extraction_is_opt_in_and_silent():
    msg = message("Решить кейс до 18.11.2026")
    asyncio.run(handler(config(), "handle_group_text")(msg))
    assert homework.for_day(-200, date(2026, 11, 18)) == []
    asyncio.run(handler(config(hw_from_chat=True), "handle_group_text")(msg))
    assert len(homework.for_day(-200, date(2026, 11, 18))) == 1
    msg.bot.send_message.assert_not_awaited()


def test_where_returns_only_current_context_before_group_configuration():
    msg = message("/where", chat_id=-300, thread_id=5)
    asyncio.run(handler(config(), "handle_where")(msg))
    assert "-300" in msg.bot.send_message.call_args.args[1]
    assert "5" in msg.bot.send_message.call_args.args[1]
