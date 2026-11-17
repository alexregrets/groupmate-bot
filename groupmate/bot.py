"""Single-process Telegram application with explicitly scoped handlers."""
import asyncio
import logging
import re
from datetime import timedelta
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import BotCommand

from . import ai_client, clock, homework, mail_watcher, pings, schedule, storage, study, usage
from .config import load_config
from .ruz import RuzScheduleProvider

logger = logging.getLogger(__name__)


def build_router(config, provider, username, bot_id):
    router = Router()

    async def reply(message, text, keyboard=None):
        await pings.send_chunks(message.bot, message.chat.id, text, message.message_thread_id, keyboard)

    def permitted(message):
        return config.permits_chat(message.chat.id)

    @router.message(Command("where"))
    async def handle_where(message):
        user = message.from_user.id if message.from_user else "нет"
        await reply(message, f"chat_id={message.chat.id}\nthread_id={message.message_thread_id or 'нет'}\nuser_id={user}")

    @router.message(Command("start", "help"))
    async def handle_help(message):
        if permitted(message) or (message.from_user and message.from_user.id == config.admin_chat_id):
            await reply(message, "/where - ID для настройки\n/schedule [завтра|дата] - пары\n/week [дата] - неделя\n"
                        "/hw - домашка\n/hw завтра текст - добавить\n/cost - расходы, администратору в личку")

    @router.message(Command("schedule", "week"))
    async def handle_schedule(message):
        if not permitted(message):
            return
        command, _, argument = message.text.partition(" ")
        day = schedule.parse_day("" if argument.strip() == "неделя" else argument.strip(), clock.today())
        if day is None:
            await reply(message, "Дата: завтра, вт, 25.11 или 2026-11-25")
            return
        is_week = command.split("@")[0].lower() == "/week" or argument.strip() == "неделя"
        if is_week:
            start = schedule.monday(day)
            week = await provider.week(start)
            for offset in range(7):
                target = start + timedelta(days=offset)
                lessons = week.get(target.isoformat()) if week is not None else None
                text = schedule.format_day(target, lessons)
                tasks = homework.for_day(message.chat.id, target, message.from_user.id)
                if tasks:
                    text += "\n" + homework.format_items(tasks)
                await reply(message, text)
        else:
            lessons = await schedule.day_lessons(provider, day)
            text = schedule.format_day(day, lessons)
            tasks = homework.for_day(message.chat.id, day, message.from_user.id)
            if tasks:
                text += "\n" + homework.format_items(tasks)
            await reply(message, text)

    @router.message(Command("hw"))
    async def handle_homework(message):
        if not permitted(message) or not message.from_user:
            return
        argument = message.text.partition(" ")[2].strip()
        if argument:
            token, _, text = argument.partition(" ")
            day = schedule.parse_day(token, clock.today())
            if day is None:
                await reply(message, "Добавить: /hw завтра прочитать главу 3")
                return
            if text.strip():
                task_id = homework.add(message.chat.id, day, text, "вручную",
                    source=f"chat:{message.chat.id}:{message.message_id}")
                await reply(message, f"Записано #{task_id} на {day:%d.%m.%Y}")
                return
            items = homework.for_day(message.chat.id, day, message.from_user.id)
        else:
            items = homework.upcoming(message.chat.id, user_id=message.from_user.id)
        await reply(message, homework.format_items(items), study.done_keyboard(items))

    @router.message(Command("cost"))
    async def handle_cost(message):
        if message.chat.type != "private" or not message.from_user or message.from_user.id != config.admin_chat_id:
            return
        text = usage.format_summary()
        key = await usage.key_status(config)
        if key and key.get("limit_remaining") is not None:
            text += f"\nОстаток ключа: ${key['limit_remaining']:.2f}"
        await reply(message, escape(text))

    @router.callback_query(F.data.startswith("done:"))
    async def handle_done(callback):
        if not callback.message or not permitted(callback.message):
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            task_id = int(callback.data.split(":")[1])
        except (ValueError, IndexError):
            await callback.answer("Неверная кнопка")
            return
        result = homework.mark_done(callback.message.chat.id, task_id, callback.from_user.id)
        await callback.answer("Отмечено только для тебя" if result else "Задание не найдено")

    @router.callback_query(F.data.startswith("mo:" ) | F.data.startswith("ms:"))
    async def handle_mail_button(callback):
        if not callback.message:
            await callback.answer("Письмо недоступно")
            return
        try:
            parts = callback.data.split(":")
            if parts[0] == "mo" and len(parts) == 4:
                uid, source_key = int(parts[1]), parts[3]
            elif parts[0] == "ms" and len(parts) == 3:
                source_key, uid = parts[1], int(parts[2])
            else:
                raise ValueError
        except (ValueError, IndexError):
            await callback.answer("Неверная кнопка")
            return
        source = config.source_by_key(source_key)
        if not source or not any(t.chat_id == callback.message.chat.id and
                t.thread_id == callback.message.message_thread_id for t in source.targets):
            await callback.answer("Нет доступа к ящику", show_alert=True)
            return
        record = storage.get(mail_watcher.cache_key(source_key, uid))
        if not record:
            await callback.answer("Письмо отсутствует в локальном архиве", show_alert=True)
            return
        await callback.answer()
        if parts[0] == "mo":
            text = f"<b>{escape(record['subject'])}</b>\n{escape(record['sender'])}\n\n{escape(record['body'])}"
        else:
            text = escape("Помоги начать работу по заданию из письма. Сначала выдели требования и предложи один первый шаг. "
                "Не выдумывай недостающие условия.\n\n" + record["subject"] + "\n" + record["body"])
        await reply(callback.message, text)

    @router.message(F.text)
    async def handle_group_text(message):
        if not permitted(message) or not message.from_user or message.from_user.is_bot or message.text.startswith("/"):
            return
        addressed = (bool(re.search(r"(?<![\w@])@" + re.escape(username) + r"(?!\w)", message.text, re.IGNORECASE)) or
                     (message.reply_to_message and message.reply_to_message.from_user and
                      message.reply_to_message.from_user.id == bot_id))
        if config.hw_from_chat:
            card = await ai_client.extract_homework(config, message.text)
            if card is None:
                card = {"tasks": study.fallback_tasks(message.text, "", clock.today())}
            study.ingest(message.chat.id, card, f"chat:{message.chat.id}:{message.message_id}")
        if config.ai_replies and addressed:
            answer = await ai_client.ask(config, message.text)
            if answer:
                await reply(message, escape(answer))

    return router


async def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    clock.configure(config.timezone)
    storage.DB_PATH = config.state_dir / "groupmate.db"
    provider = (RuzScheduleProvider(config.ruz_group_id, config.ruz_refresh_minutes)
                if config.schedule_provider == "ruz" else schedule.JsonScheduleProvider(config.schedule_file))
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    tasks = []
    try:
        me = await bot.get_me()
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(config, provider, me.username, me.id))
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in (
            ("schedule", "Расписание дня"), ("week", "Неделя"), ("hw", "Домашка"), ("cost", "Расходы LLM"),
            ("where", "ID чата и топика"))])
        tasks = [asyncio.create_task(mail_watcher.poll_loop(bot, config, source)) for source in config.mail_sources]
        tasks.append(asyncio.create_task(pings.loop(bot, config, provider)))
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()
