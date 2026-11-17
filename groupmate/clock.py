"""One configurable group timezone."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Europe/Moscow")


def configure(name):
    global TIMEZONE
    TIMEZONE = ZoneInfo(name)


def now():
    return datetime.now(TIMEZONE)


def today():
    return now().date()


def parse_time(value):
    return time.fromisoformat(value) if value else None


def seconds_until(target, reference=None):
    reference = reference or now()
    candidate = reference.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if candidate <= reference:
        candidate += timedelta(days=1)
    return max(0, candidate.timestamp() - reference.timestamp())
