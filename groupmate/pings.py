"""Morning/evening schedule pings with persistent daily deduplication."""
import asyncio
import logging
from datetime import timedelta

from . import clock, homework, schedule, storage, study, usage

logger = logging.getLogger(__name__)


async def build_ping(provider, chat_id, day, is_morning):
    lessons = await schedule.day_lessons(provider, day)
    tasks = homework.for_day(chat_id, day)
    if not lessons and not tasks:
        return None
    when = "Сегодня" if is_morning else "Завтра"
    text = when + "\n" + schedule.format_day(day, lessons)
    if tasks:
        text += "\n\n" + homework.format_items(tasks)
    return text


async def tick(bot, config, provider, stamp=None):
    stamp = stamp or clock.now()
    day = stamp.date()
    for morning, at in ((True, config.morning_ping), (False, config.evening_ping)):
        if not at or stamp.strftime("%H:%M") < at:
            continue
        target_time = clock.parse_time(at)
        planned = stamp.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
        if stamp - planned >= timedelta(hours=2):
            continue
        # Catch up a missed ping only within two hours of its configured time.
        target = day if morning else day + timedelta(days=1)
        key = f"ping:{config.group_chat_id}:{day}:{morning}"
        if storage.get(key):
            continue
        text = await build_ping(provider, config.group_chat_id, target, morning)
        if text:
            await send_chunks(bot, config.group_chat_id, text, config.ping_thread_id)
        storage.put(key, True)
    radar_at = config.morning_ping or "08:00"
    if radar_at <= stamp.strftime("%H:%M") < "12:00":
        await study.radar(bot, config, day)


def text_chunks(text, size=3500):
    """Split on complete escaped lines; exceptionally long lines are sent as plain text."""
    chunks, current = [], ""
    for line in text.splitlines():
        if len(line.encode("utf-16-le")) // 2 > size:
            # Callers normally cap dynamic fields; preserve safe HTML entities if splitting.
            import html
            plain = html.unescape(line.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
            while plain:
                part = plain[:size // 6]
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(html.escape(part))
                plain = plain[len(part):]
            continue
        if current and len((current + "\n" + line).encode("utf-16-le")) // 2 > size:
            chunks.append(current)
            current = ""
        current += ("\n" if current else "") + line
    if current:
        chunks.append(current)
    return chunks


async def send_chunks(bot, chat_id, text, thread_id=None, reply_markup=None):
    chunks = text_chunks(text)
    for index, chunk in enumerate(chunks):
        await bot.send_message(chat_id, chunk, message_thread_id=thread_id,
            reply_markup=reply_markup if index == len(chunks) - 1 else None)


async def loop(bot, config, provider):
    last_cost_check = 0
    while True:
        try:
            await tick(bot, config, provider)
        except Exception:
            logger.exception("Schedule/deadline tick failed; will retry")
        if clock.now().timestamp() - last_cost_check >= 3600:
            try:
                await usage.check_alert(bot, config)
            except Exception:
                logger.warning("Cost alert failed; will retry next hour")
            last_cost_check = clock.now().timestamp()
        await asyncio.sleep(30)
