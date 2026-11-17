"""Письмо со спецсимволами не должно ломать разметку и терять доставку.

Реальный сценарий: тема «Задание <срочно> & практика». Без экранирования
Telegram отвечает 400 can't parse entities, письмо не доходит, а last_uid
не двигается - и следующий цикл пытается переслать ту же пачку заново.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from groupmate.mail_watcher import format_message_text  # noqa: E402


@dataclass
class FakeMessage:
    subject: str
    from_: str
    text: str
    date_str: str = "Mon, 01 Sep 2026"
    html: str = ""
    attachments: list = field(default_factory=list)


def test_тема_со_скобками_экранируется():
    msg = FakeMessage(subject="Задание <срочно> & практика", from_="teacher@example.test", text="")
    text = format_message_text(msg)
    assert "&lt;срочно&gt;" in text
    assert "&amp;" in text
    assert "<срочно>" not in text


def test_имя_отправителя_в_угловых_скобках_экранируется():
    msg = FakeMessage(subject="тема", from_="Иванов И.И. <teacher@example.test>", text="")
    assert "&lt;teacher@example.test&gt;" in format_message_text(msg)


def test_тело_письма_экранируется():
    msg = FakeMessage(subject="тема", from_="a@b.ru", text="если a < b, то <b>жирный</b> не жирный")
    text = format_message_text(msg)
    assert "&lt;b&gt;" in text
    # собственная разметка бота остаётся рабочей
    assert text.startswith("<b>тема</b>")


def test_html_письмо_приходит_текстом_а_не_вёрсткой():
    """Письма преподов часто уходят HTML-only: в msg.text пусто, всё в msg.html.
    Раньше вёрстка уезжала в чат как есть."""
    msg = FakeMessage(
        subject="Задание",
        from_="teacher@example.test",
        text="",
        html="<html><head><style>p{color:red}</style></head><body>"
             "<p>Сдать до <b>пятницы</b></p><div>Тема&nbsp;3</div></body></html>",
    )
    text = format_message_text(msg)
    assert "Сдать до пятницы" in text
    assert "Тема 3" in text
    assert "color:red" not in text
    assert "&lt;p&gt;" not in text  # тегов быть не должно вообще


def test_длинное_письмо_влезает_в_лимит_после_экранирования():
    """3500 символов из «<» после escape превращаются в 14000 - Telegram
    отклоняет такое сообщение целиком, а uid уже сохранён, и письмо теряется."""
    msg = FakeMessage(subject="тема", from_="a@example.test", text="<" * 5000)
    text = format_message_text(msg)
    assert len(text) <= 4096
    assert text.endswith("…")


def test_очень_длинная_тема_не_раздувает_сообщение():
    msg = FakeMessage(subject="А" * 1000, from_="a@example.test", text="текст письма")
    assert len(format_message_text(msg)) <= 4096
