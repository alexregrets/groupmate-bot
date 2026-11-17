"""RUZ adapter with a persistent, group-scoped cache and outage fallback."""
import asyncio
import logging
from datetime import timedelta

import aiohttp

from . import clock, storage
from .schedule import monday

logger = logging.getLogger(__name__)
API_URL = "https://ruz.fa.ru/api/schedule/group"


def parse_week(rows, day):
    start = monday(day)
    days = {(start + timedelta(days=i)).isoformat(): [] for i in range(7)}
    for row in rows:
        key = row["date"][:10]
        if key not in days:
            continue
        kind = row.get("kindOfWork") or ""
        lowered = kind.lower()
        kind = "лекция" if "лекци" in lowered else "семинар" if "практич" in lowered or "семинар" in lowered else kind
        slot = {"teacher": row.get("lecturer_title") or row.get("lecturer") or "",
                "room": row.get("auditorium") or "", "subgroup": row.get("subgroup") or ""}
        lesson = dict(pair=row.get("lessonNumberStart", 0), start=row["beginLesson"], end=row["endLesson"],
                      kind=kind, subject=row["discipline"], who=[slot], status=row.get("status"),
                      note=row.get("note"), replaces=row.get("replaces"))
        existing = next((x for x in days[key] if all(x[k] == lesson[k] for k in
                        ("start", "end", "kind", "subject", "status", "note", "replaces"))), None)
        if existing is not None:
            if slot not in existing["who"]:
                existing["who"].append(slot)
        else:
            days[key].append(lesson)
    for lessons in days.values():
        lessons.sort(key=lambda x: (x["start"], x["subject"]))
        for lesson in lessons:
            lesson["who"].sort(key=lambda x: (x["subgroup"], x["teacher"], x["room"]))
    return days


async def fetch_week(day, group_id):
    start = monday(day)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(f"{API_URL}/{group_id}", params={
            "start": start.strftime("%Y.%m.%d"), "finish": (start + timedelta(days=6)).strftime("%Y.%m.%d"), "lng": 1}) as response:
            response.raise_for_status()
            rows = await response.json()
            if not isinstance(rows, list):
                raise ValueError("RUZ response must be a list")
            return parse_week(rows, start)


class RuzScheduleProvider:
    def __init__(self, group_id, refresh_minutes=180):
        self.group_id = group_id
        self.refresh_seconds = refresh_minutes * 60
        self.lock = asyncio.Lock()
        self.failed_until = {}

    async def week(self, day):
        key = f"ruz:{self.group_id}:{monday(day).isoformat()}"
        async with self.lock:
            cached = storage.get(key)
            stamp = clock.now().timestamp()
            if cached and stamp - cached["stamp"] < self.refresh_seconds:
                return cached["days"]
            if self.failed_until.get(key, 0) > stamp:
                return cached["days"] if cached else None
            try:
                days = await fetch_week(day, self.group_id)
                storage.put(key, {"stamp": stamp, "days": days})
                return days
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError, KeyError, TypeError):
                logger.warning("RUZ update failed; using cached week %s", monday(day), exc_info=True)
                self.failed_until[key] = stamp + 60
                return cached["days"] if cached else None
