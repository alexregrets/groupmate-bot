"""Почта в чате: карточка вместо портянки.

Претензия группы 3 сентября - не «письма не нужны», а «искать среди кучи всего».
Полный текст письма в чате не нужен никому: для поиска есть /recent, /all
и /search, а решение «это меня касается?» принимается по теме, отправителю
и первым строкам. Файлы остаются в чате - за ними и приходят.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from groupmate import mail_watcher  # noqa: E402


class ФейковыйБот:
    def __init__(self):
        self.сообщения = []
        self.документы = []

    async def send_message(self, chat_id, text, **kwargs):
        self.сообщения.append((chat_id, text, kwargs))

    async def send_document(self, chat_id, document, **kwargs):
        self.документы.append((chat_id, document, kwargs))


def _письмо(text="текст письма", attachments=(), subject="Тема", uid="42"):
    return SimpleNamespace(
        uid=uid,
        from_="prepod@example.test",
        subject=subject,
        text=text,
        html="",
        attachments=list(attachments),
    )


def _вложение(filename="lecture.pdf"):
    return SimpleNamespace(filename=filename, payload=b"%PDF-1.4 ...")


# --- сама карточка ----------------------------------------------------------

def test_длинное_письмо_в_чат_целиком_не_уходит():
    длинное = "А" * 3000
    карточка, есть_продолжение = mail_watcher.format_card_text(_письмо(text=длинное))

    assert len(карточка) < 600
    assert есть_продолжение
    assert карточка.endswith("…")


def test_короткое_письмо_показано_целиком_и_открывать_нечего():
    карточка, есть_продолжение = mail_watcher.format_card_text(_письмо(text="сдать до пятницы"))

    assert "сдать до пятницы" in карточка
    assert not есть_продолжение


def test_в_карточке_есть_тема_и_отправитель():
    карточка, _ = mail_watcher.format_card_text(_письмо(subject="Зачёт"))

    assert "<b>Зачёт</b>" in карточка
    assert "prepod@example.test" in карточка


def test_имена_файлов_видны_без_открытия():
    карточка, _ = mail_watcher.format_card_text(
        _письмо(attachments=[_вложение("лекция1.pdf"), _вложение("задание.docx")])
    )

    assert "лекция1.pdf" in карточка
    assert "задание.docx" in карточка


def test_вёрстка_письма_в_карточку_не_уезжает():
    письмо = _письмо(text="")
    письмо.html = "<p style='color:red'>Сдать <b>до пятницы</b></p><script>alert(1)</script>"

    карточка, _ = mail_watcher.format_card_text(письмо)

    assert "Сдать до пятницы" in карточка
    assert "color:red" not in карточка
    assert "alert(1)" not in карточка


def test_разметка_из_письма_экранируется():
    карточка, _ = mail_watcher.format_card_text(_письмо(subject="<срочно>", text="если a < b"))

    assert "&lt;срочно&gt;" in карточка
    assert "&lt; b" in карточка
    # собственная разметка бота остаётся рабочей
    assert карточка.startswith("<b>")


def test_переносы_письма_карточку_не_растягивают():
    карточка, _ = mail_watcher.format_card_text(_письмо(text="строка\n\n\nещё строка"))

    # три переноса подряд из письма превратились бы в пустой экран
    assert "строка ещё строка" in карточка


# --- отправка ---------------------------------------------------------------

def test_автопостинг_идёт_карточкой_с_кнопкой():
    bot = ФейковыйБот()
    asyncio.run(
        mail_watcher.send_mail_message(bot, -200, _письмо(text="Я" * 3000), 650, preview=True)
    )

    chat_id, text, kwargs = bot.сообщения[0]
    assert kwargs["message_thread_id"] == 650
    assert len(text) < 600
    кнопка = kwargs["reply_markup"].inline_keyboard[0][0]
    assert кнопка.callback_data == "mo:42:-1:main"  # хвост - ящик, из которого письмо


def test_у_короткого_письма_кнопки_нет():
    bot = ФейковыйБот()
    asyncio.run(mail_watcher.send_mail_message(bot, -200, _письмо(), 650, preview=True))

    assert bot.сообщения[0][2]["reply_markup"] is None


def test_файлы_приходят_вместе_с_карточкой():
    bot = ФейковыйБот()
    asyncio.run(
        mail_watcher.send_mail_message(
            bot, -200, _письмо(attachments=[_вложение()]), 650, preview=True
        )
    )

    assert len(bot.документы) == 1
    assert bot.документы[0][2]["message_thread_id"] == 650


def test_открытие_с_карточки_файлы_не_дублирует():
    bot = ФейковыйБот()
    asyncio.run(
        mail_watcher.send_mail_message(
            bot, -200, _письмо(attachments=[_вложение()]), 650, with_attachments=False
        )
    )

    assert bot.документы == []
    # текст при этом пришёл полный, а не карточкой
    assert "текст письма" in bot.сообщения[0][1]


def test_без_preview_поведение_прежнее():
    """Открытие из списка и старые вызовы обязаны давать полное письмо."""
    bot = ФейковыйБот()
    asyncio.run(mail_watcher.send_mail_message(bot, -200, _письмо(text="Б" * 1000), 650))

    text = bot.сообщения[0][1]
    assert "Б" * 900 in text
    assert bot.сообщения[0][2].get("reply_markup") is None


