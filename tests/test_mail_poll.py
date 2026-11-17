"""Mail first-run, retry and durable multi-target delivery contracts."""
import asyncio
import imaplib
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from groupmate import ai_client, homework, mail_watcher as mail, storage
from groupmate.config import Config, MailSource, MailTarget


def message(uid="42"):
    return NS(uid=uid, subject="Практика", from_="teacher@example.test", text="Решить кейс до 18.11",
              html="", attachments=[], date=datetime(2026, 11, 17))


def source(key="main", targets=None):
    return MailSource(key, "imap.example.test", "group@example.test", "test-password",
                      targets=targets or (MailTarget(-200, 5),))


@pytest.mark.parametrize("error", [TimeoutError, imaplib.IMAP4.abort, ConnectionResetError])
def test_imap_retries_three_attempts_then_succeeds(monkeypatch, error, caplog):
    expected = mail.FetchResult([], 42)
    fetch = Mock(side_effect=[error("temporary"), error("temporary"), expected])
    pause = AsyncMock()
    monkeypatch.setattr(mail, "_fetch_new_messages", fetch)
    monkeypatch.setattr(mail.asyncio, "sleep", pause)
    assert asyncio.run(mail.fetch_with_retry(Config("token", -200), source())) == expected
    assert fetch.call_count == 3
    assert [c.args[0] for c in pause.call_args_list] == [2, 4]
    assert "attempt=2/3" in caplog.text


def test_exhausted_imap_does_not_advance_checkpoint(monkeypatch, caplog):
    fetch = Mock(side_effect=TimeoutError("private data must not appear"))
    monkeypatch.setattr(mail, "_fetch_new_messages", fetch)
    monkeypatch.setattr(mail.asyncio, "sleep", AsyncMock())
    storage.save_last_uid(41, "main")
    asyncio.run(mail.poll_once(NS(send_message=AsyncMock()), Config("token", -200), source()))
    assert fetch.call_count == 3
    assert storage.load_last_uid("main") == 41
    assert "checkpoint preserved" in caplog.text
    assert "private data" not in caplog.text


def test_poll_loop_survives_exhausted_cycle(monkeypatch):
    fetch = AsyncMock(side_effect=[TimeoutError(), mail.FetchResult([], 2)])
    monkeypatch.setattr(mail, "fetch_with_retry", fetch)
    cycles = [0]
    async def pause(_):
        cycles[0] += 1
        if cycles[0] == 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(mail.asyncio, "sleep", pause)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(mail.poll_loop(NS(), Config("token", -200), source()))
    assert storage.load_last_uid("main") == 2


def test_partial_target_failure_retries_only_pending_target(monkeypatch):
    src = source(targets=(MailTarget(-200, 5), MailTarget(-300, 7)))
    cfg = Config("token", -200, (src,))
    monkeypatch.setattr(mail, "fetch_with_retry", AsyncMock(return_value=mail.FetchResult([message()], 42)))
    send = AsyncMock(side_effect=[None, RuntimeError("telegram unavailable"), None])
    monkeypatch.setattr(mail, "send_mail_message", send)
    make = AsyncMock(return_value=None)
    monkeypatch.setattr(ai_client, "make_mail_card", make)
    asyncio.run(mail.poll_once(NS(), cfg, src))
    assert storage.load_last_uid("main") == 0
    asyncio.run(mail.poll_once(NS(), cfg, src))
    assert storage.load_last_uid("main") == 42
    assert [c.args[1] for c in send.call_args_list] == [-200, -300, -300]
    assert len(homework.for_day(-200, datetime(2026, 11, 18).date())) == 1
    assert len(homework.for_day(-300, datetime(2026, 11, 18).date())) == 1
    make.assert_awaited_once()


def test_each_source_uses_independent_uids_and_cache(monkeypatch):
    monkeypatch.setattr(mail, "fetch_with_retry", AsyncMock(return_value=mail.FetchResult([message("7")], 7)))
    monkeypatch.setattr(mail, "send_mail_message", AsyncMock())
    for key in ("main", "second"):
        asyncio.run(mail.poll_once(NS(), Config("token", -200), source(key)))
    assert storage.load_last_uid("main") == storage.load_last_uid("second") == 7
    assert storage.get("mail:main:7") and storage.get("mail:second:7")


class Mailbox:
    def __init__(self, uids, messages=()):
        self.ids, self.messages = uids, messages
        self.fetch_calls = []

    def login(self, *a, **kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def uids(self, criteria):
        return self.ids

    def fetch(self, **kwargs):
        self.fetch_calls.append(kwargs)
        return self.messages


def test_first_run_does_not_download_history(monkeypatch):
    mailbox = Mailbox(["1", "42"])
    monkeypatch.setattr(mail, "MailBox", lambda *a, **kw: mailbox)
    result = mail._fetch_new_messages(Config("token", -200), source())
    assert result.to_forward == [] and result.max_uid_seen == 42
    assert mailbox.fetch_calls == []


def test_empty_first_run_does_not_skip_first_future_mail(monkeypatch):
    mailbox = Mailbox([])
    monkeypatch.setattr(mail, "MailBox", lambda *a, **kw: mailbox)
    monkeypatch.setattr(mail, "send_mail_message", AsyncMock())
    cfg, src = Config("token", -200), source()
    asyncio.run(mail.poll_once(NS(), cfg, src))
    assert storage.get("initialized:main") is True
    mailbox.ids, mailbox.messages = ["1"], [message("1")]
    asyncio.run(mail.poll_once(NS(), cfg, src))
    mail.send_mail_message.assert_awaited_once()
    assert storage.load_last_uid("main") == 1


def test_imap_last_uid_star_never_replays_last_message(monkeypatch):
    storage.save_last_uid(42, "main")
    storage.put("initialized:main", True)
    mailbox = Mailbox(["42"])
    monkeypatch.setattr(mail, "MailBox", lambda *a, **kw: mailbox)
    assert mail._fetch_new_messages(Config("token", -200), source()).to_forward == []
    assert not mailbox.fetch_calls


def test_sender_filters_fetch_headers_before_body(monkeypatch):
    storage.put("initialized:main", True)
    blocked, wanted = message("1"), message("2")
    blocked.from_ = "newsletter@example.test"
    mailbox = Mailbox(["1", "2"], [blocked, wanted])
    monkeypatch.setattr(mail, "MailBox", lambda *a, **kw: mailbox)
    cfg = Config("token", -200, sender_blocklist=("newsletter",), teacher_senders=("teacher@",))
    mail._fetch_new_messages(cfg, source())
    assert mailbox.fetch_calls == [dict(uid_list=["1", "2"], headers_only=True, mark_seen=False),
                                   dict(uid_list=["2"], mark_seen=False)]
