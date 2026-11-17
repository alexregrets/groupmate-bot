"""Endpoint-independent token accounting, daily budget and optional key balance."""
import logging
import math
from datetime import timedelta
from urllib.parse import urlsplit

import aiohttp

from . import clock, storage

logger = logging.getLogger(__name__)


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS ai_usage (
        stamp REAL, kind TEXT, model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, cost REAL)""")


def record(kind, model, values, config=None):
    values = values if isinstance(values, dict) else {}
    prompt = max(0, int(values.get("prompt_tokens") or 0))
    completion = max(0, int(values.get("completion_tokens") or 0))
    cost = values.get("cost")
    if cost is None and config and "prompt_tokens" in values and "completion_tokens" in values:
        if config.ai_input_usd_per_million or config.ai_output_usd_per_million:
            cost = (prompt * config.ai_input_usd_per_million + completion * config.ai_output_usd_per_million) / 1_000_000
    if not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        cost = None
    with storage.connect() as db:
        _schema(db)
        db.execute("INSERT INTO ai_usage VALUES (?,?,?,?,?,?)",
                   (clock.now().timestamp(), kind, model, prompt, completion, cost))


def summary(now=None):
    now = (now or clock.now()).astimezone(clock.TIMEZONE)
    windows = {"сегодня": now.replace(hour=0, minute=0, second=0, microsecond=0),
               "7 дней": now - timedelta(days=7), "30 дней": now - timedelta(days=30)}
    result = {}
    with storage.connect() as db:
        _schema(db)
        for label, start in windows.items():
            rows = db.execute("""SELECT kind, COUNT(*) n, SUM(prompt_tokens) p, SUM(completion_tokens) c,
                CASE WHEN COUNT(cost)=COUNT(*) THEN SUM(cost) ELSE NULL END cost
                FROM ai_usage WHERE stamp BETWEEN ? AND ? GROUP BY kind""",
                              (start.timestamp(), now.timestamp())).fetchall()
            result[label] = {r["kind"]: dict(requests=r["n"], prompt_tokens=r["p"], completion_tokens=r["c"], cost=r["cost"]) for r in rows}
    return result


def budget_remaining(config):
    rows = summary()["сегодня"].values()
    if any(r["cost"] is None for r in rows):
        return None
    return config.ai_daily_limit_usd - sum(r["cost"] for r in rows)


def allowed(config):
    remaining = budget_remaining(config)
    return remaining is not None and remaining > 0


def is_openrouter(url):
    return urlsplit(url).hostname == "openrouter.ai"


async def key_status(config):
    if not config.ai_enabled or not is_openrouter(config.ai_api_url):
        return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {config.ai_api_key}"}) as response:
                response.raise_for_status()
                data = (await response.json())["data"]
                return {key: data.get(key) for key in ("limit", "limit_remaining", "usage_daily", "usage_weekly", "usage_monthly")}
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
        logger.warning("Key balance unavailable")
        return None


async def check_alert(bot, config):
    if not config.ai_enabled or not config.admin_chat_id:
        return
    local = budget_remaining(config)
    key = await key_status(config)
    remote = key.get("limit_remaining") if key else None
    if local is not None and local > config.cost_alert_usd and (remote is None or remote > config.cost_alert_usd):
        return
    marker = f"cost-alert:{clock.today().isoformat()}"
    if storage.get(marker):
        return
    text = "LLM: стоимость неизвестна, запросы остановлены. Задайте цены токенов."
    if local is not None:
        text = f"LLM: осталось ${max(0, local):.2f} из дневного бюджета."
    if remote is not None:
        text += f" Остаток на ключе: ${remote:.2f}."
    await bot.send_message(config.admin_chat_id, text)
    storage.put(marker, True)


def format_summary():
    lines = ["Расходы LLM"]
    for window, kinds in summary().items():
        lines.append(window + ":")
        if not kinds:
            lines.append("нет запросов")
        for kind, row in kinds.items():
            cost = "стоимость неизвестна" if row["cost"] is None else f"${row['cost']:.4f}"
            lines.append(f"{kind}: {row['requests']}, токены {row['prompt_tokens']}/{row['completion_tokens']}, {cost}")
    return "\n".join(lines)
