import pytest

from groupmate import ai_client, clock, mail_watcher, storage


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "groupmate.db")
    monkeypatch.setattr(mail_watcher, "_poll_locks", {})
    monkeypatch.setattr(ai_client, "_lock", asyncio.Lock())
    clock.configure("Europe/Moscow")
    yield
    clock.configure("Europe/Moscow")
