"""Homework extraction and one-line deadline radar."""
import logging
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import clock, homework, storage
from .schedule import parse_day

logger = logging.getLogger(__name__)


def fallback_tasks(text, subject, reference):
    tasks = []
    for line in text.splitlines():
        if not re.search(r"(?i)сдать|решить|прочитать|подготовить|выполнить|домашн|задани", line):
            continue
        match = re.search(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}(?:\.\d{4})?)\b", line)
        due = parse_day(match.group(), reference) if match else None
        if due:
            tasks.append({"what": line.strip(), "subject": subject, "due": due.isoformat()})
    return tasks


def ingest(chat_id, card, source, reference=None):
    reference = reference or clock.today()
    tasks = card.get("tasks")
    if not isinstance(tasks, list):
        return []
    ids = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or not isinstance(task.get("what"), str) or not task["what"].strip():
            continue
        token = task.get("due") or card.get("deadline")
        due = parse_day(str(token), reference) if token else None
        if not due:
            logger.info("Homework skipped: missing or ambiguous deadline source=%s task=%s", source, index)
            continue
        ids.append(homework.add(chat_id, due, task["what"], "почта" if source.startswith("mail:") else "чат",
            str(task.get("subject") or card.get("subject_short") or ""), f"{source}:{index}"))
    return ids


def done_keyboard(items):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"сдал #{item['id']}", callback_data=f"done:{item['id']}")]
        for item in items[:50]]) if items else None


async def radar(bot, config, day=None):
    day = day or clock.today()
    if not config.radar:
        return
    targets = {config.group_chat_id: config.ping_thread_id}
    for source in config.mail_sources:
        for target in source.targets:
            targets.setdefault(target.chat_id, target.thread_id)
    for chat_id, thread_id in targets.items():
        for item in homework.for_day(chat_id, day):
            key = f"radar:{chat_id}:{day}:{item['id']}"
            if storage.get(key):
                continue
            title = item["subject"] or "Дедлайн"
            from .mail_watcher import _escaped_short
            text = "сегодня · " + _escaped_short(f"{title}: {item['text']}", 250)
            await bot.send_message(chat_id, text, message_thread_id=thread_id,
                                   reply_markup=done_keyboard([item]))
            storage.put(key, True)
