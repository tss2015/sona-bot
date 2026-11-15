#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Miss Sona - all-in-one Telegram group management and gaming bot.

Single-file deployment blueprint built on python-telegram-bot v20+/v21.x.

Modules
-------
1. Moderation & persistence : /ban /mute /unmute /warn (3 strikes -> ban),
                              /setrules /rules /setnote /note in SQLite.
2. Security & anti-spam     : unapproved http(s)/t.me link removal with a
                              temporary warning, keyword filtering and
                              PyTesseract OCR on images with repeat-offender bans.
3. Admin broadcasts         : cooldown + lock throttled @all / @admin HTML
                              mentions. @all targets every known human member
                              (active or dormant) and never bots, paced safely.
4. Captcha verification     : restrict on join, inline button, JobQueue timeout.
5. Shadow-reply             : delete admin command, resend payload as the bot.
6. Word jumble game         : /new /new4 /new5 /new6, 30 guesses, icon hints.

Quick start
-----------
    pip install -r requirements.txt          # includes [job-queue]
    sudo apt install tesseract-ocr           # system binary for OCR
    cp .env.example .env && edit .env
    python bot.py

The bot MUST be a group administrator with "Delete messages", "Ban users" and
"Restrict members" rights. Captcha/moderation require a supergroup. Disable
privacy mode via @BotFather so the bot receives ordinary group messages.

Design notes
------------
* All handlers are async; all SQLite work runs through asyncio.to_thread.
* One SQLite connection guarded by an RLock, WAL journal, idempotent schema.
* Handler groups are explicit so guesses/broadcasts never hit the text filter.
* Every Telegram API call is wrapped in targeted error handling: a missing
  permission or a deleted message must never crash the polling loop.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

try:  # Optional convenience: python-dotenv is in requirements.txt
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover - env vars work without it
    pass

from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType, MessageEntityType, ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
    import pytesseract

    OCR_AVAILABLE = True
except ImportError:  # The bot still runs; OCR/welcome cards degrade gracefully.
    OCR_AVAILABLE = False
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]
    UnidentifiedImageError = OSError  # type: ignore[assignment,misc]
    pytesseract = None  # type: ignore[assignment]

logger = logging.getLogger("miss_sona")
BOT_VERSION = "1.3.0"

DEFAULT_BANNED_WORDS = (
    "porn", "xxx", "nsfw", "escort", "phishing", "airdrop-scam",
    "nude", "casino-spam", "crypto-doubler", "free-giftcard",
)

# Welcome / goodbye defaults. {first} and {group} resolve per member and chat.
DEFAULT_WELCOME_TEXT = "Hey {first}, welcome to {group}! 🎉"
DEFAULT_GOODBYE_TEXT = "Goodbye {first}, we'll miss you in {group}! 👋"
GREETING_LIMIT = 1024
CONNECT_TOKEN_TTL = 900  # 15 minutes, single use
SUPPRESS_GOODBYE: set[tuple[int, int]] = set()  # bot-initiated kicks
WELCOME_CARD_ENABLED = os.getenv("WELCOME_CARD", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
GREETING_PLACEHOLDERS = (
    "{first} first name · {user} full name · {username} @username\n"
    "{mention} clickable mention · {group} group title · {id} user ID · {count} member count"
)

# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

def _parse_ids(raw: str) -> set[int]:
    return {int(x) for x in re.findall(r"-?\d+", raw or "")}


def _parse_csv(raw: str) -> tuple[str, ...]:
    return tuple(p.strip().lower() for p in (raw or "").split(",") if p.strip())


def _env_int(key: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(key, str(default)))))
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", key, os.getenv(key), default)
        return default


def _env_float(key: str, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(os.getenv(key, str(default)))))
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", key, os.getenv(key), default)
        return default


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration loaded once from the environment."""

    token: str
    owner_ids: frozenset[int]
    db_path: Path
    warn_limit: int
    content_ban_threshold: int
    banned_words: tuple[str, ...]
    allowed_domains: frozenset[str]
    link_whitelist: frozenset[int]
    captcha_timeout: int
    broadcast_cooldown: int
    mention_delay: float
    mention_batch_size: int
    max_mentions: int  # 0 means every known member (no cap)
    max_guesses: int
    ocr_lang: str
    ocr_max_bytes: int
    tesseract_cmd: str
    error_chat_id: Optional[int]
    words_file: Optional[Path]

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token or ":" not in token:
            raise SystemExit("BOT_TOKEN is missing. Set it in .env (from @BotFather).")
        raw_chat = os.getenv("ERROR_CHAT_ID", "").strip()
        words_file = os.getenv("WORDS_FILE", "").strip()
        return cls(
            token=token,
            owner_ids=frozenset(_parse_ids(os.getenv("OWNER_IDS", ""))),
            db_path=Path(os.getenv("DB_PATH", "bot_data.db")).resolve(),
            warn_limit=_env_int("WARN_LIMIT", 3, 1, 20),
            content_ban_threshold=_env_int("CONTENT_BAN_THRESHOLD", 2, 1, 20),
            banned_words=_parse_csv(os.getenv("BANNED_WORDS", ",".join(DEFAULT_BANNED_WORDS))),
            allowed_domains=frozenset(_parse_csv(os.getenv("ALLOWED_DOMAINS", "github.com,stackoverflow.com"))),
            link_whitelist=frozenset(_parse_ids(os.getenv("LINK_WHITELIST", ""))),
            captcha_timeout=_env_int("CAPTCHA_TIMEOUT", 120, 30, 900),
            broadcast_cooldown=_env_int("BROADCAST_COOLDOWN", 300, 10, 3600),
            mention_delay=_env_float("MENTION_DELAY", 3.0, 1.0, 30.0),
            mention_batch_size=_env_int("MENTION_BATCH_SIZE", 1, 1, 20),
            # 0 = unlimited: mention the entire known roster, active or dormant.
            max_mentions=_env_int("MAX_MENTIONS", 0, 0, 500),
            max_guesses=_env_int("MAX_GUESSES", 30, 5, 60),
            ocr_lang=os.getenv("OCR_LANG", "eng").strip() or "eng",
            ocr_max_bytes=_env_int("OCR_MAX_BYTES", 8 * 1024 * 1024, 1024, 20 * 1024 * 1024),
            tesseract_cmd=os.getenv("TESSERACT_CMD", "").strip(),
            error_chat_id=int(raw_chat) if raw_chat.lstrip("-").isdigit() else None,
            words_file=Path(words_file) if words_file else None,
        )


# --------------------------------------------------------------------------- #
# Word bank for the guessing game                                              #
# --------------------------------------------------------------------------- #

WORD_BANK: dict[int, tuple[str, ...]] = {
    3: ("ace", "arc", "ash", "bee", "cat", "cub", "dog", "dot", "elm", "fog",
        "gem", "ice", "ink", "joy", "key", "map", "oak", "owl", "pen", "pie",
        "ray", "sun", "tea", "toy", "van", "web", "zip", "ant", "bus", "cup"),
    4: ("atom", "blue", "calm", "dawn", "echo", "fire", "glow", "hero", "iris",
        "jazz", "kite", "luna", "mint", "nova", "opal", "pine", "quiz", "rain",
        "star", "tide", "unit", "vibe", "wave", "yarn", "zone", "bolt", "clay",
        "dusk", "fern", "gold", "harp", "isle", "jade"),
    5: ("amber", "blaze", "brave", "cloud", "crane", "delta", "eagle", "flame",
        "glass", "grape", "haven", "ivory", "jelly", "knack", "lemon", "mango",
        "nexus", "ocean", "pixel", "quilt", "raven", "scout", "tiger", "umber",
        "vivid", "wheat", "xenon", "yacht", "zebra", "prism", "spark", "stone",
        "swift", "tulip", "ultra", "vault", "waltz", "young", "zesty"),
    6: ("astral", "banana", "canyon", "desert", "engine", "falcon", "garden",
        "harbor", "island", "jungle", "kitten", "lagoon", "marble", "nectar",
        "orange", "planet", "quiver", "rocket", "saddle", "temple", "unison",
        "velvet", "walnut", "yellow", "zephyr", "breeze", "cobalt", "dragon",
        "forest", "meadow", "pebble", "ripple", "sunset", "willow", "cactus",
        "pickle", "puzzle", "silver", "violet", "wonder", "anchor", "bubble",
        "carpet"),
    7: ("galaxy", "harmony", "journey", "lantern", "mystery", "crystal",
        "compass", "diamond", "eclipse", "freedom", "granite", "horizon",
        "islands", "kitchen", "library", "machine", "natural", "oceanic",
        "panther", "quality", "rainbow", "sunrise", "village", "whisper"),
    8: ("adventure", "bluebird", "campfire", "daylight", "elephant", "firework",
        "gardener", "mountain", "notebook", "overture", "paintbox", "sandwich",
        "umbrella", "sunlight", "treasure", "keyboard", "portrait", "festival",
        "midnight"),
}


def load_word_bank(custom: Optional[Path]) -> dict[int, tuple[str, ...]]:
    """Optionally replace/extend the built-in bank with a JSON file.

    Accepted formats: {"4": ["word", ...], "5": [...]} or ["word", ...].
    """
    bank = {length: tuple(words) for length, words in WORD_BANK.items()}
    if not custom:
        return bank
    try:
        data = json.loads(Path(custom).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read WORDS_FILE %s: %s", custom, exc)
        return bank
    raw_items: Iterable[tuple[Any, Any]]
    raw_items = data.items() if isinstance(data, dict) else ((None, w) for w in data)
    for key, value in raw_items:
        if not isinstance(value, (list, tuple)):
            continue
        cleaned = tuple(str(w).strip().lower() for w in value if isinstance(w, str) and w.isalpha())
        if key is None:  # flat list: infer lengths
            for word in cleaned:
                bank.setdefault(len(word), ())
                bank[len(word)] = bank[len(word)] + (word,)
        elif str(key).isdigit() and cleaned:
            bank[int(key)] = cleaned
    return {k: v for k, v in bank.items() if v}


# --------------------------------------------------------------------------- #
# Text normalisation and keyword matching                                      #
# --------------------------------------------------------------------------- #

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_CONFUSABLES = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s",
                              "7": "t", "@": "a", "$": "s", "!": "i"})


def normalize(text: str) -> str:
    """NFKC + confusable folding so 'p0rn' and 'p o r n' are still detectable."""
    text = unicodedata.normalize("NFKC", text or "").translate(_ZERO_WIDTH)
    text = text.translate(_CONFUSABLES).casefold()
    text = re.sub(r"[^\w\s]+", " ", text)
    return " ".join(text.split())


class KeywordMatcher:
    """Word-boundary matcher over normalised text (safe for multi-word terms)."""

    def __init__(self, words: Iterable[str]) -> None:
        self.patterns: list[tuple[str, re.Pattern[str]]] = []
        for raw in words:
            needle = normalize(raw)
            if needle:
                self.patterns.append(
                    (raw, re.compile(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", re.IGNORECASE))
                )

    def find(self, text: str) -> Optional[str]:
        if not text or not self.patterns:
            return None
        haystack = normalize(text)
        for raw, pattern in self.patterns:
            if pattern.search(haystack):
                return raw
        return None


# --------------------------------------------------------------------------- #
# SQLite persistence                                                          #
# --------------------------------------------------------------------------- #

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rules (
    chat_id     INTEGER PRIMARY KEY,
    text        TEXT NOT NULL,
    updated_by  INTEGER NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    chat_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    text        TEXT NOT NULL,
    updated_by  INTEGER NOT NULL,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (chat_id, name)
);
CREATE TABLE IF NOT EXISTS warnings (
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS content_strikes (
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS link_trust (
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    granted_by  INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
-- Persistent membership roster. Rows are kept forever (activity history) but
-- in_chat tracks presence: 1 = still in the group, 0 = left/kicked/not added.
CREATE TABLE IF NOT EXISTS activity (
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    username    TEXT NOT NULL DEFAULT '',
    is_bot      INTEGER NOT NULL DEFAULT 0,
    last_seen   REAL NOT NULL,
    in_chat     INTEGER NOT NULL DEFAULT 1,
    joined_at   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS captcha (
    chat_id       INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    token         TEXT NOT NULL,
    message_id    INTEGER NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS games (
    chat_id     INTEGER PRIMARY KEY,
    word        TEXT NOT NULL,
    length      INTEGER NOT NULL,
    guesses     TEXT NOT NULL DEFAULT '[]',
    started_by  INTEGER NOT NULL,
    started_at  REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS wins (
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    wins        INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS greetings (
    chat_id     INTEGER PRIMARY KEY,
    welcome     TEXT,
    goodbye     TEXT,
    updated_by  INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
    user_id      INTEGER PRIMARY KEY,
    chat_id      INTEGER NOT NULL,
    connected_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS connect_tokens (
    token      TEXT PRIMARY KEY,
    chat_id    INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bans (
    chat_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    banned_by    INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_bans_chat ON bans (chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_seen ON activity (chat_id, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_wins_chat ON wins (chat_id, wins DESC);
-- idx_activity_roster is created by _migrate_schema() AFTER legacy columns exist.
"""


class Database:
    """Small synchronous SQLite facade; every method is called off-loop."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None

    # -- infrastructure -----------------------------------------------------
    async def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute a sync DB method in the default thread pool."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA foreign_keys=ON")
                self._conn = conn
            return self._conn

    def _query(self, sql: str, params: tuple = (), fetch: Optional[str] = None) -> Any:
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(sql, params)
            if fetch == "one":
                row = cursor.fetchone()
                result = dict(row) if row else None
            elif fetch == "all":
                result = [dict(row) for row in cursor.fetchall()]
            else:
                result = None
            conn.commit()
            return result

    def init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.executescript(SCHEMA_SQL)
            self._migrate_schema(conn)
            conn.commit()
        logger.info("SQLite schema ready at %s", self.path)

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        """Idempotent upgrades for databases created by earlier versions."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(activity)")}
        if "in_chat" not in columns:
            conn.execute("ALTER TABLE activity ADD COLUMN in_chat INTEGER NOT NULL DEFAULT 1")
            logger.info("Migrated activity: added in_chat")
        if "joined_at" not in columns:
            conn.execute("ALTER TABLE activity ADD COLUMN joined_at REAL NOT NULL DEFAULT 0")
            logger.info("Migrated activity: added joined_at")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_roster "
                     "ON activity (chat_id, in_chat, is_bot)")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- rules and notes ----------------------------------------------------
    def set_rules(self, chat_id: int, text: str, updated_by: int) -> None:
        self._query(
            "INSERT INTO rules (chat_id, text, updated_by, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET text=excluded.text, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (chat_id, text, updated_by, time.time()),
        )

    def get_rules(self, chat_id: int) -> Optional[dict]:
        return self._query("SELECT text, updated_by, updated_at FROM rules WHERE chat_id=?",
                           (chat_id,), fetch="one")

    def set_note(self, chat_id: int, name: str, text: str, updated_by: int) -> None:
        self._query(
            "INSERT INTO notes (chat_id, name, text, updated_by, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id, name) DO UPDATE SET text=excluded.text, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (chat_id, name, text, updated_by, time.time()),
        )

    def get_note(self, chat_id: int, name: str) -> Optional[dict]:
        return self._query("SELECT text, updated_by FROM notes WHERE chat_id=? AND name=?",
                           (chat_id, name), fetch="one")

    def list_notes(self, chat_id: int) -> list[dict]:
        return self._query("SELECT name FROM notes WHERE chat_id=? ORDER BY name",
                           (chat_id,), fetch="all") or []

    def delete_note(self, chat_id: int, name: str) -> bool:
        with self._lock:
            conn = self._connect()
            cursor = conn.execute("DELETE FROM notes WHERE chat_id=? AND name=?", (chat_id, name))
            conn.commit()
            return cursor.rowcount > 0

    # -- strikes ------------------------------------------------------------
    def add_warn(self, chat_id: int, user_id: int) -> int:
        return self._bump("warnings", chat_id, user_id)

    def get_warns(self, chat_id: int, user_id: int) -> int:
        row = self._query("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
                          (chat_id, user_id), fetch="one")
        return int(row["count"]) if row else 0

    def reset_warns(self, chat_id: int, user_id: int) -> None:
        self._query("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    def add_content_strike(self, chat_id: int, user_id: int) -> int:
        return self._bump("content_strikes", chat_id, user_id)

    def get_content_strikes(self, chat_id: int, user_id: int) -> int:
        row = self._query("SELECT count FROM content_strikes WHERE chat_id=? AND user_id=?",
                          (chat_id, user_id), fetch="one")
        return int(row["count"]) if row else 0

    def reset_content_strikes(self, chat_id: int, user_id: int) -> None:
        self._query("DELETE FROM content_strikes WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    def _bump(self, table: str, chat_id: int, user_id: int) -> int:
        # table is a hard-coded internal constant, never user input.
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                f"SELECT count FROM {table} WHERE chat_id=? AND user_id=?", (chat_id, user_id)
            ).fetchone()
            count = int(row["count"] if row else 0) + 1
            conn.execute(
                f"INSERT INTO {table} (chat_id, user_id, count, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chat_id, user_id) DO UPDATE SET count=excluded.count, "
                "updated_at=excluded.updated_at",
                (chat_id, user_id, count, time.time()),
            )
            conn.commit()
            return count

    # -- link trust ---------------------------------------------------------
    def set_link_trust(self, chat_id: int, user_id: int, granted_by: int) -> None:
        self._query(
            "INSERT OR REPLACE INTO link_trust (chat_id, user_id, granted_by, created_at) "
            "VALUES (?, ?, ?, ?)", (chat_id, user_id, granted_by, time.time()),
        )

    def remove_link_trust(self, chat_id: int, user_id: int) -> None:
        self._query("DELETE FROM link_trust WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    def is_link_trusted(self, chat_id: int, user_id: int) -> bool:
        return self._query("SELECT 1 FROM link_trust WHERE chat_id=? AND user_id=?",
                           (chat_id, user_id), fetch="one") is not None

    # -- membership roster --------------------------------------------------
    def touch_activity(self, chat_id: int, user: Any) -> None:
        """A message was seen: remember the human and mark them present."""
        now = time.time()
        self._query(
            "INSERT INTO activity "
            "(chat_id, user_id, name, username, is_bot, last_seen, in_chat, joined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?) ON CONFLICT(chat_id, user_id) DO UPDATE SET "
            "name=excluded.name, username=excluded.username, is_bot=excluded.is_bot, "
            "last_seen=excluded.last_seen, in_chat=1",
            (chat_id, user.id, getattr(user, "full_name", "") or "",
             (getattr(user, "username", "") or "").lower(),
             int(bool(getattr(user, "is_bot", False))), now, now),
        )

    def upsert_member(self, chat_id: int, user: Any, in_chat: bool = True) -> None:
        """Join or status change: add to the permanent roster.

        last_seen is deliberately not overwritten here, so a quiet member stays
        quiet in the activity metrics while still receiving @all.
        """
        now = time.time()
        self._query(
            "INSERT INTO activity "
            "(chat_id, user_id, name, username, is_bot, last_seen, in_chat, joined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id, user_id) DO UPDATE SET "
            "name=excluded.name, username=excluded.username, is_bot=excluded.is_bot, "
            "in_chat=excluded.in_chat",
            (chat_id, user.id, getattr(user, "full_name", "") or "",
             (getattr(user, "username", "") or "").lower(),
             int(bool(getattr(user, "is_bot", False))), now,
             int(in_chat), now if in_chat else 0.0),
        )

    def mark_member_left(self, chat_id: int, user_id: int) -> None:
        """Keep the history row, but never mention someone who left or was kicked."""
        self._query("UPDATE activity SET in_chat=0 WHERE chat_id=? AND user_id=?",
                    (chat_id, user_id))

    def mention_members(self, chat_id: int, limit: int = 0) -> list[dict]:
        """Everyone still in the chat, regardless of how long they have been quiet.

        Bots are ALWAYS excluded (is_bot=0) along with deleted/non-user IDs.
        limit=0 returns the full roster; a positive value caps the broadcast.
        """
        sql = ("SELECT user_id, name, username FROM activity "
               "WHERE chat_id=? AND is_bot=0 AND in_chat=1 AND user_id>0 "
               "ORDER BY last_seen DESC")
        params: tuple[Any, ...] = (chat_id,)
        if limit > 0:
            sql += " LIMIT ?"
            params += (limit,)
        return self._query(sql, params, fetch="all") or []

    def member_stats(self, chat_id: int, dormant_before: float) -> dict:
        def count(where: str, params: tuple[Any, ...]) -> int:
            row = self._query(f"SELECT COUNT(*) AS n FROM activity WHERE chat_id=? AND {where}",
                              (chat_id,) + params, fetch="one")
            return int(row["n"]) if row else 0
        return {
            "humans": count("is_bot=0 AND in_chat=1 AND user_id>0", ()),
            "bots": count("is_bot=1 AND in_chat=1", ()),
            "dormant": count("is_bot=0 AND in_chat=1 AND last_seen<?", (dormant_before,)),
            "left": count("in_chat=0", ()),
        }

    def find_by_username(self, chat_id: int, username: str) -> Optional[dict]:
        return self._query(
            "SELECT user_id, name FROM activity WHERE chat_id=? AND lower(username)=? LIMIT 1",
            (chat_id, username.lstrip("@").lower()), fetch="one",
        )

    # -- captcha ------------------------------------------------------------
    def save_captcha(self, chat_id: int, user_id: int, token: str,
                     message_id: int, display_name: str) -> None:
        self._query(
            "INSERT OR REPLACE INTO captcha "
            "(chat_id, user_id, token, message_id, display_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, user_id, token, message_id, display_name, time.time()),
        )

    def get_captcha(self, chat_id: int, user_id: int) -> Optional[dict]:
        return self._query("SELECT * FROM captcha WHERE chat_id=? AND user_id=?",
                           (chat_id, user_id), fetch="one")

    def finish_captcha(self, chat_id: int, user_id: int) -> None:
        self._query("DELETE FROM captcha WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    def pending_captchas(self) -> list[dict]:
        return self._query("SELECT * FROM captcha", fetch="all") or []

    # -- games --------------------------------------------------------------
    def get_game(self, chat_id: int) -> Optional[dict]:
        return self._query("SELECT * FROM games WHERE chat_id=?", (chat_id,), fetch="one")

    def start_game(self, chat_id: int, word: str, length: int, started_by: int) -> None:
        self._query(
            "INSERT INTO games (chat_id, word, length, guesses, started_by, started_at, status) "
            "VALUES (?, ?, ?, '[]', ?, ?, 'active') ON CONFLICT(chat_id) DO UPDATE SET "
            "word=excluded.word, length=excluded.length, guesses='[]', "
            "started_by=excluded.started_by, started_at=excluded.started_at, status='active'",
            (chat_id, word, length, started_by, time.time()),
        )

    def append_guess(self, chat_id: int, entry: dict) -> Optional[int]:
        """Atomically add one guess; returns the new total or None if no game."""
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT guesses, status FROM games WHERE chat_id=?",
                               (chat_id,)).fetchone()
            if row is None or row["status"] != "active":
                return None
            try:
                guesses = json.loads(row["guesses"])
            except (TypeError, ValueError):
                guesses = []
            guesses.append(entry)
            conn.execute("UPDATE games SET guesses=? WHERE chat_id=?",
                         (json.dumps(guesses, ensure_ascii=False), chat_id))
            conn.commit()
            return len(guesses)

    def finish_game(self, chat_id: int, status: str = "finished") -> None:
        self._query("UPDATE games SET status=? WHERE chat_id=?", (status, chat_id))

    def record_win(self, chat_id: int, user_id: int, name: str) -> int:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT wins FROM wins WHERE chat_id=? AND user_id=?",
                               (chat_id, user_id)).fetchone()
            total = int(row["wins"] if row else 0) + 1
            conn.execute(
                "INSERT INTO wins (chat_id, user_id, name, wins, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(chat_id, user_id) DO UPDATE SET name=excluded.name, "
                "wins=excluded.wins, updated_at=excluded.updated_at",
                (chat_id, user_id, name, total, time.time()),
            )
            conn.commit()
            return total

    def leaderboard(self, chat_id: int, limit: int = 5) -> list[dict]:
        return self._query("SELECT user_id, name, wins FROM wins WHERE chat_id=? "
                           "ORDER BY wins DESC LIMIT ?", (chat_id, limit), fetch="all") or []

    # -- welcome / goodbye templates ---------------------------------------
    def set_greeting(self, chat_id: int, kind: str, text: str, updated_by: int) -> None:
        if kind not in ("welcome", "goodbye"):
            raise ValueError("kind must be 'welcome' or 'goodbye'")
        column = "welcome" if kind == "welcome" else "goodbye"
        self._query(
            f"INSERT INTO greetings (chat_id, {column}, updated_by, updated_at) "
            f"VALUES (?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET "
            f"{column}=excluded.{column}, updated_by=excluded.updated_by, "
            "updated_at=excluded.updated_at",
            (chat_id, text, updated_by, time.time()),
        )

    def get_greetings(self, chat_id: int) -> dict:
        return self._query("SELECT welcome, goodbye FROM greetings WHERE chat_id=?",
                           (chat_id,), fetch="one") or {}

    def clear_greeting(self, chat_id: int, kind: str) -> bool:
        if kind not in ("welcome", "goodbye"):
            raise ValueError("kind must be 'welcome' or 'goodbye'")
        column = "welcome" if kind == "welcome" else "goodbye"
        row = self._query(f"SELECT {column} FROM greetings WHERE chat_id=?",
                          (chat_id,), fetch="one")
        existed = bool(row and row.get(column))
        self._query(f"UPDATE greetings SET {column}=NULL, updated_at=? WHERE chat_id=?",
                    (time.time(), chat_id))
        return existed

    # -- private admin console: tokens + connections -----------------------
    def create_connect_token(self, token: str, chat_id: int, created_by: int) -> None:
        self._query("DELETE FROM connect_tokens WHERE chat_id=? AND created_by=?",
                    (chat_id, created_by))
        self._query("INSERT INTO connect_tokens (token, chat_id, created_by, created_at) "
                    "VALUES (?, ?, ?, ?)", (token, chat_id, created_by, time.time()))

    def get_connect_token(self, token: str) -> Optional[dict]:
        return self._query("SELECT * FROM connect_tokens WHERE token=?",
                           (token,), fetch="one")

    def delete_connect_token(self, token: str) -> None:
        self._query("DELETE FROM connect_tokens WHERE token=?", (token,))

    def set_connection(self, user_id: int, chat_id: int) -> None:
        self._query("INSERT OR REPLACE INTO connections (user_id, chat_id, connected_at) "
                    "VALUES (?, ?, ?)", (user_id, chat_id, time.time()))

    def get_connection(self, user_id: int) -> Optional[dict]:
        return self._query("SELECT chat_id, connected_at FROM connections WHERE user_id=?",
                           (user_id,), fetch="one")

    def delete_connection(self, user_id: int) -> None:
        self._query("DELETE FROM connections WHERE user_id=?", (user_id,))

    # -- permanent-ban ledger ---------------------------------------------
    def record_ban(self, chat_id: int, user_id: int, name: str,
                   reason: str, banned_by: int) -> None:
        self._query(
            "INSERT INTO bans (chat_id, user_id, display_name, reason, banned_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id, user_id) DO UPDATE SET "
            "display_name=excluded.display_name, reason=excluded.reason, "
            "banned_by=excluded.banned_by, created_at=excluded.created_at",
            (chat_id, user_id, name, reason, banned_by, time.time()),
        )

    def clear_ban(self, chat_id: int, user_id: int) -> None:
        self._query("DELETE FROM bans WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    def list_bans(self, chat_id: int, limit: int = 30) -> list[dict]:
        return self._query("SELECT user_id, display_name, reason, banned_by, created_at "
                           "FROM bans WHERE chat_id=? ORDER BY created_at DESC LIMIT ?",
                           (chat_id, limit), fetch="all") or []

    def dashboard(self, chat_id: int) -> dict:
        def count(table: str) -> int:
            row = self._query(f"SELECT COUNT(*) AS n FROM {table} WHERE chat_id=?",
                              (chat_id,), fetch="one")
            return int(row["n"]) if row else 0
        row = self._query(
            "SELECT COUNT(*) AS n FROM activity "
            "WHERE chat_id=? AND is_bot=0 AND in_chat=1 AND user_id>0",
            (chat_id,), fetch="one")
        return {"notes": count("notes"), "warned": count("warnings"),
                "strikes": count("content_strikes"), "trusted": count("link_trust"),
                "roster": int(row["n"]) if row else 0}


# --------------------------------------------------------------------------- #
# Shared helpers                                                               #
# --------------------------------------------------------------------------- #

GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)

# PTB exposes the group owner as ChatMemberStatus.OWNER; some builds only have
# CREATOR. Hard-coding either one causes AttributeError on the other.
_OWNER_STATUS = getattr(ChatMemberStatus, "OWNER", "creator")
_CREATOR_STATUS = getattr(ChatMemberStatus, "CREATOR", _OWNER_STATUS)
_OWNER_STATUSES = frozenset({_OWNER_STATUS, _CREATOR_STATUS})
_ADMIN_STATUS = getattr(ChatMemberStatus, "ADMINISTRATOR", "administrator")
_ADMIN_STATUSES = _OWNER_STATUSES | {_ADMIN_STATUS}

BACKGROUND_TASKS: set[asyncio.Task] = set()
_ADMIN_CACHE: dict[tuple[int, int], tuple[bool, float]] = {}
BROADCAST_LAST: dict[int, float] = {}
BROADCAST_ACTIVE: set[int] = set()      # background broadcast currently running
BROADCAST_CANCEL: set[int] = set()      # cooperative /cancel requests
BROADCAST_STATS = {"observed": 0, "started": 0, "finished": 0, "failed": 0}
SHADOW_LAST: dict[int, float] = {}

LINK_RE = re.compile(
    r"(?i)(?:https?://\S+|www\.\S+|(?:t\.me|telegram\.me|tg\.me)/[^\s]+|tg://\S+)"
)
# Lenient trigger parser: accepts @all, @all, hi / @admin: hi and the
# command aliases /all, /everyone, /mentionall, /admin, /mentionadmin.
BROADCAST_ALIASES = {
    "all": "all", "everyone": "all", "mentionall": "all",
    "admin": "admin", "mentionadmin": "admin",
}
BROADCAST_RE = re.compile(r"^[@/]([A-Za-z]+)(?:@[A-Za-z0-9_]+)?\b", re.IGNORECASE)


def parse_broadcast(text: str, bot_username: Optional[str] = None
                    ) -> Optional[tuple[str, str]]:
    """Return (mode, note) for a mass-mention trigger, else None."""
    raw = (text or "").strip()
    if not raw:
        return None
    if bot_username and raw.lower().startswith("@" + bot_username.lower()):
        raw = raw[len(bot_username) + 1:].lstrip(" ,:;-")
    match = BROADCAST_RE.match(raw)
    if not match:
        return None
    mode = BROADCAST_ALIASES.get(match.group(1).lower())
    if not mode:
        return None
    note = re.sub(r"^[\s,;:\-–—]+", "", raw[match.end():]).strip()
    return mode, note
CAPTCHA_RE = re.compile(r"^cv:(-?\d+):(\d+):([A-Za-z0-9_\-]+)$")
DURATION_RE = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
NOTE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def mention(user_id: int, name: str) -> str:
    """Telegram HTML mention - works for users without a username."""
    return f'<a href="tg://user?id={int(user_id)}">{esc(name or user_id)}</a>'


def state(context: ContextTypes.DEFAULT_TYPE) -> tuple[Config, Database]:
    data = context.application.bot_data
    return data["cfg"], data["db"]


def bot_of(context: ContextTypes.DEFAULT_TYPE):
    # Prefer application.bot: context.bot is deprecated in newer PTB releases.
    return context.application.bot


def command_payload(message: Any) -> str:
    """Everything after the first whitespace token of a command message."""
    parts = (message.text or message.caption or "").split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def spawn(coro, application: Optional[Application] = None) -> asyncio.Task:
    """Fire-and-forget with a strong reference and optional PTB task tracking."""
    try:
        task = application.create_task(coro) if application else asyncio.create_task(coro)
    except (AttributeError, RuntimeError):
        task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


async def safe_delete(bot, chat_id: int, message_id: int) -> bool:
    try:
        return bool(await bot.delete_message(chat_id=chat_id, message_id=message_id))
    except TelegramError as exc:
        logger.debug("Could not delete %s/%s: %s", chat_id, message_id, exc)
        return False


async def delete_later(bot, chat_id: int, message_id: int, delay: float = 8.0) -> None:
    """Send-and-forget temporary notices (used by link/content warnings)."""
    try:
        await asyncio.sleep(delay)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass  # Message already removed, or bot lost delete rights.


async def send_long(bot, chat_id: int, text: str, **kwargs: Any) -> None:
    """Telegram caps messages at 4096 chars; split on line boundaries."""
    kwargs.setdefault("parse_mode", ParseMode.HTML)
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True) or [""]:
        while len(line) > 3900:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:3900])
            line = line[3900:]
        if len(current) + len(line) > 3900:
            chunks.append(current)
            current = ""
        current += line
    if current.strip():
        chunks.append(current)
    for chunk in chunks or ["(empty)"]:
        await bot.send_message(chat_id=chat_id, text=chunk, **kwargs)


async def is_admin(bot, chat_id: int, user_id: int, ttl: float = 45.0) -> bool:
    """Cached admin check to avoid a getChatMember call on every message."""
    key = (chat_id, user_id)
    cached = _ADMIN_CACHE.get(key)
    if cached and time.monotonic() - cached[1] < ttl:
        return cached[0]
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        allowed = member.status in _ADMIN_STATUSES
    except TelegramError as exc:
        logger.debug("Admin lookup failed for %s in %s: %s", user_id, chat_id, exc)
        return False
    if len(_ADMIN_CACHE) > 2000:
        _ADMIN_CACHE.clear()
    _ADMIN_CACHE[key] = (allowed, time.monotonic())
    return allowed


async def is_privileged(bot, chat_id: int, user_id: int, cfg: Config) -> bool:
    return user_id in cfg.owner_ids or await is_admin(bot, chat_id, user_id)


async def default_permissions(bot, chat_id: int) -> ChatPermissions:
    """Unmute to the CHAT'S defaults, not blindly to all-permissions."""
    try:
        chat = await bot.get_chat(chat_id)
        if getattr(chat, "permissions", None):
            return chat.permissions
    except TelegramError as exc:
        logger.debug("Could not read chat permissions for %s: %s", chat_id, exc)
    return ChatPermissions.all_permissions()


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        action: str = "use this command") -> bool:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if not chat or not user or not message:
        return False
    if chat.type not in GROUP_TYPES:
        await message.reply_text("This command only works in groups.")
        return False
    cfg, _ = state(context)
    if await is_privileged(bot_of(context), chat.id, user.id, cfg):
        return True
    await message.reply_text(f"🔒 Only admins can {action}.")
    return False


RIGHT_LABELS = {
    "can_restrict_members": "Ban / restrict members",
    "can_change_info": "Change group info",
    "can_delete_messages": "Delete messages",
}


async def has_right(bot, chat_id: int, user_id: int, right: str,
                    cfg: Optional[Config] = None) -> bool:
    """Check a specific Telegram admin right (the group owner always passes).

    Bot-owner IDs do NOT bypass real group rights for ban/mute/kick.
    """
    _ = cfg
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except TelegramError as exc:
        logger.debug("Right lookup failed for %s/%s: %s", chat_id, user_id, exc)
        return False
    if member.status in _OWNER_STATUSES:
        return True
    if member.status not in _ADMIN_STATUSES:
        return False
    return bool(getattr(member, right, False))


async def require_right(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        right: str, action: str) -> bool:
    """Group-only gate for destructive commands such as /ban, /kick and /mute."""
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if not chat or not user or not message:
        return False
    if chat.type not in GROUP_TYPES:
        await message.reply_text(
            "Run this command in the group, replying to the member. "
            "The private console is reserved for greeting settings and ban records.")
        return False
    cfg, _ = state(context)
    if await has_right(bot_of(context), chat.id, user.id, right, cfg):
        return True
    label = RIGHT_LABELS.get(right, right)
    await message.reply_text(
        f"🔒 You need the <b>{esc(label)}</b> admin right to {esc(action)}.",
        parse_mode=ParseMode.HTML)
    return False


def user_first_name(user: Any) -> str:
    first = (getattr(user, "first_name", None) or "").strip()
    if first:
        return first
    full = (getattr(user, "full_name", None) or "").strip()
    return full.split()[0] if full else "there"


class _SafeTemplate(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_greeting(template: str, user: Any, chat_title: str,
                    member_count: Optional[int] = None) -> str:
    """Fill greeting placeholders; member values are HTML-escaped."""
    first = user_first_name(user)
    full = getattr(user, "full_name", None) or first
    username = getattr(user, "username", None)
    values = _SafeTemplate(
        first=esc(first),
        user=esc(full),
        username=esc("@" + username) if username else esc(first),
        mention=mention(getattr(user, "id", 0), first),
        group=esc(chat_title or "the group"),
        id=str(getattr(user, "id", "")),
        count=str(member_count) if member_count is not None else "",
    )
    try:
        return template.format_map(values)
    except (ValueError, IndexError):
        return template


async def chat_title(bot, chat_id: int) -> str:
    try:
        chat = await bot.get_chat(chat_id)
        return getattr(chat, "title", None) or "this group"
    except TelegramError:
        return "this group"


async def send_greeting(bot, chat_id: int, template: str, user: Any,
                        title: str, count: Optional[int] = None) -> None:
    """Send a rendered welcome/goodbye; fall back to plain text on bad HTML."""
    text = render_greeting(template, user, title, count)
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)
    except BadRequest as exc:
        logger.warning("Greeting HTML rejected (%s); resending as plain text", exc)
        plain = html.unescape(re.sub(r"<[^>]+>", "", text))
        try:
            await bot.send_message(chat_id=chat_id, text=plain,
                                   disable_web_page_preview=True)
        except TelegramError as plain_exc:
            logger.warning("Could not send greeting in %s: %s", chat_id, plain_exc)
    except TelegramError as exc:
        logger.warning("Could not send greeting in %s: %s", chat_id, exc)


def _card_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        except (OSError, TypeError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def build_welcome_card(first: str, title: str, username: str = "") -> Optional[BytesIO]:
    """Draw a lightweight welcome photo. CPU work runs via asyncio.to_thread."""
    if not (WELCOME_CARD_ENABLED and OCR_AVAILABLE and Image and ImageDraw):
        return None
    try:
        width, height = 1200, 600
        image = Image.new("RGB", (width, height), (8, 13, 22))
        draw = ImageDraw.Draw(image)
        for y in range(height):  # deep teal-to-indigo gradient
            ratio = y / height
            colour = (int(8 + 10 * ratio), int(20 + 28 * ratio), int(34 + 52 * ratio))
            draw.line([(0, y), (width, y)], fill=colour)
        draw.rounded_rectangle((58, 58, width - 58, height - 58), radius=36,
                               outline=(62, 224, 189), width=3)
        draw.ellipse((108, 170, 348, 410), fill=(62, 224, 189))
        initial = (first or "?").strip()[:1].upper() or "?"
        initial_font = _card_font(140, bold=True)
        draw.text((228, 290), initial, font=initial_font, fill=(4, 18, 24), anchor="mm")
        draw.text((108, 112), "MISS SONA  ·  COMMUNITY", font=_card_font(24, bold=True),
                  fill=(110, 168, 254))
        draw.text((408, 205), "WELCOME", font=_card_font(30, bold=True), fill=(62, 224, 189))
        name = " ".join((first or "there").split())[:28]
        draw.text((404, 250), name, font=_card_font(86, bold=True), fill=(240, 248, 255))
        group_line = " ".join((title or "our group").split())
        group_line = group_line[:42] + ("…" if len(group_line) > 42 else "")
        draw.text((408, 365), f"to {group_line}", font=_card_font(40), fill=(190, 208, 230))
        handle = f"@{username}" if username else ""
        draw.text((108, 462), handle, font=_card_font(30), fill=(141, 156, 181))
        draw.text((width - 108, 474), "You're verified ✓", font=_card_font(26, bold=True),
                  fill=(62, 224, 189), anchor="ra")
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        buffer.seek(0)
        return buffer
    except Exception as exc:  # A failed card must never block the welcome.
        logger.debug("Welcome card generation failed: %s", exc)
        return None


async def send_welcome(bot, chat_id: int, template: str, user: Any,
                       title: str, count: Optional[int] = None) -> None:
    """Welcome after captcha: rendered card photo + configured message."""
    text = render_greeting(template, user, title, count)
    card = await asyncio.to_thread(build_welcome_card, user_first_name(user), title,
                                  getattr(user, "username", "") or "")
    if card is not None:
        caption = text if len(text) <= 950 else None
        try:
            await bot.send_photo(
                chat_id=chat_id, photo=card,
                caption=caption or f"👋 Welcome to {esc(title)}!",
                parse_mode=ParseMode.HTML)
            if caption is None:
                await send_greeting(bot, chat_id, template, user, title, count)
            return
        except TelegramError as exc:
            logger.warning("Welcome photo failed in %s; sending text: %s", chat_id, exc)
    await send_greeting(bot, chat_id, template, user, title, count)


async def member_count(bot, chat_id: int) -> Optional[int]:
    try:
        return await bot.get_chat_member_count(chat_id)
    except TelegramError:
        return None


async def greeting_target(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          *, manage: bool) -> Optional[tuple[int, str]]:
    """Resolve the group whose greeting is viewed/edited, from group or /connect PM."""
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if not chat or not user or not message:
        return None
    bot, (cfg, db) = bot_of(context), state(context)
    if chat.type in GROUP_TYPES:
        if manage and not await has_right(bot, chat.id, user.id, "can_change_info", cfg):
            await message.reply_text(
                "🔒 Only the group owner or admins with the <b>Change group info</b> "
                "right can change greetings.", parse_mode=ParseMode.HTML)
            return None
        return chat.id, getattr(chat, "title", None) or "this group"
    if chat.type == ChatType.PRIVATE:
        conn = await db.run(db.get_connection, user.id)
        if not conn:
            await message.reply_text(
                "🔗 No group connected. In your group, send <code>/connect</code> "
                "as the owner or an admin with the <b>Change group info</b> right, "
                "then open the private link.", parse_mode=ParseMode.HTML)
            return None
        target_id = int(conn["chat_id"])
        if not await has_right(bot, target_id, user.id, "can_change_info", cfg):
            await message.reply_text(
                "🔒 You no longer have the <b>Change group info</b> right in the "
                "connected group. Ask the owner or reconnect after being promoted.",
                parse_mode=ParseMode.HTML)
            return None
        return target_id, await chat_title(bot, target_id)
    await message.reply_text("Greeting settings work in groups and in private chat.")
    return None


async def private_management_target(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    right: str) -> Optional[tuple[int, str]]:
    """Resolve a connected group for a PM admin, enforcing a specific right."""
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if not chat or not user or not message or chat.type != ChatType.PRIVATE:
        return None
    bot, (cfg, db) = bot_of(context), state(context)
    conn = await db.run(db.get_connection, user.id)
    if not conn:
        await message.reply_text(
            "🔗 Connect a group first: send <code>/connect</code> in the group as its "
            "owner or an admin with the Change group info right.", parse_mode=ParseMode.HTML)
        return None
    target_id = int(conn["chat_id"])
    if not await has_right(bot, target_id, user.id, right, cfg):
        label = RIGHT_LABELS.get(right, right)
        await message.reply_text(
            f"🔒 You need the <b>{esc(label)}</b> right in the connected group.",
            parse_mode=ParseMode.HTML)
        return None
    return target_id, await chat_title(bot, target_id)


async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[Optional[int], str]:
    """Resolve a moderation target from reply, text mention, ID or @username."""
    message = update.effective_message
    if not message:
        return None, ""
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        return target.id, target.full_name or str(target.id)
    text = message.text or ""
    for entity in message.entities or []:
        if entity.type == MessageEntityType.TEXT_MENTION and entity.user:
            return entity.user.id, entity.user.full_name or str(entity.user.id)
    args = text.split()[1:]
    if not args:
        return None, ""
    token = args[0]
    if token.lstrip("-").isdigit():
        return int(token), token
    if token.startswith("@"):
        try:
            target_chat = await bot_of(context).get_chat(token)
            return target_chat.id, getattr(target_chat, "full_name", None) or token
        except TelegramError:
            pass
        _, db = state(context)
        row = await db.run(db.find_by_username, update.effective_chat.id, token)
        if row:
            return int(row["user_id"]), row["name"] or token
    return None, ""


def reason_from_args(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    args = list(context.args or [])
    message = update.effective_message
    if message and message.reply_to_message:
        return " ".join(args).strip()
    return " ".join(args[1:]).strip()


async def target_guard(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       target_id: int) -> bool:
    message, chat = update.effective_message, update.effective_chat
    bot = bot_of(context)
    if target_id == getattr(bot, "id", None):
        await message.reply_text("I can't moderate myself 🙂")
        return False
    if target_id == update.effective_user.id:
        await message.reply_text("You can't moderate yourself.")
        return False
    if await is_admin(bot, chat.id, target_id):
        await message.reply_text("Group administrators are protected from bot moderation.")
        return False
    return True


async def kick_user(bot, chat_id: int, user_id: int) -> None:
    """Ban + immediate unban == kick (user may rejoin with a new invite)."""
    await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
    await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)


async def temp_reply(bot, chat_id: int, text: str, delay: float = 8.0,
                     application: Optional[Application] = None) -> None:
    try:
        sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        spawn(delete_later(bot, chat_id, sent.message_id, delay), application)
    except TelegramError as exc:
        logger.warning("Could not send temporary notice: %s", exc)


def parse_duration(token: str) -> Optional[timedelta]:
    match = DURATION_RE.match(token.strip()) if token else None
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2).lower()
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    # Telegram treats <30s or >366d restrictions as permanent.
    return timedelta(seconds=max(30, min(seconds, 366 * 86400)))


# --------------------------------------------------------------------------- #
# 1. Core moderation commands                                                  #
# --------------------------------------------------------------------------- #

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Permanent ban; only admins with the Ban/Restrict members right may use it."""
    if not await require_right(update, context, "can_restrict_members", "ban members"):
        return
    message, chat, actor = update.effective_message, update.effective_chat, update.effective_user
    target_id, name = await resolve_target(update, context)
    if target_id is None or not await target_guard(update, context, target_id):
        await message.reply_text("Usage: reply to a user with <code>/ban [reason]</code> "
                                 "or use <code>/ban 123456789 [reason]</code>.",
                                 parse_mode=ParseMode.HTML)
        return
    reason = reason_from_args(update, context) or "no reason provided"
    bot, (_, db) = bot_of(context), state(context)
    try:
        await bot.ban_chat_member(chat_id=chat.id, user_id=target_id, revoke_messages=False)
    except Forbidden:
        await message.reply_text("I need the <b>Ban users</b> admin right.")
        return
    except BadRequest as exc:
        await message.reply_text(f"Could not ban: {esc(exc.message)}", parse_mode=ParseMode.HTML)
        return
    await db.run(db.reset_warns, chat.id, target_id)
    await db.run(db.finish_captcha, chat.id, target_id)
    await db.run(db.mark_member_left, chat.id, target_id)
    await db.run(db.record_ban, chat.id, target_id, name, reason, actor.id)
    await message.reply_text(f"🔨 {mention(target_id, name)} banned.\n<i>Reason:</i> {esc(reason)}\n"
                             "<i>Recorded in /banned.</i>", parse_mode=ParseMode.HTML)
    logger.info("ban chat=%s target=%s by=%s", chat.id, target_id, actor.id)


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unban works in the group, or in PM through a /connect-ed group."""
    message, effective_chat, actor = (update.effective_message, update.effective_chat,
                                      update.effective_user)
    bot, (cfg, db) = bot_of(context), state(context)
    if effective_chat.type == ChatType.PRIVATE:
        target = await private_management_target(update, context, "can_restrict_members")
        if not target:
            return
        chat_id, title = target
        args = [a for a in (context.args or []) if a.lstrip("-").isdigit()]
        target_id = int(args[0]) if args else None
        name = str(target_id) if target_id else "member"
        if target_id is None:
            await message.reply_text(
                "Usage in private: <code>/unban 123456789</code>.\n"
                "Use /banned to see IDs recorded for the connected group.",
                parse_mode=ParseMode.HTML)
            return
    else:
        if not await require_right(update, context, "can_restrict_members", "unban members"):
            return
        chat_id, title = effective_chat.id, await chat_title(bot, effective_chat.id)
        target_id, name = await resolve_target(update, context)
        if target_id is None:
            await message.reply_text(
                "Usage: reply to a banned user's message with <code>/unban</code>, or "
                "<code>/unban 123456789</code>. Use /banned to see recorded bans.",
                parse_mode=ParseMode.HTML)
            return
    try:
        await bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
    except TelegramError as exc:
        await message.reply_text(f"Could not unban: {esc(exc)}", parse_mode=ParseMode.HTML)
        return
    await db.run(db.clear_ban, chat_id, target_id)
    await message.reply_text(
        f"✅ {mention(target_id, name)} may join <b>{esc(title)}</b> again.",
        parse_mode=ParseMode.HTML)
    logger.info("unban chat=%s target=%s by=%s", chat_id, target_id, actor.id)


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kick (ban + immediate unban) without leaving the user in /banned."""
    if not await require_right(update, context, "can_restrict_members", "kick members"):
        return
    message, chat, actor = update.effective_message, update.effective_chat, update.effective_user
    target_id, name = await resolve_target(update, context)
    if target_id is None or not await target_guard(update, context, target_id):
        await message.reply_text("Usage: reply to a user with <code>/kick [reason]</code>.",
                                 parse_mode=ParseMode.HTML)
        return
    reason = reason_from_args(update, context) or "no reason provided"
    bot, (_, db) = bot_of(context), state(context)
    SUPPRESS_GOODBYE.add((chat.id, target_id))
    try:
        await bot.ban_chat_member(chat.id, target_id)
        await bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
    except TelegramError as exc:
        await message.reply_text(f"Could not kick: {esc(exc)}", parse_mode=ParseMode.HTML)
        return
    await db.run(db.finish_captcha, chat.id, target_id)
    await db.run(db.mark_member_left, chat.id, target_id)
    await message.reply_text(
        f"👢 {mention(target_id, name)} kicked (not a permanent ban).\n"
        f"<i>Reason:</i> {esc(reason)}", parse_mode=ParseMode.HTML)
    logger.info("kick chat=%s target=%s by=%s", chat.id, target_id, actor.id)


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_right(update, context, "can_restrict_members", "mute members"):
        return
    message, chat = update.effective_message, update.effective_chat
    target_id, name = await resolve_target(update, context)
    if target_id is None or not await target_guard(update, context, target_id):
        await message.reply_text("Usage: reply with <code>/mute [30m|2h|1d]</code>; "
                                 "omit the duration for a permanent mute.", parse_mode=ParseMode.HTML)
        return
    args = list(context.args or [])
    duration_token = "" if message.reply_to_message else (args[1] if len(args) > 1 else "")
    if message.reply_to_message and args:
        duration_token = args[0]
    delta = parse_duration(duration_token)
    until_date = datetime.now(timezone.utc) + delta if delta else None
    try:
        await bot_of(context).restrict_chat_member(
            chat_id=chat.id, user_id=target_id,
            permissions=ChatPermissions.no_permissions(), until_date=until_date,
        )
    except Forbidden:
        await message.reply_text("I need the <b>Restrict members</b> admin right.",
                                 parse_mode=ParseMode.HTML)
        return
    except BadRequest as exc:
        await message.reply_text(f"Could not mute: {esc(exc.message)}", parse_mode=ParseMode.HTML)
        return
    span = f" for <b>{esc(duration_token)}</b>" if delta else " indefinitely"
    await message.reply_text(f"🔇 {mention(target_id, name)} muted{span}.", parse_mode=ParseMode.HTML)


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_right(update, context, "can_restrict_members", "unmute members"):
        return
    message, chat = update.effective_message, update.effective_chat
    target_id, name = await resolve_target(update, context)
    if target_id is None:
        await message.reply_text("Usage: reply to a muted user with <code>/unmute</code>.",
                                 parse_mode=ParseMode.HTML)
        return
    bot = bot_of(context)
    try:
        await bot.restrict_chat_member(chat_id=chat.id, user_id=target_id,
                                       permissions=await default_permissions(bot, chat.id))
    except TelegramError as exc:
        await message.reply_text(f"Could not unmute: {esc(exc)}", parse_mode=ParseMode.HTML)
        return
    await message.reply_text(f"🔊 {mention(target_id, name)} can speak again.",
                             parse_mode=ParseMode.HTML)


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_right(update, context, "can_restrict_members", "warn members"):
        return
    message, chat = update.effective_message, update.effective_chat
    target_id, name = await resolve_target(update, context)
    if target_id is None or not await target_guard(update, context, target_id):
        await message.reply_text("Usage: reply to a user with <code>/warn [reason]</code>.",
                                 parse_mode=ParseMode.HTML)
        return
    cfg, db = state(context)
    count = await db.run(db.add_warn, chat.id, target_id)
    reason = reason_from_args(update, context) or "no reason provided"
    who = mention(target_id, name)
    if count >= cfg.warn_limit:
        try:
            await bot_of(context).ban_chat_member(chat.id, target_id)
            await db.run(db.reset_warns, chat.id, target_id)
            await db.run(db.mark_member_left, chat.id, target_id)
            await db.run(db.record_ban, chat.id, target_id, name,
                         f"warning limit ({cfg.warn_limit}): {reason}",
                         update.effective_user.id)
            await message.reply_text(
                f"⛔ {who} reached <b>{count}/{cfg.warn_limit}</b> warnings and was banned.\n"
                f"<i>Last reason:</i> {esc(reason)}", parse_mode=ParseMode.HTML)
        except TelegramError as exc:
            logger.warning("Auto-ban failed for %s: %s", target_id, exc)
            await message.reply_text(f"⚠️ {who} reached the warning limit, but the ban failed.",
                                     parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(
            f"⚠️ Warning <b>{count}/{cfg.warn_limit}</b> for {who}.\n"
            f"<i>Reason:</i> {esc(reason)}", parse_mode=ParseMode.HTML)


async def cmd_warns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context, "inspect warnings"):
        return
    message, chat = update.effective_message, update.effective_chat
    target_id, name = await resolve_target(update, context)
    if target_id is None:
        await message.reply_text("Usage: reply to a user with <code>/warns</code>.",
                                 parse_mode=ParseMode.HTML)
        return
    cfg, db = state(context)
    count = await db.run(db.get_warns, chat.id, target_id)
    await message.reply_text(f"📋 {mention(target_id, name)} has <b>{count}/{cfg.warn_limit}</b> warnings.",
                             parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# 1b. Persistent rules and reference notes                                     #
# --------------------------------------------------------------------------- #

async def cmd_setrules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context, "set rules"):
        return
    message, chat = update.effective_message, update.effective_chat
    text = command_payload(message)
    if not text:
        await message.reply_text("Usage: <code>/setrules Be kind. No spam.</code>",
                                 parse_mode=ParseMode.HTML)
        return
    if len(text) > 8000:
        await message.reply_text("Rules are limited to 8000 characters.")
        return
    _, db = state(context)
    await db.run(db.set_rules, chat.id, text, update.effective_user.id)
    await message.reply_text("📜 Rules saved permanently for this chat.")


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, db = state(context)
    row = await db.run(db.get_rules, update.effective_chat.id)
    if not row:
        await update.effective_message.reply_text("No rules saved yet. An admin can use /setrules.")
        return
    await send_long(bot_of(context), update.effective_chat.id,
                    f"📜 <b>Group rules</b>\n\n{esc(row['text'])}")


async def cmd_setnote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context, "save notes"):
        return
    message, chat = update.effective_message, update.effective_chat
    payload = command_payload(message)
    if "|" in payload:
        name, text = (part.strip() for part in payload.split("|", 1))
    else:
        parts = payload.split(None, 1)
        name = parts[0].strip() if parts else ""
        text = parts[1].strip() if len(parts) > 1 else ""
    if not NOTE_NAME_RE.match(name) or not text:
        await message.reply_text(
            "Usage: <code>/setnote faq | Our FAQ lives at example.com</code>\n"
            "Names may contain letters, digits, _ and - (max 32 chars).",
            parse_mode=ParseMode.HTML)
        return
    _, db = state(context)
    await db.run(db.set_note, chat.id, name.lower(), text, update.effective_user.id)
    await message.reply_text(f"🗂️ Note <code>{esc(name.lower())}</code> saved.", parse_mode=ParseMode.HTML)


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, db = state(context)
    name = command_payload(update.effective_message).split()[0].lower() if command_payload(update.effective_message) else ""
    if not name:
        notes = await db.run(db.list_notes, update.effective_chat.id)
        listing = ", ".join(f"<code>{esc(n['name'])}</code>" for n in notes) or "none yet"
        await update.effective_message.reply_text(f"🗂️ Saved notes: {listing}\nUse <code>/note name</code>.",
                                                  parse_mode=ParseMode.HTML)
        return
    row = await db.run(db.get_note, update.effective_chat.id, name)
    if not row:
        await update.effective_message.reply_text(f"No note named <code>{esc(name)}</code>.",
                                                  parse_mode=ParseMode.HTML)
        return
    await send_long(bot_of(context), update.effective_chat.id,
                    f"🗂️ <b>{esc(name)}</b>\n\n{esc(row['text'])}")


async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_note(update, context)  # no argument -> list view


async def cmd_delnote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context, "delete notes"):
        return
    name = command_payload(update.effective_message).strip().lower()
    _, db = state(context)
    removed = await db.run(db.delete_note, update.effective_chat.id, name)
    await update.effective_message.reply_text(
        f"🗑️ Note <code>{esc(name)}</code> {'deleted' if removed else 'not found'}.",
        parse_mode=ParseMode.HTML)


async def cmd_trust(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context, "manage link trust"):
        return
    message, chat = update.effective_message, update.effective_chat
    target_id, name = await resolve_target(update, context)
    if target_id is None:
        await message.reply_text("Usage: reply to a user with <code>/trust</code>.",
                                 parse_mode=ParseMode.HTML)
        return
    _, db = state(context)
    await db.run(db.set_link_trust, chat.id, target_id, update.effective_user.id)
    await message.reply_text(f"✅ {mention(target_id, name)} may now post links.",
                             parse_mode=ParseMode.HTML)


async def cmd_untrust(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context, "manage link trust"):
        return
    message, chat = update.effective_message, update.effective_chat
    target_id, name = await resolve_target(update, context)
    if target_id is None:
        await message.reply_text("Usage: reply to a user with <code>/untrust</code>.",
                                 parse_mode=ParseMode.HTML)
        return
    _, db = state(context)
    await db.run(db.remove_link_trust, chat.id, target_id)
    await message.reply_text(f"🚫 Link exemption removed for {mention(target_id, name)}.",
                             parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# 2. Link / keyword / OCR safeguards                                           #
# --------------------------------------------------------------------------- #

def extract_links(message: Any) -> set[str]:
    """Catch raw URLs, t.me invites and Telegram URL / TEXT_LINK entities."""
    text = message.text or message.caption or ""
    found = {match.group(0) for match in LINK_RE.finditer(text)}
    for entity in list(message.entities or []) + list(message.caption_entities or []):
        if entity.type == MessageEntityType.URL:
            found.add(text[entity.offset:entity.offset + entity.length])
        elif entity.type == MessageEntityType.TEXT_LINK and entity.url:
            found.add(entity.url)
    return {link for link in found if link}


def allowed_link(link: str, cfg: Config) -> bool:
    candidate = link if "://" in link else "https://" + link.lstrip("@")
    host = (urlparse(candidate).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in cfg.allowed_domains)


async def enforce_link_policy(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              privileged: bool) -> bool:
    """Delete unapproved links and post a temporary warning. True == consumed."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    cfg, db = state(context)
    links = extract_links(message)
    if not links or all(allowed_link(link, cfg) for link in links):
        return False
    if (privileged or user.id in cfg.link_whitelist
            or await db.run(db.is_link_trusted, chat.id, user.id)):
        return False
    bot = bot_of(context)
    await safe_delete(bot, chat.id, message.message_id)
    await temp_reply(bot, chat.id,
                     f"🚫 {mention(user.id, user.full_name)}, unapproved links are not allowed here. "
                     "Ask an admin for permission. <i>(This notice disappears shortly.)</i>",
                     delay=8, application=context.application)
    logger.info("link removed chat=%s user=%s count=%d", chat.id, user.id, len(links))
    return True


async def enforce_content_policy(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 text: str, source: str) -> bool:
    """Delete prohibited content, escalate strikes, ban repeat offenders."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    cfg, db = state(context)
    matcher: KeywordMatcher = context.application.bot_data["matcher"]
    hit = matcher.find(text)
    if not hit:
        return False
    bot = bot_of(context)
    await safe_delete(bot, chat.id, message.message_id)
    strikes = await db.run(db.add_content_strike, chat.id, user.id)
    detail = "text" if source == "text" else f"{source} content"
    if strikes >= cfg.content_ban_threshold:
        try:
            await bot.ban_chat_member(chat.id, user.id)
            await db.run(db.reset_content_strikes, chat.id, user.id)
            await db.run(db.mark_member_left, chat.id, user.id)
            await db.run(db.record_ban, chat.id, user.id, user.full_name or str(user.id),
                         f"prohibited {detail} ({strikes}/{cfg.content_ban_threshold} strikes)",
                         getattr(bot, "id", 0))
            tail = (f"⛔ {mention(user.id, user.full_name)} was <b>banned</b> as a repeat "
                    f"offender ({strikes}/{cfg.content_ban_threshold} strikes).")
        except TelegramError as exc:
            logger.warning("Repeat-offender ban failed: %s", exc)
            tail = f"⚠️ Strike limit reached, but the ban failed ({esc(exc)})."
    else:
        tail = (f"⚠️ {mention(user.id, user.full_name)}: prohibited {detail} removed. "
                f"Strike <b>{strikes}/{cfg.content_ban_threshold}</b>.")
    await temp_reply(bot, chat.id, tail, delay=12, application=context.application)
    logger.info("content hit=%s source=%s chat=%s user=%s strikes=%d",
                hit, source, chat.id, user.id, strikes)
    return True


def image_candidate(message: Any) -> Optional[tuple[str, int]]:
    """Return (file_id, size) for photos and image documents, else None."""
    if message.photo:
        photo = message.photo[-1]  # largest available variant
        return photo.file_id, int(photo.file_size or 0)
    doc = getattr(message, "document", None)
    if doc and (getattr(doc, "mime_type", "") or "").startswith("image/"):
        return doc.file_id, int(doc.file_size or 0)
    return None


def run_ocr(raw: bytes, lang: str) -> str:
    """CPU-bound Tesseract call - always invoked via asyncio.to_thread."""
    if not OCR_AVAILABLE or Image is None or pytesseract is None:
        return ""
    try:
        with Image.open(BytesIO(raw)) as image:
            image = image.convert("L")
            if max(image.size) > 1600:
                image.thumbnail((1600, 1600))
            return pytesseract.image_to_string(image, lang=lang, config="--psm 6") or ""
    except (UnidentifiedImageError, OSError) as exc:
        logger.debug("Image could not be decoded: %s", exc)
    except Exception as exc:  # TesseractError, missing language data, etc.
        logger.warning("OCR failed: %s", exc)
    return ""


async def scan_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    cfg = state(context)[0]
    candidate = image_candidate(update.effective_message)
    if not candidate:
        return ""
    file_id, size = candidate
    if size and size > cfg.ocr_max_bytes:
        logger.debug("Skipping OCR for oversized image (%d bytes)", size)
        return ""
    if not OCR_AVAILABLE:
        return ""
    try:
        tg_file = await bot_of(context).get_file(file_id)
        raw = bytes(await tg_file.download_as_bytearray())
    except TelegramError as exc:
        logger.warning("Could not download image for OCR: %s", exc)
        return ""
    return await asyncio.to_thread(run_ocr, raw, cfg.ocr_lang)


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Final group handler: link policy -> keyword filter -> OCR safeguard."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or user.is_bot:
        return
    bot = bot_of(context)
    cfg, _ = state(context)
    privileged = await is_privileged(bot, chat.id, user.id, cfg)
    if not privileged and await enforce_link_policy(update, context, privileged):
        return  # message deleted; no reason to OCR it
    text = message.text or message.caption or ""
    ocr_text = ""
    if not privileged and image_candidate(message):
        ocr_text = await scan_image(update, context)
        if ocr_text:
            logger.debug("OCR extracted %d chars in chat %s", len(ocr_text), chat.id)
    if not privileged and await enforce_content_policy(
        update, context, f"{text}\n{ocr_text}", "image" if ocr_text else "text"
    ):
        return


# --------------------------------------------------------------------------- #
# 3. Throttled @all / @admin broadcasts                                        #
# --------------------------------------------------------------------------- #

def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), max(1, size)):
        yield items[index:index + max(1, size)]


async def sync_admin_roster(bot, db: Database, chat_id: int) -> int:
    """Capture administrators, who may never have posted, into the roster.

    The Bot API cannot enumerate ordinary silent members; this at least makes
    sure every current human admin is known and mentionable.
    """
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except TelegramError as exc:
        logger.warning("Could not sync admins in %s: %s", chat_id, exc)
        return 0
    synced = 0
    for member in admins:
        if member.user and not member.user.is_bot:
            await db.run(db.upsert_member, chat_id, member.user, True)
            synced += 1
    return synced


async def cancellable_sleep(chat_id: int, seconds: float) -> bool:
    """Sleep in 250ms slices so /cancel also interrupts flood-waits."""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if chat_id in BROADCAST_CANCEL:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return chat_id in BROADCAST_CANCEL
        await asyncio.sleep(min(0.25, remaining))


def _member_name(member: dict) -> str:
    return member.get("name") or member.get("username") or str(member.get("user_id", "user"))


async def _send_one_mention(bot, chat_id: int, member: dict) -> bool:
    """Send one mention, falling back to @username and then plain text."""
    user_id = int(member["user_id"])
    name = _member_name(member)
    username = (member.get("username") or "").lstrip("@")
    candidates: list[tuple[str, Optional[str]]] = [(mention(user_id, name), ParseMode.HTML)]
    if username:
        candidates.append(("@" + username, None))  # auto-links and pings
    candidates.append((name, None))  # final fallback: always visible, may not ping
    for text, parse_mode in candidates:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode,
                                   disable_web_page_preview=True)
            return True
        except RetryAfter as exc:
            retry = getattr(exc, "retry_after", 3)
            wait = (retry.total_seconds() if hasattr(retry, "total_seconds")
                    else float(retry)) + 1.0
            logger.warning("Flood-wait %.1fs while mentioning user %s", wait, user_id)
            if await cancellable_sleep(chat_id, wait):
                return False
        except TelegramError as exc:
            logger.warning("Mention failed for user=%s (%s); trying fallback", user_id, exc)
    return False


async def send_mention_batch(bot, chat_id: int, members: list[dict]) -> int:
    """Send a batch; return how many members were successfully mentioned."""
    if not members:
        return 0
    if len(members) == 1:
        return int(await _send_one_mention(bot, chat_id, members[0]))
    text = " ".join(mention(int(m["user_id"]), _member_name(m)) for m in members)
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)
        return len(members)
    except TelegramError as exc:
        logger.warning("Batch mention failed in %s (%s); splitting", chat_id, exc)
        delivered = 0
        for member in members:
            if await _send_one_mention(bot, chat_id, member):
                delivered += 1
        return delivered


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: stop the mention broadcast running in this group."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if chat.type not in GROUP_TYPES:
        await message.reply_text("Use /cancel in the group where the mention is running.")
        return
    cfg, _ = state(context)
    if not await is_privileged(bot_of(context), chat.id, user.id, cfg):
        await message.reply_text("🔒 Only group admins can cancel a mention broadcast.")
        return
    if chat.id not in BROADCAST_ACTIVE:
        BROADCAST_CANCEL.discard(chat.id)
        await message.reply_text("No mention broadcast is running in this group.")
        return
    BROADCAST_CANCEL.add(chat.id)
    await message.reply_text(
        "🛑 <b>Cancellation requested.</b> The current mention message finishes, then "
        "the broadcast stops. The cooldown remains active.", parse_mode=ParseMode.HTML)


async def cmd_mentionstatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explain exactly why @all may or may not be arriving, with live checks."""
    if not await require_admin(update, context, "inspect mention status"):
        return
    message, chat = update.effective_message, update.effective_chat
    cfg, db = state(context)
    bot = bot_of(context)
    me = await bot.get_me()
    privacy = getattr(me, "can_read_all_group_messages", None)
    bot_admin = False
    rights = "unknown"
    try:
        self_member = await bot.get_chat_member(chat.id, me.id)
        bot_admin = self_member.status in _ADMIN_STATUSES
        rights = " · ".join(
            f"{label}: {'yes' if getattr(self_member, right, False) else 'NO'}"
            for right, label in (("can_delete_messages", "delete"),
                                 ("can_restrict_members", "restrict"),
                                 ("can_change_info", "info"))
            if self_member.status not in _OWNER_STATUSES)
        if not rights:
            rights = "owner"
    except TelegramError as exc:
        rights = f"lookup failed ({esc(exc)})"
    stats = await db.run(db.member_stats, chat.id, time.time() - 30 * 86400)
    left = max(0, int(cfg.broadcast_cooldown -
                      (time.monotonic() - BROADCAST_LAST.get(chat.id, 0.0))))
    privacy_label = {True: "OFF (bot reads all group messages)",
                     False: "ON (ordinary group text is filtered by Telegram)",
                     None: "unknown"}.get(privacy, str(privacy))
    advice = ""
    if privacy is False and not bot_admin:
        advice = ("\n⚠️ <b>@all cannot arrive.</b> Privacy mode is ON and I am not an admin. "
                  "Turn it off in @BotFather (/setprivacy → Disable), then <b>remove me "
                  "from the group and add me back</b>, and promote me again.")
    elif privacy is False:
        advice = ("\n⚠️ Privacy mode is ON. Admin bots usually receive group text, but if "
                  "@all is silent, disable privacy in @BotFather and re-add me. "
                  "Commands (/all, /mentionall) always work.")
    else:
        advice = "\n✅ Privacy mode is OFF, so ordinary @all messages should reach me."
    await message.reply_text(
        "📣 <b>Mention diagnostics</b>\n"
        f"Bot: @{esc(me.username or 'unknown')} · admin: <b>{'yes' if bot_admin else 'NO'}</b>\n"
        f"Rights: {rights}\n"
        f"Privacy mode: <b>{privacy_label}</b>\n\n"
        f"Roster: <b>{stats['humans']}</b> humans · {stats['dormant']} quiet 30d+ · "
        f"{stats['bots']} bots excluded · {stats['left']} left/kicked skipped\n"
        f"Triggers observed since start: <b>{BROADCAST_STATS['observed']}</b>\n"
        f"Broadcasts started / finished / failed: <b>{BROADCAST_STATS['started']}</b> / "
        f"<b>{BROADCAST_STATS['finished']}</b> / <b>{BROADCAST_STATS['failed']}</b>\n"
        f"Running now: <b>{'yes' if chat.id in BROADCAST_ACTIVE else 'no'}</b> · "
        f"cooldown: <b>{left}s</b>\n"
        f"Pacing: {cfg.mention_delay:g}s · cap: "
        f"{cfg.max_mentions or 'all known'} · batch: {cfg.mention_batch_size}\n\n"
        f"<b>Try:</b> <code>/all Hello everyone</code> (works even with privacy ON) "
        f"or <code>/all@{esc(me.username or 'yourbot')} Hello everyone</code> — commands "
        f"addressed to me are always delivered. Plain <code>@all Hello</code> requires "
        f"privacy mode OFF or admin visibility.\n"
        f"/mentiontest sends a real HTML mention to you. Bot v{BOT_VERSION}.\n"
        f"{advice}", parse_mode=ParseMode.HTML)


async def cmd_mentiontest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context, "test mentions"):
        return
    chat, user, bot = update.effective_chat, update.effective_user, bot_of(context)
    try:
        await bot.send_message(
            chat.id,
            f"🔔 <b>Mention test:</b> {mention(user.id, user.full_name)} — if you were "
            "pinged, mention delivery works. Use /mentionstatus for the full check.",
            parse_mode=ParseMode.HTML)
    except TelegramError as exc:
        await update.effective_message.reply_text(
            f"❌ Mention test failed: {esc(exc)}", parse_mode=ParseMode.HTML)


async def broadcast_worker(bot, chat, user, cfg: Config, db: Database, mode: str,
                           note: str, application: Application) -> None:
    """Run one broadcast off the update loop so /cancel can be received."""
    chat_id = chat.id
    BROADCAST_STATS["started"] += 1
    try:
        if mode == "admin":
            try:
                admins = await bot.get_chat_administrators(chat_id)
            except TelegramError as exc:
                await bot.send_message(chat_id, f"⚠️ Could not read the admin list: "
                                                f"{esc(exc)}", parse_mode=ParseMode.HTML)
                BROADCAST_STATS["failed"] += 1
                return
            targets = []
            for admin in admins:
                if not admin.user or admin.user.is_bot:
                    continue
                await db.run(db.upsert_member, chat_id, admin.user, True)
                targets.append({"user_id": admin.user.id, "name": admin.user.full_name,
                                "username": admin.user.username or ""})
            label = "human administrators (bots excluded)"
        else:
            await sync_admin_roster(bot, db, chat_id)
            targets = await db.run(db.mention_members, chat_id, cfg.max_mentions)
            label = "known humans — active or dormant · bots excluded"

        # Never fail silently on an empty roster: at least mention the requester.
        if not any(int(t["user_id"]) == user.id for t in targets):
            targets.insert(0, {"user_id": user.id, "name": user.full_name,
                               "username": user.username or ""})
        if not targets:
            await bot.send_message(
                chat_id,
                "⚠️ <b>No mentionable members yet.</b> I learn members from joins, "
                "messages and the admin list. Run /syncmembers, then /mentionstatus.",
                parse_mode=ParseMode.HTML)
            BROADCAST_STATS["failed"] += 1
            return

        batches = list(chunks(targets, cfg.mention_batch_size))
        pauses = max(0, len(batches) - 1)
        eta_seconds = int(pauses * cfg.mention_delay)
        eta = (f"{eta_seconds // 60}m {eta_seconds % 60}s" if eta_seconds >= 60
               else f"{eta_seconds}s")
        cap_note = " · MAX_MENTIONS cap applied" if cfg.max_mentions else ""
        header = (f"📢 <b>Announcement from {mention(user.id, user.full_name)}</b>\n"
                  f"{esc(note) if note else '(no message attached)'}\n"
                  f"<i>Mentioning {len(targets)} {label}{cap_note} — "
                  f"{cfg.mention_delay:g}s apart, ~{eta} total. Send /cancel to stop.</i>")
        await bot.send_message(chat_id, header, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)

        delivered = 0
        for index, batch in enumerate(batches):
            if chat_id in BROADCAST_CANCEL:
                await bot.send_message(chat_id,
                    f"🛑 Broadcast cancelled after {delivered}/{len(targets)} mention(s).")
                return
            delivered += await send_mention_batch(bot, chat_id, batch)
            if index < len(batches) - 1:
                await cancellable_sleep(chat_id, cfg.mention_delay)
        logger.info("broadcast mode=%s chat=%s delivered=%d/%d",
                    mode, chat_id, delivered, len(targets))
        BROADCAST_STATS["finished"] += 1
    except (Forbidden, BadRequest, TelegramError) as exc:
        logger.warning("Broadcast failed in chat %s: %s", chat_id, exc)
        BROADCAST_STATS["failed"] += 1
        try:
            await bot.send_message(chat_id, f"⚠️ Broadcast stopped: {esc(exc)}\n"
                                            "Check /mentionstatus.", parse_mode=ParseMode.HTML)
        except TelegramError:
            pass
    except Exception:
        BROADCAST_STATS["failed"] += 1
        logger.exception("Broadcast worker crashed in chat %s", chat_id)
    finally:
        BROADCAST_ACTIVE.discard(chat_id)
        BROADCAST_CANCEL.discard(chat_id)


async def on_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Validate @all/@admin or /all/@admin aliases, then start a background worker."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    bot = bot_of(context)
    parsed = parse_broadcast(getattr(message, "text", None) or "",
                             getattr(bot, "username", None))
    if not parsed:
        return  # Ordinary text: let guesses and safeguards handle it.
    mode, note = parsed
    logger.info("mention trigger handled chat=%s user=%s mode=%s", chat.id, user.id, mode)
    cfg, db = state(context)
    application = context.application

    if not await is_privileged(bot, chat.id, user.id, cfg):
        await safe_delete(bot, chat.id, message.message_id)
        await temp_reply(bot, chat.id,
                         f"🔒 {mention(user.id, user.full_name)}, mass mentions are admin-only.",
                         delay=10, application=application)
        raise ApplicationHandlerStop
    if chat.id in BROADCAST_ACTIVE:
        await temp_reply(bot, chat.id, "⏳ A broadcast is running here. Use /cancel to stop it.",
                         delay=8, application=application)
        raise ApplicationHandlerStop
    elapsed = time.monotonic() - BROADCAST_LAST.get(chat.id, 0.0)
    if elapsed < cfg.broadcast_cooldown:
        await temp_reply(bot, chat.id,
                         f"⏳ Please wait <b>{int(cfg.broadcast_cooldown - elapsed)}s</b> "
                         "before another broadcast. Use /mentionstatus to inspect.",
                         delay=8, application=application)
        raise ApplicationHandlerStop

    BROADCAST_CANCEL.discard(chat.id)
    BROADCAST_ACTIVE.add(chat.id)
    BROADCAST_LAST[chat.id] = time.monotonic()
    spawn(broadcast_worker(bot, chat, user, cfg, db, mode, note, application), application)
    raise ApplicationHandlerStop


# --------------------------------------------------------------------------- #
# 4. Captcha verification on join                                              #
# --------------------------------------------------------------------------- #

def memberish(member: Any) -> bool:
    status = member.status
    if status == ChatMemberStatus.MEMBER or status in _ADMIN_STATUSES:
        return True
    return status == ChatMemberStatus.RESTRICTED and bool(getattr(member, "is_member", False))


async def captcha_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kick a user who never pressed the button (JobQueue or fallback task)."""
    job = getattr(context, "job", None)
    payload = getattr(job, "data", None) or getattr(context, "data", {}) or {}
    chat_id, user_id, token = payload.get("chat_id"), payload.get("user_id"), payload.get("token")
    application = context.application
    bot, db = application.bot, application.bot_data["db"]
    if chat_id is None or user_id is None or not token:
        return
    row = await db.run(db.get_captcha, chat_id, user_id)
    if not row or row["token"] != token:
        return  # Already verified, re-joined, or manually handled.
    SUPPRESS_GOODBYE.add((chat_id, user_id))  # Never send goodbye for timeouts.
    try:
        await kick_user(bot, chat_id, user_id)
        await db.run(db.finish_captcha, chat_id, user_id)
        await db.run(db.mark_member_left, chat_id, user_id)
        await safe_delete(bot, chat_id, int(row["message_id"]))
        await temp_reply(bot, chat_id,
                         f"⏳ {mention(user_id, row['display_name'])} was removed: "
                         "captcha not completed in time.", delay=15, application=application)
        logger.info("captcha timeout kick chat=%s user=%s", chat_id, user_id)
    except (Forbidden, BadRequest) as exc:
        logger.warning("Captcha kick failed (missing ban rights?) chat=%s: %s", chat_id, exc)
    except TelegramError as exc:
        logger.warning("Captcha cleanup failed: %s", exc)


def schedule_captcha_timeout(application: Application, chat_id: int, user_id: int,
                             token: str, delay: float) -> None:
    payload = {"chat_id": chat_id, "user_id": user_id, "token": token}
    name = f"captcha:{chat_id}:{user_id}"
    if application.job_queue:
        for existing in application.job_queue.get_jobs_by_name(name):
            existing.schedule_removal()
        application.job_queue.run_once(
            captcha_timeout, when=timedelta(seconds=max(1.0, delay)),
            data=payload, name=name,
        )
    else:
        # APScheduler extra not installed: fall back to a plain asyncio task.
        async def fallback() -> None:
            await asyncio.sleep(max(1.0, delay))
            fake = SimpleNamespace(
                application=application,
                job=SimpleNamespace(data=payload),
                data=payload,
            )
            await captcha_timeout(fake)  # type: ignore[arg-type]
        spawn(fallback(), application)


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep the roster in sync, restrict new joiners and send the captcha."""
    change = update.chat_member
    if not change or not change.new_chat_member:
        return
    old, new = change.old_chat_member, change.new_chat_member
    user, chat = new.user, change.chat
    if not user or user.is_bot:
        return  # Bots never enter the mention roster.
    cfg, db = state(context)
    was_member, is_member = memberish(old), memberish(new)
    if is_member:
        await db.run(db.upsert_member, chat.id, user, True)
    elif was_member:
        await db.run(db.mark_member_left, chat.id, user.id)
        await db.run(db.finish_captcha, chat.id, user.id)
        key = (chat.id, user.id)
        bot = bot_of(context)
        if key in SUPPRESS_GOODBYE:
            SUPPRESS_GOODBYE.discard(key)  # Bot-initiated kick: no goodbye.
        elif new.status == ChatMemberStatus.LEFT and user.id not in cfg.owner_ids:
            greetings = await db.run(db.get_greetings, chat.id)
            template = (greetings or {}).get("goodbye") or DEFAULT_GOODBYE_TEXT
            await send_greeting(bot, chat.id, template, user,
                                getattr(chat, "title", None) or "the group",
                                await member_count(bot, chat.id))
        logger.info("member left chat=%s user=%s", chat.id, user.id)
        return
    if was_member or not is_member:
        return  # Role change only; roster already updated above.
    bot = bot_of(context)
    # Never captcha-restrict an administrator, the owner, or a configured owner.
    if new.status in _ADMIN_STATUSES or user.id in cfg.owner_ids:
        await db.run(db.finish_captcha, chat.id, user.id)
        greetings = await db.run(db.get_greetings, chat.id)
        template = (greetings or {}).get("welcome") or DEFAULT_WELCOME_TEXT
        await send_welcome(bot, chat.id, template, user,
                           getattr(chat, "title", None) or "the group",
                           await member_count(bot, chat.id))
        logger.info("admin/owner joined without captcha chat=%s user=%s", chat.id, user.id)
        return
    try:
        await bot.restrict_chat_member(chat_id=chat.id, user_id=user.id,
                                       permissions=ChatPermissions.no_permissions())
    except (Forbidden, BadRequest) as exc:
        logger.warning("Cannot restrict new member %s in %s: %s", user.id, chat.id, exc)
        await temp_reply(bot, chat.id,
                         "⚠️ I need the <b>Restrict members</b> right (and a supergroup) "
                         "to run captcha verification.", delay=20, application=context.application)
        return
    token = secrets.token_urlsafe(6)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Click here to prove you're human",
                             callback_data=f"cv:{chat.id}:{user.id}:{token}")
    ]])
    captcha_text = (
        f"🔐 {mention(user.id, user.full_name)}, verification required.\n\n"
        "To keep this group spam-free, prove you're human by pressing the button "
        f"below within <b>{cfg.captcha_timeout // 60} minute(s)</b>. "
        "Your welcome message arrives right after verification. "
        "You can't post until you verify."
    )
    try:
        sent = await bot.send_message(chat_id=chat.id, text=captcha_text,
                                      parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except TelegramError as exc:
        logger.warning("Could not send captcha message: %s", exc)
        return
    await db.run(db.save_captcha, chat.id, user.id, token, sent.message_id, user.full_name or "")
    schedule_captcha_timeout(context.application, chat.id, user.id, token, float(cfg.captcha_timeout))
    logger.info("captcha sent chat=%s user=%s", chat.id, user.id)


async def on_captcha_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    match = CAPTCHA_RE.match(query.data)
    if not match:
        await query.answer("Invalid verification token.", show_alert=True)
        return
    chat_id, target_id, token = int(match.group(1)), int(match.group(2)), match.group(3)
    if query.from_user.id != target_id:
        await query.answer("This button isn't for you 🙂", show_alert=True)
        return
    bot = bot_of(context)
    _, db = state(context)
    row = await db.run(db.get_captcha, chat_id, target_id)
    if not row or row["token"] != token:
        await query.answer("This verification expired or was already used.", show_alert=True)
        return
    try:
        await bot.restrict_chat_member(chat_id=chat_id, user_id=target_id,
                                       permissions=await default_permissions(bot, chat_id))
    except TelegramError as exc:
        logger.warning("Could not restore permissions after captcha: %s", exc)
        await query.answer("I need Restrict members rights — please tell an admin.", show_alert=True)
        return
    await db.run(db.finish_captcha, chat_id, target_id)
    if context.application.job_queue:
        for job in context.application.job_queue.get_jobs_by_name(f"captcha:{chat_id}:{target_id}"):
            job.schedule_removal()
    await query.answer("✅ Verified — here comes your welcome!")
    # Remove the verification prompt, then deliver the configured welcome.
    prompt_deleted = await safe_delete(bot, chat_id, int(row["message_id"]))
    if not prompt_deleted:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=int(row["message_id"]),
                text="✅ Verification complete.", reply_markup=None)
        except TelegramError:
            pass
    greetings = await db.run(db.get_greetings, chat_id)
    template = (greetings or {}).get("welcome") or DEFAULT_WELCOME_TEXT
    title = await chat_title(bot, chat_id)
    await send_welcome(bot, chat_id, template, query.from_user, title,
                       await member_count(bot, chat_id))
    logger.info("captcha verified + welcomed chat=%s user=%s", chat_id, target_id)


# --------------------------------------------------------------------------- #
# 5. Shadow-reply / proxy messaging                                            #
# --------------------------------------------------------------------------- #

async def cmd_shadowreply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete the admin's command and republish the payload as the bot itself."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not await require_admin(update, context, "use shadow replies"):
        return
    raw_html = getattr(message, "text_html", None) or esc(message.text or "")
    # Strip only the leading command (including an optional @BotUsername).
    payload = re.sub(r"^/shadowreply(?:@\w+)?\s*", "", raw_html, count=1,
                     flags=re.IGNORECASE).strip()
    if not payload:
        await message.reply_text("Usage: <code>/shadowreply Your announcement text</code>",
                                 parse_mode=ParseMode.HTML)
        return
    last = SHADOW_LAST.get(user.id, 0.0)
    if time.monotonic() - last < 2.0:
        await message.reply_text("⏳ Please wait a moment before another shadow reply.")
        return
    SHADOW_LAST[user.id] = time.monotonic()
    bot = bot_of(context)
    await safe_delete(bot, chat.id, message.message_id)
    try:
        # Sent by the bot's own identity: the original admin is not the author.
        await bot.send_message(chat_id=chat.id, text=payload, parse_mode=ParseMode.HTML)
    except BadRequest:
        # Fallback for exotic entity combinations: resend as plain text.
        await bot.send_message(chat_id=chat.id, text=command_payload(message))
    logger.info("shadow reply chat=%s by=%s", chat.id, user.id)


# --------------------------------------------------------------------------- #
# 6. Word jumble / Wordle-style game                                           #
# --------------------------------------------------------------------------- #

EXACT_ICON, MISPLACED_ICON, MISSING_ICON = "🎯", "🔀", "🚫"
HINT_LEGEND = (f"{EXACT_ICON} right letter &amp; spot · "
               f"{MISPLACED_ICON} right letter, wrong spot · "
               f"{MISSING_ICON} not in the word")


def score_guess(guess: str, answer: str) -> str:
    """Two-pass Wordle scoring with correct duplicate-letter handling."""
    length = len(answer)
    result = [MISSING_ICON] * length
    remaining: Counter = Counter()
    for index, (letter, target) in enumerate(zip(guess, answer)):
        if letter == target:
            result[index] = EXACT_ICON
        else:
            remaining[target] += 1
    for index, letter in enumerate(guess):
        if result[index] == EXACT_ICON:
            continue
        if remaining[letter] > 0:
            result[index] = MISPLACED_ICON
            remaining[letter] -= 1
    return "".join(result)


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    command = (message.text or "").split()[0].lstrip("/").split("@")[0].lower()
    match = re.fullmatch(r"new(\d)?", command)
    length = int(match.group(1)) if match and match.group(1) else 5
    bank: dict[int, tuple[str, ...]] = context.application.bot_data["words"]
    words = bank.get(length)
    if not words:
        await message.reply_text(
            f"No {length}-letter words available. Try: " +
            ", ".join(f"/new{n}" for n in sorted(bank)) + " or /new.")
        return
    cfg, db = state(context)
    word = secrets.choice(words)
    await db.run(db.start_game, chat.id, word, length, update.effective_user.id)
    await message.reply_text(
        f"🎲 <b>New {length}-letter word game!</b>\n"
        f"Send a {length}-letter word as a plain message to guess. "
        f"Up to <b>{cfg.max_guesses}</b> guesses — first correct guess wins.\n\n{HINT_LEGEND}",
        parse_mode=ParseMode.HTML)


async def on_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Consume a valid bare-word guess before the safeguard layer sees it."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not message.text or message.text.startswith("/") or not user or user.is_bot:
        return
    cfg, db = state(context)
    game = await db.run(db.get_game, chat.id)
    if not game or game["status"] != "active":
        return
    guess = message.text.strip().lower()
    if not re.fullmatch(r"[a-z]+", guess) or len(guess) != int(game["length"]):
        return  # ordinary chat, let later groups process it
    hint = score_guess(guess, str(game["word"]))
    entry = {"g": guess, "u": user.id, "n": user.full_name, "h": hint, "at": time.time()}
    count = await db.run(db.append_guess, chat.id, entry)
    if count is None:
        return
    who = mention(user.id, user.full_name)
    answer = str(game["word"])
    if guess == answer:
        await db.run(db.finish_game, chat.id, "won")
        total = await db.run(db.record_win, chat.id, user.id, user.full_name or "")
        await message.reply_text(
            f"🏆 {who} solved it in <b>{count}</b> guess(es)!\n"
            f"<code>{guess.upper()}  {hint}</code>\n"
            f"The word was <b>{esc(answer.upper())}</b> · career wins: {total}.\n"
            "Start another round with /new.", parse_mode=ParseMode.HTML)
    elif count >= cfg.max_guesses:
        await db.run(db.finish_game, chat.id, "exhausted")
        await message.reply_text(
            f"🛑 <b>{cfg.max_guesses} guesses used — game over!</b>\n"
            f"<code>{guess.upper()}  {hint}</code>\n"
            f"The word was <b>{esc(answer.upper())}</b>. Use /new to retry.",
            parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(
            f"<code>{guess.upper()}  {hint}</code>\n"
            f"<i>Guess {count}/{cfg.max_guesses} · {HINT_LEGEND}</i>",
            parse_mode=ParseMode.HTML)
    raise ApplicationHandlerStop


async def cmd_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg, db = state(context)
    game = await db.run(db.get_game, update.effective_chat.id)
    if not game or game["status"] != "active":
        await update.effective_message.reply_text("No active game. Start one with /new, /new4, /new5 or /new6.")
        return
    guesses = json.loads(game["guesses"] or "[]")
    history = "\n".join(f"<code>{esc(g['g'].upper())}  {g['h']}</code>" for g in guesses[-6:])
    await update.effective_message.reply_text(
        f"🎯 <b>Active {game['length']}-letter game</b> · "
        f"{len(guesses)}/{cfg.max_guesses} guesses used.\n"
        f"{history or '<i>No guesses yet.</i>'}\n{HINT_LEGEND}", parse_mode=ParseMode.HTML)


async def cmd_stopgame(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context, "stop games"):
        return
    _, db = state(context)
    game = await db.run(db.get_game, update.effective_chat.id)
    if not game or game["status"] != "active":
        await update.effective_message.reply_text("There is no active game.")
        return
    await db.run(db.finish_game, update.effective_chat.id, "stopped")
    await update.effective_message.reply_text(
        f"🛑 Game stopped. The word was <b>{esc(str(game['word']).upper())}</b>.",
        parse_mode=ParseMode.HTML)


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, db = state(context)
    rows = await db.run(db.leaderboard, update.effective_chat.id, 5)
    if not rows:
        await update.effective_message.reply_text("No winners yet. Start a game with /new!")
        return
    lines = [f"{i}. {mention(int(r['user_id']), r['name'])} — <b>{r['wins']}</b> win(s)"
             for i, r in enumerate(rows, 1)]
    await update.effective_message.reply_text("🏅 <b>Top guessers</b>\n" + "\n".join(lines),
                                              parse_mode=ParseMode.HTML)


async def cmd_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the @all audience: every known human, regardless of activity."""
    _, db = state(context)
    dormant_cutoff = time.time() - 30 * 86400
    stats = await db.run(db.member_stats, update.effective_chat.id, dormant_cutoff)
    await update.effective_message.reply_text(
        "👥 <b>Mention roster</b>\n"
        f"Humans mentionable by @all: <b>{stats['humans']}</b>\n"
        f"Quiet for 30+ days (still included): <b>{stats['dormant']}</b>\n"
        f"Bots excluded: <b>{stats['bots']}</b>\n"
        f"Left/kicked (skipped): <b>{stats['left']}</b>\n\n"
        "<i>@all reaches every human above whether active or inactive. Joins, message "
        "authors and admins are stored automatically; use /syncmembers after promoting "
        "an admin. Telegram's Bot API cannot enumerate silent members I have never seen.</i>",
        parse_mode=ParseMode.HTML)


async def cmd_syncmembers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: add the current administrator list to the permanent roster."""
    if not await require_admin(update, context, "sync the member roster"):
        return
    bot, chat, db = bot_of(context), update.effective_chat, state(context)[1]
    synced = await sync_admin_roster(bot, db, chat.id)
    stats = await db.run(db.member_stats, chat.id, time.time() - 30 * 86400)
    await update.effective_message.reply_text(
        f"👥 Roster synced. Captured <b>{synced}</b> human administrator(s).\n"
        f"Mentionable humans: <b>{stats['humans']}</b> · bots excluded: <b>{stats['bots']}</b> "
        f"· left/kicked skipped: <b>{stats['left']}</b>.\n\n"
        "<i>Note: the Bot API has no method to list ordinary group members. The roster is "
        "built permanently from joins, message authors and admins. Previously silent members "
        "who joined before I was added cannot be discovered until they speak.</i>",
        parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# 7. Welcome / goodbye greetings + private /connect console                    #
# --------------------------------------------------------------------------- #

async def cmd_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group: issue a private deep link. PM: show status or consume a token."""
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    bot, (cfg, db) = bot_of(context), state(context)
    if chat.type == ChatType.PRIVATE:
        if context.args:
            await complete_connection(update, context, context.args[0])
            return
        conn = await db.run(db.get_connection, user.id)
        if conn:
            title = await chat_title(bot, int(conn["chat_id"]))
            await message.reply_text(
                f"✅ Connected to <b>{esc(title)}</b>.\n\n"
                "<b>Private group console</b>\n"
                "/welcome — view the welcome message\n"
                "/setwelcome — change it (Change group info right)\n"
                "/rmwelcome — restore the default\n"
                "/goodbye · /setgoodbye · /rmgoodbye\n"
                "/banned — recorded bans\n"
                "/unban &lt;id&gt; — lift a recorded ban (Ban right)\n"
                "/disconnect — unlink this group",
                parse_mode=ParseMode.HTML)
        else:
            await message.reply_text(
                "🔗 <b>Connect a group</b>\n"
                "In your group, send <code>/connect</code>. The owner or an admin with "
                "the <b>Change group info</b> right receives a private link to this chat. "
                "Open it, then manage greetings and bans here.",
                parse_mode=ParseMode.HTML)
        return
    if chat.type not in GROUP_TYPES:
        await message.reply_text("Use /connect inside a group.")
        return
    if not await has_right(bot, chat.id, user.id, "can_change_info", cfg):
        await message.reply_text(
            "🔒 Only the group owner or an admin with the <b>Change group info</b> "
            "right can connect this group.", parse_mode=ParseMode.HTML)
        return
    token = secrets.token_urlsafe(18)
    await db.run(db.create_connect_token, token, chat.id, user.id)
    try:
        username = context.application.bot.username or (await bot.get_me()).username
    except TelegramError:
        username = getattr(bot, "username", None)
    if not username:
        await message.reply_text("Could not resolve my username; please retry.")
        return
    deep_link = f"https://t.me/{username}?start=connect_{token}"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔗 Open private admin console", url=deep_link)]])
    title = getattr(chat, "title", None) or "your group"
    dm_text = (
        f"🔗 <b>Connect {esc(title)}</b>\n"
        "You are eligible because you are the owner or have the Change group info right.\n"
        "Press the button, or send <code>/connect " + token + "</code> here. "
        f"The link expires in {CONNECT_TOKEN_TTL // 60} minutes and works once.")
    try:
        await bot.send_message(chat_id=user.id, text=dm_text, parse_mode=ParseMode.HTML,
                               reply_markup=keyboard)
        sent = True
    except TelegramError as exc:
        sent = False
        logger.info("Could not DM connect link to %s: %s", user.id, exc)
    await message.reply_text(
        ("📬 I sent the private connection link to your DM." if sent else
         "📬 Open the private connection link below (start me in DM first if needed).") +
        "\nIt is valid for 15 minutes and can be used once.",
        parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def complete_connection(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              raw_token: str) -> None:
    """Consume a single-use connect token in private and remember the group."""
    user, message = update.effective_user, update.effective_message
    bot, (cfg, db) = bot_of(context), state(context)
    token = raw_token.removeprefix("connect_").strip()
    row = await db.run(db.get_connect_token, token)
    if not row:
        await message.reply_text("❌ This connection link is invalid, used, or expired. "
                                 "Send /connect in the group again.")
        return
    if int(row["created_by"]) != user.id and user.id not in cfg.owner_ids:
        await message.reply_text("❌ This link was issued to another administrator.")
        return
    if time.time() - float(row["created_at"]) > CONNECT_TOKEN_TTL:
        await db.run(db.delete_connect_token, token)
        await message.reply_text("❌ This link expired. Send /connect in the group again.")
        return
    chat_id = int(row["chat_id"])
    if not await has_right(bot, chat_id, user.id, "can_change_info", cfg):
        await message.reply_text(
            "🔒 The Change group info right is required. Ask the owner to promote you, "
            "then send /connect in the group again.")
        return
    await db.run(db.set_connection, user.id, chat_id)
    await db.run(db.delete_connect_token, token)
    title = await chat_title(bot, chat_id)
    await message.reply_text(
        f"✅ Connected to <b>{esc(title)}</b>.\n"
        "Manage it from here with /welcome, /setwelcome, /rmwelcome, /goodbye, "
        "/setgoodbye, /rmgoodbye, /banned and /unban. Use /disconnect to unlink.",
        parse_mode=ParseMode.HTML)
    logger.info("private console connected user=%s chat=%s", user.id, chat_id)


async def cmd_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("Use /disconnect in a private chat.")
        return
    _, db = state(context)
    await db.run(db.delete_connection, update.effective_user.id)
    await update.effective_message.reply_text(
        "🔌 Disconnected. Your group settings are unchanged.")


async def _show_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         kind: str) -> None:
    target = await greeting_target(update, context, manage=False)
    if not target:
        return
    chat_id, title = target
    _, db = state(context)
    bot = bot_of(context)
    row = await db.run(db.get_greetings, chat_id)
    custom = (row or {}).get(kind) or ""
    default = DEFAULT_WELCOME_TEXT if kind == "welcome" else DEFAULT_GOODBYE_TEXT
    template = custom or default
    count = await member_count(bot, chat_id)
    rendered = render_greeting(template, update.effective_user, title, count)
    icon = "👋"
    label = "Welcome" if kind == "welcome" else "Goodbye"
    await update.effective_message.reply_text(
        f"{icon} <b>{label} message</b> — {esc(title)}\n"
        f"Source: <b>{'custom' if custom else 'default'}</b>\n\n"
        f"<b>Template</b>\n<code>{esc(template)}</code>\n\n"
        f"<b>Rendered for you</b>\n<code>{esc(rendered)}</code>\n\n"
        f"<i>Placeholders:</i>\n{esc(GREETING_PLACEHOLDERS)}\n\n"
        f"Set: /set{kind} · Remove: /rm{kind}",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_greeting(update, context, "welcome")


async def cmd_goodbye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_greeting(update, context, "goodbye")


async def _set_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        kind: str) -> None:
    target = await greeting_target(update, context, manage=True)
    if not target:
        return
    chat_id, title = target
    message = update.effective_message
    text = command_payload(message)
    if not text:
        await message.reply_text(
            f"Usage: <code>/set{kind} your message</code>\n\n"
            f"Example: <code>Hey {{first}}, welcome to {{group}}! 🎉</code>\n\n"
            f"<b>Placeholders</b>\n{esc(GREETING_PLACEHOLDERS)}",
            parse_mode=ParseMode.HTML)
        return
    if len(text) > GREETING_LIMIT:
        await message.reply_text(f"The message is limited to {GREETING_LIMIT} characters.")
        return
    _, db = state(context)
    await db.run(db.set_greeting, chat_id, kind, text, update.effective_user.id)
    rendered = render_greeting(text, update.effective_user, title,
                               await member_count(bot_of(context), chat_id))
    await message.reply_text(
        f"✅ Custom {kind} message saved for <b>{esc(title)}</b>.\n"
        f"<code>{esc(text)}</code>\n\n"
        f"<b>Preview</b>\n<code>{esc(rendered)}</code>\n\n"
        f"Use /{kind} to review or /rm{kind} to reset.",
        parse_mode=ParseMode.HTML)
    logger.info("%s template updated chat=%s by=%s",
                kind, chat_id, update.effective_user.id)


async def cmd_setwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_greeting(update, context, "welcome")


async def cmd_setgoodbye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_greeting(update, context, "goodbye")


async def _remove_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           kind: str) -> None:
    target = await greeting_target(update, context, manage=True)
    if not target:
        return
    chat_id, title = target
    _, db = state(context)
    removed = await db.run(db.clear_greeting, chat_id, kind)
    default = DEFAULT_WELCOME_TEXT if kind == "welcome" else DEFAULT_GOODBYE_TEXT
    await update.effective_message.reply_text(
        (f"🗑️ Custom {kind} removed." if removed else f"No custom {kind} was set.") +
        f"\nThe default is now active for <b>{esc(title)}</b>:\n"
        f"<code>{esc(default)}</code>",
        parse_mode=ParseMode.HTML)


async def cmd_rmwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _remove_greeting(update, context, "welcome")


async def cmd_rmgoodbye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _remove_greeting(update, context, "goodbye")


# --------------------------------------------------------------------------- #
# Utility commands + lifecycle                                                 #
# --------------------------------------------------------------------------- #

async def cmd_banned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List bans recorded by the bot; manual Telegram bans cannot be enumerated."""
    chat, message = update.effective_chat, update.effective_message
    db = state(context)[1]
    bot = bot_of(context)
    if message is None:
        return
    if chat.type == ChatType.PRIVATE:
        target = await private_management_target(update, context, "can_restrict_members")
    else:
        if not await require_right(update, context, "can_restrict_members",
                                   "view banned members"):
            return
        target = (chat.id, await chat_title(bot, chat.id))
    if not target:
        return
    chat_id, title = target
    rows = await db.run(db.list_bans, chat_id, 40)
    if not rows:
        await message.reply_text(
            f"🚫 No bans recorded for <b>{esc(title)}</b> yet.\n"
            "<i>Only bans issued through me are recorded; bans made in the Telegram "
            "admin UI are invisible to bots.</i>", parse_mode=ParseMode.HTML)
        return
    lines = []
    for index, row in enumerate(rows, 1):
        when = datetime.fromtimestamp(float(row["created_at"]), tz=timezone.utc)
        lines.append(
            f"{index}. {mention(int(row['user_id']), row['display_name'] or str(row['user_id']))} "
            f"· <code>{row['user_id']}</code>\n"
            f"   <i>{esc(row['reason'] or 'no reason')}</i> · {when:%d %b %Y}")
    await send_long(bot, update.effective_chat.id,
                    f"🚫 <b>Banned members</b> — {esc(title)}\n"
                    f"Recorded: <b>{len(rows)}</b>\n\n" + "\n".join(lines) +
                    "\n\nLift a ban with <code>/unban &lt;user id&gt;</code>.")


HELP_TEXT = """<b>Miss Sona — command list</b>

<b>Moderation (specific rights required)</b>
/ban · /unban · /kick · /mute · /unmute · /warn · /warns
/ban, /kick, /mute and /warn need the Ban/Restrict members right.
/banned — recorded permanent bans · /trust · /untrust

<b>Welcome &amp; goodbye</b>
/welcome · /setwelcome · /rmwelcome
/goodbye · /setgoodbye · /rmgoodbye
Default welcome: “Hey {first}, welcome to {group}!”
A custom message may use {first} {user} {username} {mention} {group} {id} {count}.
New members verify with the captcha first, then receive the welcome.

<b>Private console</b>
/connect — group owner or Change group info admins get a private link
Then use /welcome /setwelcome /rmwelcome /goodbye /setgoodbye
/rmgoodbye /banned /unban &lt;id&gt; in our private chat. /disconnect to unlink.

<b>Rules &amp; notes</b>
/setrules /rules /setnote /note /notes /delnote

<b>Announcements</b>
/shadowreply &lt;text&gt; — repost as the bot (admin/owner)
@all [message] or /mentionall [message] — every known human,
    active or dormant (bots never mentioned)
@admin [message] or /mentionadmin [message] — human administrators only
/all or /everyone — command aliases that work even with privacy mode ON
/cancel — stop a running broadcast · /mentionstatus — diagnose @all delivery
/mentiontest — send yourself a real HTML mention
/members — inspect the mention roster
/syncmembers — capture current admins into the roster (admin)

Privacy mode: ordinary @all text is delivered only when Telegram's privacy mode
is disabled (or the bot is an admin). Disable it with @BotFather /setprivacy,
then REMOVE and RE-ADD the bot. Commands such as /all always work.

<b>Game</b>
/new /new4 /new5 /new6 /game /stopgame /top
Send a plain word of the right length to guess.
🎯 exact · 🔀 wrong spot · 🚫 absent

<b>Info</b>
/start /help /ping /id

New members must complete the 2-minute captcha. Unapproved links and
prohibited text (including text inside images) are removed automatically."""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = list(context.args or [])
    if args and args[0].lower().startswith("connect_"):
        await complete_connection(update, context, args[0])
        return
    await update.effective_message.reply_text(
        "👋 <b>Miss Sona online.</b>\nI handle moderation, welcome/goodbye messages, "
        "rules, notes, anti-spam, captcha, broadcasts and word games.\n"
        "Group owners and admins with <b>Change group info</b> can send /connect in "
        "their group to manage greetings privately.\nUse /help for the full command list.",
        parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, db = state(context)
    stats = await db.run(db.dashboard, update.effective_chat.id)
    await update.effective_message.reply_text(
        "🏓 <b>pong</b> — polling, database and handlers healthy.\n"
        f"<i>{stats['notes']} notes · {stats['warned']} warned users · "
        f"{stats['roster']} mentionable humans · v{BOT_VERSION}</i>", parse_mode=ParseMode.HTML)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    await update.effective_message.reply_text(
        f"🆔 <b>Chat:</b> <code>{chat.id}</code> ({esc(chat.type)})\n"
        f"<b>You:</b> <code>{user.id}</code> ({esc(user.full_name)})",
        parse_mode=ParseMode.HTML)


async def on_any_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -10: track activity and enforce the unverified-user message gate."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or user.is_bot:
        return
    cfg, db = state(context)
    await db.run(db.touch_activity, chat.id, user)
    bot = bot_of(context)
    # Prove whether the trigger reached us: look for it in the raw update log.
    text = getattr(message, "text", None) or ""
    if parse_broadcast(text, getattr(bot, "username", None)):
        BROADCAST_STATS["observed"] += 1
        logger.info("mention trigger observed chat=%s user=%s text=%r privacy_read_all=%s",
                    chat.id, user.id, text[:120],
                    context.application.bot_data.get("can_read_all_group_messages"))
    # A restricted user should not be able to post; this is a defensive fallback.
    pending = await db.run(db.get_captcha, chat.id, user.id)
    if pending:
        if await is_admin(bot, chat.id, user.id):
            await db.run(db.finish_captcha, chat.id, user.id)  # Admins bypass captcha.
            try:  # Heal a stale restriction left by an earlier captcha version.
                await bot.restrict_chat_member(
                    chat.id, user.id, permissions=await default_permissions(bot, chat.id))
            except TelegramError as exc:
                logger.debug("Could not restore admin permissions for %s: %s", user.id, exc)
            return
        await safe_delete(bot, chat.id, message.message_id)
        raise ApplicationHandlerStop


async def on_service_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback roster capture from new_chat_members / left_chat_member."""
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    _, db = state(context)
    for member in message.new_chat_members or []:
        if member and not member.is_bot:
            await db.run(db.upsert_member, chat.id, member, True)
    left = message.left_chat_member
    if left and not left.is_bot:
        await db.run(db.mark_member_left, chat.id, left.id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, (NetworkError, RetryAfter)):
        logger.warning("Transient Telegram network issue: %s", err)
        return
    logger.error("Unhandled exception while processing an update",
                 exc_info=(type(err), err, getattr(err, "__traceback__", None)))
    cfg = context.application.bot_data.get("cfg")
    if cfg and cfg.error_chat_id:
        try:
            text = f"⚠️ <b>Miss Sona error</b>\n<code>{esc(str(err))[:800]}</code>"
            await context.application.bot.send_message(cfg.error_chat_id, text,
                                                       parse_mode=ParseMode.HTML)
        except TelegramError:
            pass


async def post_init(application: Application) -> None:
    cfg: Config = application.bot_data["cfg"]
    db: Database = application.bot_data["db"]
    if cfg.tesseract_cmd and OCR_AVAILABLE and pytesseract is not None:
        pytesseract.pytesseract.tesseract_cmd = cfg.tesseract_cmd
    await db.run(db.init_schema)
    # Restart safety: re-arm timeouts for users still awaiting verification.
    pending = await db.run(db.pending_captchas)
    now = time.time()
    for row in pending:
        remaining = max(1.0, float(cfg.captcha_timeout) - (now - float(row["created_at"])))
        schedule_captcha_timeout(application, int(row["chat_id"]), int(row["user_id"]),
                                 str(row["token"]), remaining)
    me = await application.bot.get_me()
    privacy = getattr(me, "can_read_all_group_messages", None)
    application.bot_data["can_read_all_group_messages"] = privacy
    logger.info("Miss Sona v%s @%s ready | OCR=%s | DB=%s | pending captchas=%d | owners=%d",
                BOT_VERSION, me.username, "on" if OCR_AVAILABLE else "off", cfg.db_path,
                len(pending), len(cfg.owner_ids))
    logger.info("Mention delivery: privacy_read_all_group_messages=%s — "
                "use @all or /all; /mentionstatus diagnoses delivery", privacy)
    if privacy is False:
        logger.warning(
            "Telegram privacy mode is ENABLED (can_read_all_group_messages=false). "
            "@all is ordinary group text and may not be delivered. Disable it in "
            "@BotFather via /setprivacy, then REMOVE and RE-ADD the bot to the group. "
            "The /all and /mentionall commands work regardless.")


async def post_shutdown(application: Application) -> None:
    db: Database = application.bot_data["db"]
    await db.run(db.close)
    for task in list(BACKGROUND_TASKS):
        task.cancel()
    logger.info("Miss Sona shutdown complete")


def register_handlers(application: Application, cfg: Config) -> None:
    """Explicit handler groups: exactly one handler per group handles an update."""
    groups = filters.ChatType.GROUPS
    text_no_cmd = groups & filters.TEXT & ~filters.COMMAND

    # -10 runs first for every group message; it only stops unverified users.
    application.add_handler(MessageHandler(groups & filters.ALL, on_any_group_message), group=-10)
    # -9 captures membership service messages as a fallback roster source.
    application.add_handler(MessageHandler(
        groups & (filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER),
        on_service_members), group=-9)

    public_commands = {
        "start": cmd_start, "help": cmd_help, "ping": cmd_ping, "id": cmd_id,
    }
    group_commands = {
        "ban": cmd_ban, "kick": cmd_kick, "mute": cmd_mute, "unmute": cmd_unmute,
        "warn": cmd_warn, "warns": cmd_warns,
        "setrules": cmd_setrules, "rules": cmd_rules,
        "setnote": cmd_setnote, "note": cmd_note, "notes": cmd_notes, "delnote": cmd_delnote,
        "trust": cmd_trust, "untrust": cmd_untrust,
        "members": cmd_members, "syncmembers": cmd_syncmembers,
        "all": on_broadcast, "everyone": on_broadcast,
        "mentionall": on_broadcast, "mentionadmin": on_broadcast,
        "cancel": cmd_cancel, "mentionstatus": cmd_mentionstatus,
        "mentiontest": cmd_mentiontest,
        "shadowreply": cmd_shadowreply,
        "new": cmd_new, "new4": cmd_new, "new5": cmd_new, "new6": cmd_new,
        "game": cmd_game, "stopgame": cmd_stopgame, "top": cmd_top,
    }
    # Available in groups and in the /connect private console.
    greeting_commands = {
        "connect": cmd_connect, "disconnect": cmd_disconnect,
        "welcome": cmd_welcome, "setwelcome": cmd_setwelcome, "rmwelcome": cmd_rmwelcome,
        "goodbye": cmd_goodbye, "setgoodbye": cmd_setgoodbye, "rmgoodbye": cmd_rmgoodbye,
        "banned": cmd_banned, "unban": cmd_unban,
    }
    for name, handler in public_commands.items():
        application.add_handler(CommandHandler(name, handler), group=0)
    for name, handler in group_commands.items():
        application.add_handler(CommandHandler(name, handler, filters=groups), group=0)
    for name, handler in greeting_commands.items():
        application.add_handler(CommandHandler(
            name, handler, filters=groups | filters.ChatType.PRIVATE), group=0)

    application.add_handler(CallbackQueryHandler(on_captcha_button, pattern=r"^cv:"), group=0)
    application.add_handler(
        ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER), group=0
    )

    # Group 2 receives every non-command text and parses mentions leniently
    # (@all, @all: hi, @all, hi). Non-triggers return and fall through.
    application.add_handler(MessageHandler(text_no_cmd, on_broadcast), group=2)
    application.add_handler(MessageHandler(text_no_cmd, on_guess), group=3)

    # Group 4: final safeguard pass for text, captions and images.
    application.add_handler(MessageHandler(
        groups & ~filters.COMMAND &
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.IMAGE),
        on_group_message), group=4)
    logger.info("Registered %d command handlers in 6 handler groups",
                len(public_commands) + len(group_commands) + len(greeting_commands))


def main() -> None:
    cfg = Config.from_env()
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    if not OCR_AVAILABLE:
        logger.warning("Pillow/pytesseract unavailable — image OCR is disabled.")

    db = Database(cfg.db_path)
    application = (
        ApplicationBuilder()
        .token(cfg.token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data.update(
        cfg=cfg,
        db=db,
        matcher=KeywordMatcher(cfg.banned_words),
        words=load_word_bank(cfg.words_file),
    )
    register_handlers(application, cfg)
    application.add_error_handler(error_handler)

    logger.info("Starting Miss Sona (python-telegram-bot, long polling)…")
    # ALL_TYPES is required: chat_member updates drive the captcha gate.
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
        timeout=30,
    )


if __name__ == "__main__":
    main()
