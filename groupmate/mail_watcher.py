import html as html_module
import logging
import re
import asyncio
import imaplib
from dataclasses import dataclass
from html import escape
from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from imap_tools import MailMessage, MailBox, AND

from . import ai_client, storage, study
from .config import Config, MailSource

logger = logging.getLogger(__name__)
MAX_TELEGRAM_TEXT = 3500
MAX_TELEGRAM_DOCUMENT_BYTES = 50 * 1024 * 1024
MAIL_PREVIEW_CHARS = 300
CARD_MAX_TASKS = 3
CARD_TASK_CHARS = 140


@dataclass
class FetchResult:
    to_forward: list
    max_uid_seen: int


def _fetch_new_messages(config: Config, source: MailSource):
    last_uid = storage.load_last_uid(source.key)
    initialized = storage.get(f"initialized:{source.key}", False)
    with MailBox(source.imap_host, timeout=30).login(
            source.imap_user, source.imap_password, initial_folder=source.imap_folder) as mailbox:
        criteria = AND(uid=f"{last_uid + 1}:*") if last_uid else AND(all=True)
        uids = sorted(u for u in map(int, mailbox.uids(criteria)) if u > last_uid)
        maximum = uids[-1] if uids else last_uid
        if not uids or (not initialized and not config.forward_history):
            return FetchResult([], maximum)
        wanted = []
        for msg in mailbox.fetch(uid_list=[str(u) for u in uids], headers_only=True, mark_seen=False):
            sender = msg.from_.lower()
            if any(pattern in sender for pattern in config.sender_blocklist):
                continue
            if config.teacher_senders and not any(pattern in sender for pattern in config.teacher_senders):
                continue
            wanted.append(msg.uid)
        messages = list(mailbox.fetch(uid_list=wanted, mark_seen=False)) if wanted else []
        return FetchResult(sorted(messages, key=lambda m: int(m.uid)), maximum)


async def fetch_with_retry(config, source):
    for attempt in range(1, 4):
        try:
            return await asyncio.to_thread(_fetch_new_messages, config, source)
        except (TimeoutError, OSError, imaplib.IMAP4.abort):
            logger.warning("IMAP transient failure source=%s attempt=%d/3", source.key, attempt)
            if attempt == 3:
                raise
            await asyncio.sleep(attempt * 2)


def cache_key(source_key, uid):
    return f"mail:{source_key}:{int(uid)}"


def mail_record(msg, card=None):
    return dict(subject=msg.subject or "(без темы)", sender=msg.from_ or "",
                body=msg.text or _html_to_text(msg.html or ""), card=card)


_poll_locks = {}


async def poll_once(bot, config, source):
    lock = _poll_locks.setdefault(source.key, asyncio.Lock())
    async with lock:
        try:
            result = await fetch_with_retry(config, source)
            for msg in result.to_forward:
                key = cache_key(source.key, msg.uid)
                cached = storage.get(key)
                card = cached.get("card") if cached else await ai_client.make_mail_card(msg, config)
                record = cached or mail_record(msg, card)
                storage.put(key, record)
                reference = getattr(msg, "date", None)
                reference = reference.date() if reference else study.clock.today()
                if config.hw_from_mail:
                    extracted = card or {"tasks": study.fallback_tasks(record["body"], record["subject"], reference)}
                    for chat_id in {t.chat_id for t in source.targets}:
                        study.ingest(chat_id, extracted, key, reference)
                for target in source.targets:
                    delivery = f"delivered:{source.key}:{int(msg.uid)}:{target.chat_id}:{target.thread_id}"
                    if storage.get(delivery):
                        continue
                    await send_mail_message(bot, target.chat_id, msg, target.thread_id,
                        preview=True, source_key=source.key, ai_card=card)
                    storage.put(delivery, True)
                # Advance only after every target and every attachment succeeded.
                storage.save_last_uid(int(msg.uid), source.key)
            storage.save_last_uid(result.max_uid_seen, source.key)
            storage.put(f"initialized:{source.key}", True)
        except Exception:
            # Never include credentials, message bodies or exception strings in logs.
            logger.error("Mail poll failed source=%s; checkpoint preserved, will retry", source.key)


async def poll_loop(bot, config, source):
    while True:
        await poll_once(bot, config, source)
        await asyncio.sleep(config.poll_interval_seconds)

def _escaped_short(text, budget):
    text = str(text)
    if len(escape(text)) <= budget:
        return escape(text)
    while len(escape(text)) + 1 > budget:
        text = text[:max(0, len(text) - max(1, len(text) // 5))]
    return escape(text) + "…"


def _html_to_text(raw: str) -> str:
    """Грубо вытаскивает текст из HTML-письма: скрипты и стили вон, теги вон,
    сущности разворачиваем. Иначе в чат уезжает вёрстка письма целиком."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_module.unescape(text)
    # Схлопываем пробелы и пустые строки, оставшиеся от вёрстки
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def format_message_text(msg: MailMessage) -> str:
    body = (msg.text or "").strip()
    if not body and msg.html:
        body = _html_to_text(msg.html)

    # Символы < > & из письма Telegram примет за разметку и отклонит сообщение целиком
    subject = escape((msg.subject or "(без темы)").strip()[:200])
    sender = _escaped_short(msg.from_, 400)
    header = f"<b>{subject}</b>\n<i>{sender}</i>"
    if not body:
        return header

    # Режем по длине уже экранированного текста: escape раздувает «<» и «&»
    # впятеро, и письмо, влезавшее в лимит до него, после него отклоняется
    # Telegram целиком - а uid уже сохранён, и письмо теряется навсегда
    room = MAX_TELEGRAM_TEXT - len(header)
    escaped = escape(body)
    if len(escaped) > room:
        while len(escape(body)) > room and len(body) > 1:
            body = body[: max(1, int(len(body) * 0.8))]
        escaped = escape(body) + "…"
    return f"{header}\n\n{escaped}"


def _preview_body(msg: MailMessage) -> str:
    """Начало письма для карточки: одним куском, без вёрстки и переносов."""
    body = (msg.text or "").strip()
    if not body and msg.html:
        body = _html_to_text(msg.html)
    # Переносы в карточке не нужны: она обязана быть коротким блоком,
    # а не повторять раскладку письма
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) <= MAIL_PREVIEW_CHARS:
        return body
    return body[:MAIL_PREVIEW_CHARS].rstrip() + "…"


def format_card_text(msg: MailMessage) -> tuple[str, bool]:
    """Карточка письма для чата и признак «есть что открывать».

    Полный текст остаётся в почте и приходит по кнопке. В чат идёт ровно то,
    по чему человек решает, касается ли его письмо: тема, отправитель, файлы
    и начало текста.
    """
    subject = _escaped_short((msg.subject or "(без темы)").strip(), 600)
    sender = _escaped_short(msg.from_, 400)
    lines = [f"<b>{subject}</b>", f"<i>{sender}</i>"]

    names = []
    budget = 800
    for attachment in msg.attachments:
        if budget < 10:
            names.append("…")
            break
        name = _escaped_short(attachment.filename or "файл", min(200, budget))
        names.append(name)
        budget -= len(name) + 2
    if names:
        lines.append(f"файлы: {', '.join(names)}")

    preview = _preview_body(msg)
    if preview:
        lines += ["", _escaped_short(preview, 1200)]

    # Открывать нечего, когда всё тело и так на экране
    has_more = preview.endswith("…") or len(escape(preview)) > 1200
    return "\n".join(lines), has_more


async def send_mail_message(
    bot: Bot,
    chat_id: int,
    msg: MailMessage,
    thread_id: int | None = None,
    *,
    preview: bool = False,
    with_attachments: bool = True,
    source_key: str = "main",
    ai_card: dict | None = None,
) -> None:
    """Отправляет письмо в чат.

    preview=True - карточка с кнопкой «читать целиком» вместо полного текста.
    with_attachments=False - файлы уже в чате (открытие по кнопке), второй раз
    их слать незачем. 
    """
    if preview:
        text, has_more = format_card_text(msg)
        keyboard = None
        if ai_card:
            # Короткая карточка (17.11, просьба группы: меньше текста). Шапка
            # «предмет · препод · до срока», до CARD_MAX_TASKS задач строкой.
            # Время письма Telegram и так показывает, файлы приходят следом
            # документами, полный текст и промпт лежат под кнопками.
            # Поля пришли от модели или из старого кеша: каждое сводим в одну
            # строку, кривое не должно срывать доставку письма (QA G1-G5, 17.11)
            def flat(value):
                return " ".join(str(value).split()) if value is not None else ""

            deadline = flat(ai_card.get("deadline"))
            subject = flat(ai_card.get("subject_short")) or flat(msg.subject) or "письмо"
            teacher = flat(ai_card.get("teacher")) or flat(msg.from_) or "без подписи"
            header = (f"<b>{_escaped_short(subject, 120)}</b> · "
                      f"{_escaped_short(teacher, 120)}")
            if deadline:
                header += " · до " + _escaped_short(deadline, 60)
            lines = [header]
            tasks = ai_card.get("tasks")
            tasks = [t for t in tasks if isinstance(t, dict)] if isinstance(tasks, list) else []
            for task in tasks[:CARD_MAX_TASKS]:
                line = flat(task.get("what"))
                due = flat(task.get("due"))
                if due and due.casefold() != deadline.casefold():
                    line += " · до " + due
                lines.append("• " + _escaped_short(line, CARD_TASK_CHARS))
            if len(tasks) > CARD_MAX_TASKS:
                lines.append(f"• …и ещё {len(tasks) - CARD_MAX_TASKS}")
            summary = flat(ai_card.get("summary"))
            if not tasks and summary:
                lines.append(_escaped_short(summary, CARD_TASK_CHARS))
            text = "\n".join(lines)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Читать целиком", callback_data=f"mo:{int(msg.uid)}:-1:{source_key}"),
                InlineKeyboardButton(text="промпт на старт", callback_data=f"ms:{source_key}:{int(msg.uid)}")]])
        elif has_more:
            # offset -1: кнопки «к списку» у такого письма не будет, списка не было.
            # Ключ ящика в хвосте: uid уникален внутри ящика, но не между ними,
            # Без метки письмо одного ящика открывалось бы в другом.
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="Читать целиком", callback_data=f"mo:{int(msg.uid)}:-1:{source_key}"
                )
            ]])
        await bot.send_message(chat_id, text, message_thread_id=thread_id, reply_markup=keyboard)
    else:
        await bot.send_message(chat_id, format_message_text(msg), message_thread_id=thread_id)

    if not with_attachments:
        return

    from aiogram.types import InputMediaDocument

    documents = []
    for attachment in msg.attachments:
        if len(attachment.payload) > MAX_TELEGRAM_DOCUMENT_BYTES:
            await bot.send_message(
                chat_id,
                f"Вложение «{_escaped_short(attachment.filename, 500)}» больше 50 МБ - не отправлено.",
                message_thread_id=thread_id,
            )
            continue
        documents.append(BufferedInputFile(
            attachment.payload, filename=attachment.filename or "attachment"
        ))
    for offset in range(0, len(documents), 10):
        batch = documents[offset:offset + 10]
        if len(batch) > 1:
            try:
                await bot.send_media_group(
                    chat_id, media=[InputMediaDocument(media=document) for document in batch],
                    message_thread_id=thread_id,
                )
                continue
            except Exception:
                logger.exception("Mail attachment album failed uid=%s; sending individually", msg.uid)
        for document in batch:
            await bot.send_document(chat_id, document, message_thread_id=thread_id)
