import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InputMediaDocument

sys.path.insert(0, str(Path(__file__).parent.parent))

from groupmate import mail_watcher


def _mail(count=0):
    return SimpleNamespace(
        uid="42", from_="teacher@example.test", subject="Практика", text="Письмо",
        html="", date=datetime(2026, 11, 10, 15, 30),
        attachments=[SimpleNamespace(filename=f"file{i}.pdf", payload=b"pdf")
                     for i in range(count)],
    )


def _card(**changes):
    card = dict(subject_short="Практика", teacher="Иванов", summary="Разбор темы",
                deadline=None, homework_ids=[1, 2], tasks=[
                    dict(what="Решить задачу", due="12.11", kind="дз"),
                    dict(what="Прочитать главу", due=None, kind="чтение"),
                    dict(what="Повторить тему", due=None, kind=""),
                ])
    card.update(changes)
    return card


def _send(bot, msg=None, card=None, **kwargs):
    asyncio.run(mail_watcher.send_mail_message(
        bot, -200, msg or _mail(), 650, preview=True, 
        ai_card=_card() if card is None else card, **kwargs,
    ))
    return bot.send_message.call_args_list[0].args[1]


def test_card_layout_and_tasks():
    bot = AsyncMock()
    text = _send(bot, _mail(1))
    # 17.11: короткая карточка, без времени, файлов, /hw и цитаты саммари
    assert text == "<b>Практика</b> · Иванов\n• Решить задачу · до 12.11\n• Прочитать главу\n• Повторить тему"
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["message_thread_id"] == 650
    buttons = kwargs["reply_markup"].inline_keyboard[0]
    assert [button.callback_data for button in buttons] == ["mo:42:-1:main", "ms:main:42"]


@pytest.mark.parametrize("tasks", [None, []])
def test_empty_tasks_fall_back_to_short_summary(tasks):
    card = _card(tasks=tasks, summary="Разбор темы " * 50)
    if tasks is None:
        card.pop("tasks")
    text = _send(AsyncMock(), card=card)
    assert "•" not in text
    lines = text.split("\n")
    assert len(lines) == 2 and lines[1].startswith("Разбор темы")
    assert len(lines[1]) <= 140


def test_card_escapes_fields_and_puts_deadline_in_header():
    text = _send(AsyncMock(), card=_card(
        subject_short="<Тема>", teacher="А & Б", summary="<текст>",
        deadline="<пятница>", homework_ids=["<1>"],
        tasks=[dict(what="a < b", due="<дата>", kind="<тест>"),
               dict(what="c", due="<пятница>", kind="дз")],
    ))
    assert text.startswith("<b>&lt;Тема&gt;</b> · А &amp; Б · до &lt;пятница&gt;\n")
    assert "• a &lt; b · до &lt;дата&gt;\n" in text
    assert text.endswith("\n• c")  # срок совпал с шапкой, не повторяем
    assert "<blockquote" not in text and "/hw" not in text


def test_tasks_are_limited_and_cut():
    tasks = [dict(what=f"Задание {i} " + "<&" * 500, due=None, kind="дз") for i in range(11)]
    text = _send(AsyncMock(), card=_card(tasks=tasks))
    assert text.count("• Задание") == mail_watcher.CARD_MAX_TASKS
    assert text.endswith("• …и ещё 8")
    for line in text.split("\n")[1:-1]:
        assert len(line) <= 2 + mail_watcher.CARD_TASK_CHARS
    assert len(text) < 1000


@pytest.mark.parametrize("count, sizes", [(3, [3]), (12, [10, 2]), (11, [10])])
def test_attachments_are_batched(count, sizes):
    bot = AsyncMock()
    _send(bot, _mail(count))
    assert bot.send_media_group.await_count == len(sizes)
    filenames = []
    for call, size in zip(bot.send_media_group.call_args_list, sizes):
        assert call.args == (-200,)
        assert call.kwargs["message_thread_id"] == 650
        media = call.kwargs["media"]
        assert len(media) == size
        assert all(isinstance(item, InputMediaDocument) for item in media)
        filenames.extend(item.media.filename for item in media)
    if count == 11:
        bot.send_document.assert_awaited_once()
        filenames.append(bot.send_document.call_args.args[1].filename)
    else:
        bot.send_document.assert_not_awaited()
    assert filenames == [f"file{i}.pdf" for i in range(count)]


def test_album_failure_falls_back_to_individual_documents(caplog):
    bot = AsyncMock()
    bot.send_media_group.side_effect = RuntimeError("album rejected")
    _send(bot, _mail(3))
    bot.send_media_group.assert_awaited_once()
    assert bot.send_document.await_count == 3
    assert "album rejected" in caplog.text
    assert [call.args[1].filename for call in bot.send_document.call_args_list] == [
        "file0.pdf", "file1.pdf", "file2.pdf"]
    assert all(call.kwargs["message_thread_id"] == 650
               for call in bot.send_document.call_args_list)


def test_single_file_and_attachment_opt_out():
    bot = AsyncMock()
    _send(bot, _mail(1))
    bot.send_document.assert_awaited_once()
    bot.send_media_group.assert_not_awaited()
    bot.reset_mock()
    _send(bot, _mail(3), with_attachments=False)
    bot.send_document.assert_not_awaited()
    bot.send_media_group.assert_not_awaited()


def test_oversized_file_is_reported_and_other_files_are_grouped(monkeypatch):
    monkeypatch.setattr(mail_watcher, "MAX_TELEGRAM_DOCUMENT_BYTES", 3)
    msg = _mail(3)
    msg.attachments[1].payload = b"large"
    bot = AsyncMock()
    _send(bot, msg)
    assert "больше 50 МБ" in bot.send_message.call_args_list[1].args[1]
    media = bot.send_media_group.call_args.kwargs["media"]
    assert [item.media.filename for item in media] == ["file0.pdf", "file2.pdf"]
    bot.send_document.assert_not_awaited()
