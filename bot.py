#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Miss Sona - all-in-one Telegram group management and gaming bot.

Single-file deployment blueprint built for python-telegram-bot v20, v21 and
v22 (no removed status constants; raw Bot API status strings are used).

Modules
-------
1. Moderation & persistence : /ban /mute /unmute /warn (3 strikes -> ban),
                              /setrules /rules /setnote /note in SQLite.
2. Security & anti-spam     : unapproved http(s)/t.me link removal with a
                              temporary warning, keyword filtering and
                              PyTesseract OCR on images with repeat-offender bans.
3. Admin broadcasts         : cooldown + lock throttled @all / @admin HTML
                              mentions with a safe 3-second pacing interval.
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
from telegram.constants import ChatType, MessageEntityType, ParseMode
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
    from PIL import Image, UnidentifiedImageError
    import pytesseract

    OCR_AVAILABLE = True
except ImportError:  # The bot still runs; OCR simply degrades to a warning.
    OCR_AVAILABLE = False
    Image = None  # type: ignore[assignment]
    UnidentifiedImageError = OSError  # type: ignore[assignment,misc]
    pytesseract = None  # type: ignore[assignment]

logger = logging.getLogger("miss_sona")

DEFAULT_BANNED_WORDS = (
    "porn", "xxx", "nsfw", "escort", "phishing", "airdrop-scam",
    "nude", "casino-spam", "crypto-doubler", "free-giftcard",
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
    max_mentions: int
    active_window_days: int
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
            max_mentions=_env_int("MAX_MENTIONS", 40, 1, 500),
            active_window_days=_env_int("ACTIVE_WINDOW_DAYS", 30, 1, 3650),
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
CREATE TABLE IF NOT EXISTS activity (
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    username    TEXT NOT NULL DEFAULT '',
    is_bot      INTEGER NOT NULL DEFAULT 0,
    last_seen   REAL NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_activity_seen ON activity (chat_id, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_wins_chat ON wins (chat_id, wins DESC);
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
            conn.commit()
        logger.info("SQLite schema ready at %s", self.path)

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

    # -- activity -----------------------------------------------------------
    def touch_activity(self, chat_id: int, user: Any) -> None:
        self._query(
            "INSERT INTO activity (chat_id, user_id, name, username, is_bot, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id, user_id) DO UPDATE SET "
            "name=excluded.name, username=excluded.username, is_bot=excluded.is_bot, "
            "last_seen=excluded.last_seen",
            (chat_id, user.id, getattr(user, "full_name", "") or "",
             (getattr(user, "username", "") or "").lower(),
             int(bool(getattr(user, "is_bot", False))), time.time()),
        )

    def active_members(self, chat_id: int, since: float, limit: int) -> list[dict]:
        return self._query(
            "SELECT user_id, name, username FROM activity "
            "WHERE chat_id=? AND last_seen>=? AND is_bot=0 AND user_id>0 "
            "ORDER BY last_seen DESC LIMIT ?", (chat_id, since, limit), fetch="all",
        ) or []

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

    def dashboard(self, chat_id: int) -> dict:
        def count(table: str) -> int:
            row = self._query(f"SELECT COUNT(*) AS n FROM {table} WHERE chat_id=?",
                              (chat_id,), fetch="one")
            return int(row["n"]) if row else 0
        return {"notes": count("notes"), "warned": count("warnings"),
                "strikes": count("content_strikes"), "trusted": count("link_trust"),
                "active": count("activity")}


# --------------------------------------------------------------------------- #
# Shared helpers                                                               #
# --------------------------------------------------------------------------- #

GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)

# Telegram Bot API chat-member status strings. We intentionally compare raw
# strings instead of PTB constants: ChatMemberStatus.CREATOR was renamed to
# OWNER in python-telegram-bot 20 and CREATOR no longer exists. Raw values
# keep this bot working on PTB 20, 21 and 22 alike.
STATUS_MEMBER = "member"
STATUS_ADMIN = "administrator"
STATUS_OWNER = "creator"  # Bot API value behind ChatMemberStatus.OWNER
STATUS_RESTRICTED = "restricted"
ADMIN_STATUSES = frozenset({STATUS_ADMIN, STATUS_OWNER})
MEMBER_STATUSES = frozenset({STATUS_MEMBER, STATUS_ADMIN, STATUS_OWNER})


def status_value(status: Any) -> str:
    """Return the raw Bot API status string for a str/Enum/None value."""
    value = getattr(status, "value", status)
    return str(value).lower() if value is not None else ""


BACKGROUND_TASKS: set[asyncio.Task] = set()
_ADMIN_CACHE: dict[tuple[int, int], tuple[bool, float]] = {}
BROADCAST_LAST: dict[int, float] = {}
BROADCAST_LOCKS: dict[int, asyncio.Lock] = {}
SHADOW_LAST: dict[int, float] = {}

LINK_RE = re.compile(
    r"(?i)(?:https?://\S+|www\.\S+|(?:t\.me|telegram\.me|tg\.me)/[^\s]+|tg://\S+)"
)
BROADCAST_RE = re.compile(r"^@(all|admin)\b(?:\s+([\s\S]+))?$", re.IGNORECASE)
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
        # Version-proof: 'administrator' or 'creator' (PTB 20+ exposes the
        # owner status as ChatMemberStatus.OWNER, not the old CREATOR).
        allowed = status_value(getattr(member, "status", "")) in ADMIN_STATUSES
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
    if not await require_admin(update, context, "ban members"):
        return
    message, chat = update.effective_message, update.effective_chat
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
    await message.reply_text(f"🔨 {mention(target_id, name)} banned.\n<i>Reason:</i> {esc(reason)}",
                             parse_mode=ParseMode.HTML)
    logger.info("ban chat=%s target=%s by=%s", chat.id, target_id, update.effective_user.id)


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context, "unban members"):
        return
    message, chat = update.effective_message, update.effective_chat
    target_id, name = await resolve_target(update, context)
    if target_id is None:
        await message.reply_text("Usage: <code>/unban 123456789</code> (a numeric ID is required).",
                                 parse_mode=ParseMode.HTML)
        return
    try:
        await bot_of(context).unban_chat_member(chat.id, target_id, only_if_banned=True)
    except TelegramError as exc:
        await message.reply_text(f"Could not unban: {esc(exc)}", parse_mode=ParseMode.HTML)
        return
    await message.reply_text(f"✅ {mention(target_id, name)} may join again.", parse_mode=ParseMode.HTML)


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context, "restrict members"):
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
    if not await require_admin(update, context, "restore members"):
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
    if not await require_admin(update, context, "warn members"):
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


async def send_mention_batch(bot, chat_id: int, members: list[dict]) -> None:
    """One HTML message containing one or more clickable user mentions."""
    text = " ".join(
        mention(int(m["user_id"]), m.get("name") or m.get("username") or str(m["user_id"]))
        for m in members
    )
    for attempt in range(3):
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
            return
        except RetryAfter as exc:
            # Telegram asked us explicitly to slow down: obey the flood-wait.
            await asyncio.sleep(float(exc.retry_after) + 1.0)
        except (BadRequest, Forbidden, TelegramError) as exc:
            logger.warning("Mention batch failed in %s: %s", chat_id, exc)
            return
    logger.warning("Giving up on a mention batch in %s after flood retries", chat_id)


async def on_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """@all [message] / @admin [message] - admin only, paced by asyncio.sleep(3)."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    match = BROADCAST_RE.match(message.text or "")
    if not match:
        raise ApplicationHandlerStop
    mode = match.group(1).lower()
    note = (match.group(2) or "").strip()
    cfg, db = state(context)
    bot = bot_of(context)
    application = context.application

    if not await is_privileged(bot, chat.id, user.id, cfg):
        await safe_delete(bot, chat.id, message.message_id)
        await temp_reply(bot, chat.id,
                         f"🔒 {mention(user.id, user.full_name)}, mass mentions are admin-only.",
                         delay=8, application=application)
        raise ApplicationHandlerStop

    lock = BROADCAST_LOCKS.setdefault(chat.id, asyncio.Lock())
    if lock.locked():
        await temp_reply(bot, chat.id, "⏳ A broadcast is already running in this chat.",
                         delay=6, application=application)
        raise ApplicationHandlerStop
    now = time.monotonic()
    elapsed = now - BROADCAST_LAST.get(chat.id, 0.0)
    if elapsed < cfg.broadcast_cooldown:
        await temp_reply(bot, chat.id,
                         f"⏳ Please wait <b>{int(cfg.broadcast_cooldown - elapsed)}s</b> "
                         "before another broadcast.", delay=6, application=application)
        raise ApplicationHandlerStop

    async with lock:
        BROADCAST_LAST[chat.id] = now
        if mode == "admin":
            try:
                admins = await bot.get_chat_administrators(chat.id)
            except TelegramError as exc:
                await temp_reply(bot, chat.id, f"Could not read admin list: {esc(exc)}",
                                 delay=8, application=application)
                raise ApplicationHandlerStop
            targets = [
                {"user_id": a.user.id, "name": a.user.full_name, "username": a.user.username or ""}
                for a in admins if a.user and not a.user.is_bot
            ]
            label = "administrators"
        else:
            since = time.time() - cfg.active_window_days * 86400
            targets = await db.run(db.active_members, chat.id, since, cfg.max_mentions)
            label = f"active members (last {cfg.active_window_days} days)"

        if not targets:
            await temp_reply(bot, chat.id,
                             "No eligible members found yet. The activity table fills as people chat.",
                             delay=10, application=application)
            raise ApplicationHandlerStop

        header = (f"📢 <b>Announcement from {mention(user.id, user.full_name)}</b>\n"
                  f"{esc(note) if note else '(no message attached)'}\n"
                  f"<i>Tagging {len(targets)} {label} — paced at {cfg.mention_delay:g}s.</i>")
        await bot.send_message(chat_id=chat.id, text=header, parse_mode=ParseMode.HTML)

        batches = list(chunks(targets, cfg.mention_batch_size))
        for index, batch in enumerate(batches):
            await send_mention_batch(bot, chat.id, batch)
            if index < len(batches) - 1:
                # The safe interval that prevents Telegram flood-wait errors.
                await asyncio.sleep(cfg.mention_delay)
        logger.info("broadcast mode=%s chat=%s targets=%d batches=%d",
                    mode, chat.id, len(targets), len(batches))
    raise ApplicationHandlerStop  # never let the safeguard layer re-read this text


# --------------------------------------------------------------------------- #
# 4. Captcha verification on join                                              #
# --------------------------------------------------------------------------- #

def memberish(member: Any) -> bool:
    """True for statuses that count as being in the chat (version-proof)."""
    status = status_value(getattr(member, "status", ""))
    if status in MEMBER_STATUSES:
        return True
    return status == STATUS_RESTRICTED and bool(getattr(member, "is_member", False))


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
    try:
        await kick_user(bot, chat_id, user_id)
        await db.run(db.finish_captcha, chat_id, user_id)
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
    """Restrict new joiners and send the inline verification button."""
    change = update.chat_member
    if not change or not change.new_chat_member:
        return
    old, new = change.old_chat_member, change.new_chat_member
    if memberish(old) or not memberish(new):
        return
    user, chat = new.user, change.chat
    if user.is_bot or user.id in state(context)[0].owner_ids:
        return
    bot = bot_of(context)
    cfg, db = state(context)
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
    welcome_text = (
        f"👋 Welcome, {mention(user.id, user.full_name)}!\n\n"
        "To keep this group spam-free, please confirm you're human by pressing "
        f"the button below within <b>{cfg.captcha_timeout // 60} minute(s)</b>. "
        "You can't post messages until you do."
    )
    try:
        sent = await bot.send_message(chat_id=chat.id, text=welcome_text,
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
    await query.answer("✅ Verified — welcome to the group!")
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=int(row["message_id"]),
            text=f"✅ {mention(target_id, query.from_user.full_name)} is verified. Welcome!",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass
    logger.info("captcha verified chat=%s user=%s", chat_id, target_id)


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


# --------------------------------------------------------------------------- #
# Utility commands + lifecycle                                                 #
# --------------------------------------------------------------------------- #

HELP_TEXT = """<b>Miss Sona — command list</b>

<b>Moderation (admin)</b>
/ban /unban /mute /unmute /warn /warns /trust /untrust

<b>Rules &amp; notes</b>
/setrules /rules /setnote /note /notes /delnote

<b>Announcements</b>
/shadowreply &lt;text&gt; — repost as the bot (admin/owner)
@all [message] / @admin [message] — paced mentions (admin)

<b>Game</b>
/new /new4 /new5 /new6 /game /stopgame /top
Send a plain word of the right length to guess.
🎯 exact · 🔀 wrong spot · 🚫 absent

<b>Info</b>
/start /help /ping /id

New members must complete the 2-minute captcha. Unapproved links and
prohibited text (including text inside images) are removed automatically."""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "👋 <b>Miss Sona online.</b>\nI handle moderation, rules, notes, anti-spam, "
        "captcha, broadcasts and word games.\nUse /help for the full command list.",
        parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, db = state(context)
    stats = await db.run(db.dashboard, update.effective_chat.id)
    await update.effective_message.reply_text(
        "🏓 <b>pong</b> — polling, database and handlers healthy.\n"
        f"<i>{stats['notes']} notes · {stats['warned']} warned users · "
        f"{stats['active']} tracked members</i>", parse_mode=ParseMode.HTML)


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
    # A restricted user should not be able to post; this is a defensive fallback.
    pending = await db.run(db.get_captcha, chat.id, user.id)
    if pending:
        await safe_delete(bot_of(context), chat.id, message.message_id)
        raise ApplicationHandlerStop


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
    logger.info("Miss Sona @%s ready | OCR=%s | DB=%s | pending captchas=%d | owners=%d",
                me.username, "on" if OCR_AVAILABLE else "off", cfg.db_path,
                len(pending), len(cfg.owner_ids))


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

    public_commands = {
        "start": cmd_start, "help": cmd_help, "ping": cmd_ping, "id": cmd_id,
    }
    group_commands = {
        "ban": cmd_ban, "unban": cmd_unban, "mute": cmd_mute, "unmute": cmd_unmute,
        "warn": cmd_warn, "warns": cmd_warns,
        "setrules": cmd_setrules, "rules": cmd_rules,
        "setnote": cmd_setnote, "note": cmd_note, "notes": cmd_notes, "delnote": cmd_delnote,
        "trust": cmd_trust, "untrust": cmd_untrust,
        "shadowreply": cmd_shadowreply,
        "new": cmd_new, "new4": cmd_new, "new5": cmd_new, "new6": cmd_new,
        "game": cmd_game, "stopgame": cmd_stopgame, "top": cmd_top,
    }
    for name, handler in public_commands.items():
        application.add_handler(CommandHandler(name, handler), group=0)
    for name, handler in group_commands.items():
        application.add_handler(CommandHandler(name, handler, filters=groups), group=0)

    application.add_handler(CallbackQueryHandler(on_captcha_button, pattern=r"^cv:"), group=0)
    application.add_handler(
        ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER), group=0
    )

    # Group 2: mass mentions. Group 3: game guesses. Both stop further processing.
    application.add_handler(MessageHandler(
        text_no_cmd & filters.Regex(r"(?i)^@(all|admin)\b"), on_broadcast), group=2)
    application.add_handler(MessageHandler(text_no_cmd, on_guess), group=3)

    # Group 4: final safeguard pass for text, captions and images.
    application.add_handler(MessageHandler(
        groups & ~filters.COMMAND &
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.IMAGE),
        on_group_message), group=4)
    logger.info("Registered %d command handlers in 6 handler groups",
                len(public_commands) + len(group_commands))


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
