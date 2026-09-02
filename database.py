"""
Слой работы с базой данных.

Хранилище — SQLite (через aiosqlite), этого достаточно для одного бота на сервере
и не требует поднимать отдельную СУБД. При росте нагрузки можно перейти на
PostgreSQL, поменяв только этот файл — остальной код работает с абстракциями
"запись/выборка", а не с сырым SQL напрямую из хендлеров.
"""

import aiosqlite
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import DB_PATH, MSK_OFFSET_HOURS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT
);

CREATE TABLE IF NOT EXISTS channels (
    channel_id INTEGER PRIMARY KEY,
    title TEXT,
    owner_id INTEGER,
    added_at TEXT
);

CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER,
    channel_id INTEGER,
    channel_title TEXT,
    post_text TEXT,
    photo_file_id TEXT,
    button_text TEXT,
    winners_count INTEGER,
    draw_datetime TEXT,      -- строка "ДД.ММ.ГГГГ ЧЧ:ММ" по МСК, как ввёл админ
    status TEXT DEFAULT 'draft',   -- draft | awaiting_channel_post | published | finished
    message_id INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    giveaway_id INTEGER,
    user_id INTEGER,
    username TEXT,
    first_name TEXT,
    joined_at TEXT,
    UNIQUE(giveaway_id, user_id)
);

CREATE TABLE IF NOT EXISTS winners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    giveaway_id INTEGER,
    user_id INTEGER,
    username TEXT,
    notified_at TEXT,
    reminder_sent_at TEXT,
    UNIQUE(giveaway_id, user_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # Миграция для уже существующих баз: добавляем новые колонки, если их ещё нет.
        # source_chat_id/source_message_id хранят, ГДЕ лежит оригинальное сообщение
        # с постом (в личке админа с ботом) — чтобы публиковать его через copyMessage
        # и сохранить всё форматирование и кастомные эмодзи как есть.
        for column, coltype in [
            ("source_chat_id", "INTEGER"),
            ("source_message_id", "INTEGER"),
            ("awaiting_since", "TEXT"),  # когда розыгрыш перешёл в статус "ждём пересылку в канал"
        ]:
            try:
                await db.execute(f"ALTER TABLE giveaways ADD COLUMN {column} {coltype}")
            except aiosqlite.OperationalError:
                pass  # колонка уже существует — это нормально при повторном запуске
        await db.commit()


# ---------- users ----------

async def upsert_user(user_id: int, username: Optional[str], first_name: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (user_id, username, first_name),
        )
        await db.commit()


async def get_username_by_owner(owner_id: int) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT username FROM users WHERE user_id = ?", (owner_id,))
        row = await cur.fetchone()
        return row[0] if row else None


# ---------- channels ----------

async def upsert_channel(channel_id: int, title: str, owner_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO channels (channel_id, title, owner_id, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title, owner_id=excluded.owner_id",
            (channel_id, title, owner_id, _now()),
        )
        await db.commit()


# ---------- giveaways ----------

async def create_giveaway_draft(owner_id: int, channel_id: int, channel_title: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO giveaways (owner_id, channel_id, channel_title, status, created_at) "
            "VALUES (?, ?, ?, 'draft', ?)",
            (owner_id, channel_id, channel_title, _now()),
        )
        await db.commit()
        return cur.lastrowid


async def update_giveaway(giveaway_id: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [giveaway_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE giveaways SET {cols} WHERE id = ?", values)
        await db.commit()


async def get_giveaway(giveaway_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_awaiting_giveaway_for_channel(channel_id: int) -> Optional[dict]:
    """
    Ищет розыгрыш в статусе 'awaiting_channel_post' для этого канала — то есть тот,
    для которого создатель уже прошёл мастер и вот-вот перешлёт готовый пост в канал.
    Берём самый свежий, если их вдруг несколько (на практике такое не должно случаться).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM giveaways WHERE channel_id = ? AND status = 'awaiting_channel_post' "
            "ORDER BY id DESC LIMIT 1",
            (channel_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_giveaways_by_owner(owner_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM giveaways WHERE owner_id = ? ORDER BY id DESC", (owner_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def list_all_giveaways() -> list[dict]:
    """Для админ-панели: все розыгрыши во всех каналах, независимо от того, кто их создал."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM giveaways ORDER BY id DESC")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ---------- participants ----------

async def add_participant(giveaway_id: int, user_id: int, username: Optional[str], first_name: Optional[str]) -> bool:
    """Возвращает True, если участник добавлен впервые, False — если уже участвовал."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO participants (giveaway_id, user_id, username, first_name, joined_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (giveaway_id, user_id, username, first_name, _now()),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def count_participants(giveaway_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM participants WHERE giveaway_id = ?", (giveaway_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else 0


async def list_participants(giveaway_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM participants WHERE giveaway_id = ? ORDER BY joined_at", (giveaway_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def find_participant(giveaway_id: int, query: str) -> Optional[dict]:
    """Ищет участника розыгрыша по @username (с @ или без) или по числовому id."""
    query = query.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if query.startswith("@"):
            query = query[1:]
        if query.isdigit():
            cur = await db.execute(
                "SELECT * FROM participants WHERE giveaway_id = ? AND user_id = ?",
                (giveaway_id, int(query)),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM participants WHERE giveaway_id = ? AND username = ? COLLATE NOCASE",
                (giveaway_id, query),
            )
        row = await cur.fetchone()
        return dict(row) if row else None


async def channel_stats_by_owner(owner_id: int) -> list[dict]:
    """Сколько участников набрал каждый канал владельца — суммарно по всем его розыгрышам."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT g.channel_id, g.channel_title, COUNT(p.id) as participants
            FROM giveaways g
            LEFT JOIN participants p ON p.giveaway_id = g.id
            WHERE g.owner_id = ?
            GROUP BY g.channel_id
            ORDER BY participants DESC
            """,
            (owner_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def channel_stats_all() -> list[dict]:
    """Для админ-панели: сводка по всем каналам всех владельцев сразу."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT g.channel_id, g.channel_title, COUNT(p.id) as participants
            FROM giveaways g
            LEFT JOIN participants p ON p.giveaway_id = g.id
            GROUP BY g.channel_id
            ORDER BY participants DESC
            """
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ---------- winners ----------

async def add_winner(giveaway_id: int, user_id: int, username: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO winners (giveaway_id, user_id, username, notified_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(giveaway_id, user_id) DO UPDATE SET notified_at=excluded.notified_at",
            (giveaway_id, user_id, username, _now()),
        )
        await db.commit()


async def list_winners(giveaway_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM winners WHERE giveaway_id = ? ORDER BY notified_at", (giveaway_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def mark_reminder_sent(giveaway_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE winners SET reminder_sent_at = ? WHERE giveaway_id = ? AND user_id = ?",
            (_now(), giveaway_id, user_id),
        )
        await db.commit()


# ---------- удаление одного розыгрыша ----------

async def delete_giveaway(giveaway_id: int):
    """
    Удаляет один конкретный розыгрыш и всё, что с ним связано (участников,
    победителей). Остальные розыгрыши, каналы и пользователей не трогает.
    Используется из админ-панели для точечной чистки "битых"/незавершённых лотов,
    в отличие от clear_all(), который сносит вообще всё.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM winners WHERE giveaway_id = ?", (giveaway_id,))
        await db.execute("DELETE FROM participants WHERE giveaway_id = ?", (giveaway_id,))
        await db.execute("DELETE FROM giveaways WHERE id = ?", (giveaway_id,))
        await db.commit()


# ---------- полная очистка (для /reset_all) ----------

async def clear_all():
    """
    Полностью очищает все данные: каналы, розыгрыши, участников, победителей
    и кэш пользователей. Схему таблиц не трогает — просто пустая база,
    как будто бот только что развёрнут. Необратимо, вызывается только
    из /reset_all после подтверждения администратором.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM winners")
        await db.execute("DELETE FROM participants")
        await db.execute("DELETE FROM giveaways")
        await db.execute("DELETE FROM channels")
        await db.execute("DELETE FROM users")
        # сбрасываем счётчики автоинкремента, чтобы новые id опять начинались с 1
        await db.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('winners', 'participants', 'giveaways')"
        )
        await db.commit()
