"""Explicit configuration loading. Importing modules never reads .env."""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class MailTarget:
    chat_id: int
    thread_id: int | None = None


@dataclass(frozen=True)
class MailSource:
    key: str
    imap_host: str
    imap_user: str
    imap_password: str = field(repr=False)
    imap_folder: str = "INBOX"
    targets: tuple[MailTarget, ...] = ()


@dataclass(frozen=True)
class Config:
    bot_token: str = field(repr=False)
    group_chat_id: int
    mail_sources: tuple[MailSource, ...] = ()
    admin_chat_id: int | None = None
    ping_thread_id: int | None = None
    timezone: str = "Europe/Moscow"
    state_dir: Path = Path("state")
    schedule_provider: str = "json"
    schedule_file: Path = Path("schedule.json")
    ruz_group_id: int = 0
    ruz_refresh_minutes: int = 180
    morning_ping: str = "07:30"
    evening_ping: str = "20:00"
    poll_interval_seconds: int = 60
    forward_history: bool = False
    teacher_senders: tuple[str, ...] = ()
    sender_blocklist: tuple[str, ...] = ()
    hw_from_mail: bool = True
    hw_from_chat: bool = False
    radar: bool = True
    ai_replies: bool = False
    ai_api_url: str = ""
    ai_api_key: str = field(default="", repr=False)
    ai_model: str = ""
    ai_daily_limit_usd: float = 0.5
    cost_alert_usd: float = 0.1
    ai_input_usd_per_million: float = 0.0
    ai_output_usd_per_million: float = 0.0

    @property
    def ai_enabled(self):
        return bool(self.ai_api_key and self.ai_api_url and self.ai_model)

    def source_by_key(self, key):
        return next((s for s in self.mail_sources if s.key == key), None)

    def permits_chat(self, chat_id):
        return chat_id == self.group_chat_id or any(
            t.chat_id == chat_id for s in self.mail_sources for t in s.targets)


def _thread(value):
    if value in (None, ""):
        return None
    result = int(value)
    if result <= 0:
        raise ValueError("Номер топика должен быть положительным")
    return result


def load_config():
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    def required(name):
        value = os.getenv(name, "").strip()
        if not value:
            raise ValueError(f"Задайте {name}")
        return value

    def flag(name, default=False):
        value = os.getenv(name, "1" if default else "0").strip()
        if value not in ("0", "1"):
            raise ValueError(f"{name}: допустимы 0 и 1")
        return value == "1"

    group_id = int(required("TELEGRAM_GROUP_CHAT_ID"))
    thread = _thread(os.getenv("PING_THREAD_ID"))
    mail_thread = _thread(os.getenv("MAIL_THREAD_ID")) or thread
    sources = []
    if os.getenv("IMAP_USER", "").strip():
        sources.append(MailSource("main", required("IMAP_HOST"), required("IMAP_USER"),
            required("IMAP_PASSWORD"), os.getenv("IMAP_FOLDER", "INBOX"),
            (MailTarget(group_id, mail_thread),)))
    for item in json.loads(os.getenv("MAIL_SOURCES", "[]")):
        sources.append(MailSource(item["key"], item["imap_host"], item["imap_user"],
            item["imap_password"], item.get("imap_folder", "INBOX"),
            tuple(MailTarget(int(t["chat_id"]), _thread(t.get("thread_id")))
                  for t in item.get("targets", [{"chat_id": group_id}]))))
    keys = [s.key for s in sources]
    if len(set(keys)) != len(keys) or any(not re.fullmatch(r"[a-z0-9_]{1,16}", k) for k in keys):
        raise ValueError("Ключи ящиков: уникальные, a-z, 0-9, _, до 16 символов")
    if any(not s.targets or not s.imap_host or not s.imap_user or not s.imap_password for s in sources):
        raise ValueError("Для каждого ящика нужны IMAP-реквизиты и хотя бы один target")
    zone = os.getenv("TIMEZONE", "Europe/Moscow")
    ZoneInfo(zone)
    provider = os.getenv("SCHEDULE_PROVIDER", "json")
    group = int(os.getenv("RUZ_GROUP_ID", "0"))
    if provider not in ("json", "ruz") or (provider == "ruz" and group <= 0):
        raise ValueError("SCHEDULE_PROVIDER: json или ruz; для ruz задайте RUZ_GROUP_ID")
    morning, evening = os.getenv("MORNING_PING", "07:30"), os.getenv("EVENING_PING", "20:00")
    from datetime import time
    for value in (morning, evening):
        if value:
            time.fromisoformat(value)
            if not re.fullmatch(r"\d{2}:\d{2}", value):
                raise ValueError("Время пинга: HH:MM или пусто")
    budget = float(os.getenv("AI_DAILY_LIMIT_USD", "0.5"))
    threshold = float(os.getenv("COST_ALERT_USD", "0.1"))
    input_price = float(os.getenv("AI_INPUT_USD_PER_MILLION", "0"))
    output_price = float(os.getenv("AI_OUTPUT_USD_PER_MILLION", "0"))
    import math
    if any(not math.isfinite(x) or x < 0 for x in (budget, threshold, input_price, output_price)):
        raise ValueError("Лимиты и цены должны быть конечными неотрицательными числами")
    ai_url, ai_key, ai_model = (os.getenv(name, "").strip() for name in ("AI_API_URL", "AI_API_KEY", "AI_MODEL"))
    if any((ai_url, ai_key, ai_model)) and not all((ai_url, ai_key, ai_model)):
        raise ValueError("Для ИИ задайте вместе AI_API_URL, AI_API_KEY и AI_MODEL")
    if ai_url:
        from urllib.parse import urlsplit
        parsed = urlsplit(ai_url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("AI_API_URL должен быть HTTP(S) URL без реквизитов внутри адреса")
    return Config(required("TELEGRAM_BOT_TOKEN"), group_id, tuple(sources),
        admin_chat_id=int(os.environ["ADMIN_CHAT_ID"]) if os.getenv("ADMIN_CHAT_ID") else None,
        ping_thread_id=thread, timezone=zone, state_dir=Path(os.getenv("STATE_DIR", "state")),
        schedule_provider=provider, schedule_file=Path(os.getenv("SCHEDULE_FILE", "schedule.json")),
        ruz_group_id=group, ruz_refresh_minutes=max(1, int(os.getenv("RUZ_REFRESH_MINUTES", "180"))),
        morning_ping=morning, evening_ping=evening,
        poll_interval_seconds=max(1, int(os.getenv("POLL_INTERVAL_SECONDS", "60"))),
        forward_history=flag("FORWARD_HISTORY"), hw_from_mail=flag("HW_FROM_MAIL", True),
        hw_from_chat=flag("HW_FROM_CHAT"), radar=flag("RADAR", True), ai_replies=flag("AI_REPLIES"),
        teacher_senders=tuple(x.strip().lower() for x in os.getenv("TEACHER_SENDER_FILTER", "").split(",") if x.strip()),
        sender_blocklist=tuple(x.strip().lower() for x in os.getenv("SENDER_BLOCKLIST", "").split(",") if x.strip()),
        ai_api_url=ai_url, ai_api_key=ai_key,
        ai_model=ai_model, ai_daily_limit_usd=budget, cost_alert_usd=threshold,
        ai_input_usd_per_million=input_price, ai_output_usd_per_million=output_price)
