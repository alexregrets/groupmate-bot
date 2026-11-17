"""Schedule adapter contract and safe text presentation."""
import json
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Protocol

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
DAY_ALIASES = {name: i for i, name in enumerate(WEEKDAYS)}
DAY_ALIASES.update({name: i for i, name in enumerate(
    ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"))})


def monday(day):
    return day - timedelta(days=day.weekday())


class ScheduleProvider(Protocol):
    async def week(self, day: date) -> dict[str, list[dict]] | None:
        """ISO dates to lessons. Missing date or None means unknown, [] means no classes."""
        ...


def parse_day(token, today):
    token = token.strip().lower()
    if token in ("сегодня", "today", ""):
        return today
    if token in ("завтра", "tomorrow"):
        return today + timedelta(days=1)
    if token in DAY_ALIASES:
        return today + timedelta(days=(DAY_ALIASES[token] - today.weekday()) % 7)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m"):
        try:
            # Leap-day parsing needs a leap reference year before selecting the current year.
            parsed = datetime.strptime(token + ".2000" if fmt == "%d.%m" else token,
                                       "%d.%m.%Y" if fmt == "%d.%m" else fmt).date()
            if fmt == "%d.%m":
                parsed = parsed.replace(year=today.year)
                if parsed < today:
                    parsed = parsed.replace(year=today.year + 1)
            return parsed
        except ValueError:
            continue
    return None


class JsonScheduleProvider:
    def __init__(self, path):
        self.path = Path(path)

    async def week(self, day):
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
            raise ValueError("Расписание: нужен объект days с ISO-датами")
        result = {}
        start = monday(day)
        for key, lessons in data["days"].items():
            current = date.fromisoformat(key)
            if not start <= current < start + timedelta(days=7):
                continue
            if not isinstance(lessons, list):
                raise ValueError("Расписание: день должен быть списком пар")
            for lesson in lessons:
                if not isinstance(lesson, dict) or not all(isinstance(lesson.get(k), str)
                        for k in ("start", "end", "subject")):
                    raise ValueError("Пара: обязательны строки start, end, subject")
                for key_time in ("start", "end"):
                    datetime.strptime(lesson[key_time], "%H:%M")
                if lesson["end"] <= lesson["start"]:
                    raise ValueError("Конец пары должен быть позже начала")
            result[key] = sorted(lessons, key=lambda x: x["start"])
        return result


def format_day(day, lessons):
    lines = [f"<b>{WEEKDAYS[day.weekday()]} {day:%d.%m.%Y}</b>"]
    if lessons is None:
        lines.append("Данных расписания нет.")
    elif not lessons:
        lines.append("Пар нет.")
    else:
        for lesson in lessons:
            who = lesson.get("who") or [{"teacher": lesson.get("teacher", ""), "room": lesson.get("room", "")}]
            people = "; ".join(" · ".join(str(slot.get(k) or "") for k in ("teacher", "room", "subgroup")).strip(" ·")
                               for slot in who)
            parts = [f"{lesson['start']}-{lesson['end']}", lesson["subject"], lesson.get("kind", ""), people]
            lines.append(escape(" · ".join(str(p) for p in parts if p)))
            for field in ("status", "note", "replaces"):
                if lesson.get(field):
                    lines.append(escape(str(lesson[field])))
    return "\n".join(lines)


async def day_lessons(provider, day):
    week = await provider.week(day)
    return week.get(day.isoformat()) if week is not None else None
