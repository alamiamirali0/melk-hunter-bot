# -*- coding: utf-8 -*-
"""لایهٔ ذخیره‌سازی (SQLite): کاربران، علاقه‌مندی‌ها، تاریخچه، دیده‌بان‌ها، مخاطبین دستی."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "bot.sqlite3"
EXTRA_PATH = BASE / "data" / "extra.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    joined_at   REAL,
    last_seen   REAL,
    searches    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS favs (
    user_id     INTEGER,
    contact_id  INTEGER,
    note        TEXT DEFAULT '',
    created_at  REAL,
    PRIMARY KEY (user_id, contact_id)
);
CREATE TABLE IF NOT EXISTS history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER,
    query    TEXT,
    ts       REAL
);
CREATE TABLE IF NOT EXISTS watches (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER,
    query     TEXT,
    created_at REAL,
    last_ids  TEXT DEFAULT '[]',
    hits      INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER,
    key     TEXT,
    value   TEXT,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS manual (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    name       TEXT,
    phone      TEXT,
    tags       TEXT DEFAULT '',
    created_at REAL
);
"""


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------- users ----
    def touch_user(self, user) -> None:
        now = time.time()
        row = self.conn.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,)).fetchone()
        if row:
            self.conn.execute(
                "UPDATE users SET last_seen=?, username=?, first_name=? WHERE user_id=?",
                (now, user.username or "", user.first_name or "", user.id),
            )
        else:
            self.conn.execute(
                "INSERT INTO users(user_id,username,first_name,joined_at,last_seen) VALUES(?,?,?,?,?)",
                (user.id, user.username or "", user.first_name or "", now, now),
            )
        self.conn.commit()

    def bump_search(self, user_id: int) -> None:
        self.conn.execute("UPDATE users SET searches=searches+1 WHERE user_id=?", (user_id,))
        self.conn.commit()

    def users_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

    def all_user_ids(self) -> list[int]:
        return [r["user_id"] for r in self.conn.execute("SELECT user_id FROM users")]

    # ------------------------------------------------------------ history ---
    def add_history(self, user_id: int, query: str) -> None:
        if not query or len(query) > 200:
            return
        self.conn.execute("INSERT INTO history(user_id,query,ts) VALUES(?,?,?)", (user_id, query, time.time()))
        self.conn.execute(
            "DELETE FROM history WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT 40)",
            (user_id, user_id),
        )
        self.conn.commit()

    def history(self, user_id: int, n: int = 12) -> list[str]:
        rows = self.conn.execute(
            "SELECT query FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, n)
        ).fetchall()
        out, seen = [], set()
        for r in rows:
            q = r["query"]
            if q not in seen:
                seen.add(q)
                out.append(q)
        return out

    # --------------------------------------------------------------- favs ---
    def is_fav(self, user_id: int, cid: int) -> bool:
        return bool(self.conn.execute(
            "SELECT 1 FROM favs WHERE user_id=? AND contact_id=?", (user_id, cid)).fetchone())

    def toggle_fav(self, user_id: int, cid: int, note: str = "") -> bool:
        if self.is_fav(user_id, cid):
            self.conn.execute("DELETE FROM favs WHERE user_id=? AND contact_id=?", (user_id, cid))
            self.conn.commit()
            return False
        self.conn.execute(
            "INSERT INTO favs(user_id,contact_id,note,created_at) VALUES(?,?,?,?)",
            (user_id, cid, note, time.time()),
        )
        self.conn.commit()
        return True

    def fav_ids(self, user_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT contact_id FROM favs WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [r["contact_id"] for r in rows]

    def fav_count(self, user_id: int) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM favs WHERE user_id=?", (user_id,)).fetchone()["c"]

    # ----------------------------------------------------------- settings ---
    def set(self, user_id: int, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO settings(user_id,key,value) VALUES(?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
            (user_id, key, json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

    def get(self, user_id: int, key: str, default=None):
        row = self.conn.execute(
            "SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, key)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    # ------------------------------------------------------------ watches --
    def add_watch(self, user_id: int, query: str, last_ids: list[int]) -> int:
        cur = self.conn.execute(
            "INSERT INTO watches(user_id,query,created_at,last_ids) VALUES(?,?,?,?)",
            (user_id, query, time.time(), json.dumps(last_ids[:400])),
        )
        self.conn.commit()
        return cur.lastrowid

    def watches(self, user_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM watches WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()

    def all_watches(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM watches").fetchall()

    def update_watch(self, wid: int, last_ids: list[int], hits: int = 0) -> None:
        self.conn.execute(
            "UPDATE watches SET last_ids=?, hits=hits+? WHERE id=?",
            (json.dumps(last_ids[:400]), hits, wid),
        )
        self.conn.commit()

    def del_watch(self, wid: int, user_id: int) -> None:
        self.conn.execute("DELETE FROM watches WHERE id=? AND user_id=?", (wid, user_id))
        self.conn.commit()

    # ------------------------------------------------------- manual adds ----
    def add_manual(self, user_id: int, name: str, phone: str, tags: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO manual(user_id,name,phone,tags,created_at) VALUES(?,?,?,?,?)",
            (user_id, name, phone, tags, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def manual_all(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM manual").fetchall()
        return [dict(r) for r in rows]

    def manual_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM manual").fetchone()["c"]

    def stats(self) -> dict:
        g = lambda q: self.conn.execute(q).fetchone()["c"]  # noqa: E731
        return {
            "users": g("SELECT COUNT(*) c FROM users"),
            "favs": g("SELECT COUNT(*) c FROM favs"),
            "watches": g("SELECT COUNT(*) c FROM watches"),
            "searches": g("SELECT COUNT(*) c FROM history"),
            "manual": g("SELECT COUNT(*) c FROM manual"),
        }


# --------------------------------------------------- مخاطبین اضافه‌شده ------
def load_extra() -> list[dict]:
    if EXTRA_PATH.exists():
        try:
            return json.loads(EXTRA_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_extra(items: list[dict]) -> None:
    EXTRA_PATH.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def merge_extra(new_items: list[dict]) -> dict:
    """ادغام مخاطبین جدید با موجودی؛ بر اساس شماره، تکراری‌ها حذف/به‌روز می‌شوند."""
    from melkbot.normalize import digits_only, parse_phone

    cur = load_extra()
    index = {}
    for it in cur:
        d = digits_only(it.get("phone", ""))
        if d:
            index[d] = it
    added, updated = 0, 0
    for it in new_items:
        p = parse_phone(it.get("phone", ""))
        d = p["e164"] or digits_only(it.get("phone", ""))
        if not d or len(digits_only(d)) < 6:
            continue
        rec = {
            "name": (it.get("name") or "").strip()[:120],
            "phone": p["e164"] or it.get("phone", ""),
            "tags": (it.get("tags") or "").strip()[:200],
            "source": it.get("source", "آپلود"),
        }
        key = digits_only(rec["phone"])
        if key in index:
            old = index[key]
            if rec["name"] and rec["name"] != old.get("name"):
                old["name"] = rec["name"]
                updated += 1
            if rec["tags"]:
                old["tags"] = ((old.get("tags") or "") + " " + rec["tags"]).strip()
        else:
            index[key] = rec
            added += 1
    save_extra(list(index.values()))
    return {"added": added, "updated": updated, "total": len(index)}
