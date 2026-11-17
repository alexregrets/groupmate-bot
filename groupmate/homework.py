"""Chat-scoped homework and individual completion markers."""
from datetime import timedelta
from html import escape

from . import clock, storage


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS homework (
        id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, due_date TEXT NOT NULL,
        subject TEXT NOT NULL, text TEXT NOT NULL, author TEXT NOT NULL, source TEXT NOT NULL,
        UNIQUE(chat_id,source))""")
    db.execute("""CREATE TABLE IF NOT EXISTS homework_done (
        homework_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(homework_id,user_id))""")


def add(chat_id, due, text, author, subject="", source=None):
    import uuid
    source = source or f"manual:{uuid.uuid4().hex}"
    with storage.connect() as db:
        _schema(db)
        db.execute("INSERT OR IGNORE INTO homework(chat_id,due_date,subject,text,author,source) VALUES (?,?,?,?,?,?)",
                   (chat_id, due.isoformat(), str(subject)[:200], str(text).strip()[:2000], author, source))
        return db.execute("SELECT id FROM homework WHERE chat_id=? AND source=?", (chat_id, source)).fetchone()[0]


def upcoming(chat_id, today=None, days=14, user_id=None):
    today = today or clock.today()
    with storage.connect() as db:
        _schema(db)
        rows = db.execute("""SELECT h.* FROM homework h WHERE chat_id=? AND due_date BETWEEN ? AND ?
            AND NOT EXISTS(SELECT 1 FROM homework_done d WHERE d.homework_id=h.id AND d.user_id=?)
            ORDER BY due_date,id""", (chat_id, today.isoformat(), (today + timedelta(days=days)).isoformat(), user_id)).fetchall()
    return [dict(row) for row in rows]


def for_day(chat_id, day, user_id=None):
    return upcoming(chat_id, day, 0, user_id)


def mark_done(chat_id, homework_id, user_id):
    with storage.connect() as db:
        _schema(db)
        if not db.execute("SELECT 1 FROM homework WHERE id=? AND chat_id=?", (homework_id, chat_id)).fetchone():
            return False
        db.execute("INSERT OR IGNORE INTO homework_done VALUES (?,?)", (homework_id, user_id))
    return True


def format_items(items):
    if not items:
        return "Домашка не записана. Добавить: /hw завтра прочитать главу 3"
    lines = ["<b>Домашка</b>"]
    for item in items:
        title = f"{item['subject']}: " if item["subject"] else ""
        lines.append(f"#{item['id']} {item['due_date']} · {escape(title + item['text'])}")
    return "\n".join(lines)
