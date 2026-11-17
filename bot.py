#!/usr/bin/env python3
"""
Miss Sona — Industrial-Grade Telegram Group Management Bot
===========================================================
Elite software architecture | python-telegram-bot v20+ | asyncio + SQLite (WAL)

Features:
  • Core moderation: /ban /unban /mute /unmute /warn (3-strike auto-ban) + /rules /setrules /note /setnote /delnote + /trust /untrust
  • SQLite persistence via asyncio.to_thread with WAL + composite primary keys
  • Granular locks: anti-link, anti-spam, banned-word vault (owner global sync)
  • AI-OCR Safety Pipeline: Pillow + Tesseract OCR — deletes prohibited/illegal text in photos/files
  • Interactive CAPTCHA gate with JobQueue timeout + activity pool
  • Throttled mass mentions: @all / @admin in batches of 5 users every 3s
  • Shadow-Reply: /shadowreply for anonymous admin sends
  • Multiplayer Wordle/Jumble: /new /new4 /new5 /new6 + /top /game

Setup:
  pip install "python-telegram-bot[job-queue]>=20.8" Pillow pytesseract
  # Linux OCR binary: sudo apt install tesseract-ocr
  export BOT_TOKEN="your_bot_token"
  export OWNER_ID="123456789"   # optional, Telegram user ID of bot owner
  python miss_sona.py

Zero external dependencies beyond python-telegram-bot + Pillow + pytesseract (both optional with graceful fallback).
"""
from __future__ import annotations

import asyncio
import datetime
import html
import io
import logging
import os
import random
import re
import sqlite3
from pathlib import Path
from typing import Optional, List, Tuple

# ──────────────────────────────────────────
# Optional OCR imports (graceful fallback)
# ──────────────────────────────────────────
try:
    from PIL import Image  # type: ignore
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    Image = None  # type: ignore

try:
    import pytesseract  # type: ignore
    TESS_AVAILABLE = True
except ImportError:
    TESS_AVAILABLE = False
    pytesseract = None  # type: ignore

# PTB v20+
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatPermissions,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

# ──────────────────────────────────────────
# Logging
# ──────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("MissSona")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ──────────────────────────────────────────
# Config
# ──────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
OWNER_ID: Optional[int] = int(os.getenv("OWNER_ID", "0")) or None
DB_PATH: str = os.getenv("DB_PATH", "miss_sona.db")

WARN_LIMIT = 3
CAPTCHA_TIMEOUT = 120  # seconds
CAPTCHA_BAN_ON_FAIL = True
TAG_BATCH_SIZE = 5
TAG_BATCH_DELAY = 3.0
ILLEGAL_NOTICE = "I found something illegal so I deleted it"
ILLEGAL_NOTICE_TTL = 15  # seconds auto-delete notice

# Default chat protection toggles
DEFAULT_SETTINGS = {
    "antilink": 1,
    "antispam": 1,
    "ocr_protection": 1,
    "captcha_enabled": 1,
}

# ──────────────────────────────────────────
# Word Lists — Wordle / Jumble
# ──────────────────────────────────────────
WORD_BANK = {
    4: [
        "code","game","sona","rose","bolt","flux","kilo","nova","apex","zeal",
        "iris","luna","aura","vibe","echo","frost","glow","kind","mint","nest",
        "opal","peak","quip","rift","sage","tide","vale","warp","yarn","zest",
        "bark","clap","dune","fern","gale","howl","jade","kite","lark","moss",
        "neon","oath","plum","quay","rust","star","tune","urge","void","wisp",
    ],
    5: [
        "sonar","crane","ultra","blitz","frost","flame","glide","prism","quirk","amber",
        "blaze","crest","drift","ember","forge","gloom","haven","ivory","jolly","kneel",
        "lunar","mango","nymph","ocean","pride","quest","rally","stone","theta","unity",
        "vivid","whirl","xenon","yield","zebra","apple","brave","crown","dream","eager",
        "flora","giant","happy","ideal","jazzy","knack","light","magic","noble","olive",
    ],
    6: [
        "sonaai","python","rocket","silver","golden","cobalt","nebula","jasper","quartz","velvet",
        "arcade","breeze","castle","desert","engine","falcon","galaxy","harbor","island","jungle",
        "kernel","legend","marble","nimbus","orange","planet","quiver","ranger","summit","tunnel",
        "unique","voyage","wisdom","yonder","zenith","bronze","crystal","diamond","emerald","fortune",
        "guitar","horizon","imagine","journey","kingdom","lantern","midnight","natural","outlook","parrot",
    ],
}

# Fallback illegal/prohibited patterns for OCR + text scans
# Includes ToS-violating + placeholder illegal categories. Extend as needed.
OCR_BLACKLIST = [
    "csam", "child porn", "cp video", "minor nude", "bomb making", "how to make bomb",
    "terrorist recruitment", "kill all", "illegal drugs buy", "credit card dump",
]
# URL / malicious patterns
LINK_RE = re.compile(r"(?:https?://|t\.me/|telegram\.me/|telegram\.dog/|wa\.me/|discord\.gg/)\S+", re.I)
SPAM_RE = re.compile(r"(.)\1{7,}|(.)(\1.{0,2}){6,}", re.I)  # repeated chars / flood

# In-memory game state is also persisted to DB; this mirrors for quick access
# pending captcha: {(chat_id, user_id): job}
pending_captcha: dict[Tuple[int, int], object] = {}

# ──────────────────────────────────────────
# SQLite Persistence Layer — thread-safe via asyncio.to_thread + WAL
# ──────────────────────────────────────────
class Database:
    def __init__(self, path: str):
        self.path = path
        self._init_lock = asyncio.Lock()

    # low-level sync helpers — always run in thread
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, check_same_thread=False, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA foreign_keys=ON;")
        con.execute("PRAGMA busy_timeout=10000;")
        return con

    def _init_sync(self):
        con = self._connect()
        cur = con.cursor()
        # Warnings — composite PK (chat_id, user_id)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS warnings(
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            last_reason TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(chat_id, user_id)
        );""")
        # Rules — one per chat
        cur.execute("""
        CREATE TABLE IF NOT EXISTS rules(
            chat_id INTEGER PRIMARY KEY,
            content TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );""")
        # Notes — composite PK (chat_id, name)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS notes(
            chat_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(chat_id, name)
        );""")
        # Trusted users — composite PK
        cur.execute("""
        CREATE TABLE IF NOT EXISTS trusted(
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(chat_id, user_id)
        );""")
        # Global banned-word vault — shared across all groups
        cur.execute("""
        CREATE TABLE IF NOT EXISTS global_banned_words(
            word TEXT PRIMARY KEY COLLATE NOCASE,
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );""")
        # Chat settings — granular locks
        cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings(
            chat_id INTEGER PRIMARY KEY,
            antilink INTEGER DEFAULT 1,
            antispam INTEGER DEFAULT 1,
            ocr_protection INTEGER DEFAULT 1,
            captcha_enabled INTEGER DEFAULT 1
        );""")
        # Active user pool
        cur.execute("""
        CREATE TABLE IF NOT EXISTS active_users(
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            fullname TEXT,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(chat_id, user_id)
        );""")
        # Wordle active game per chat
        cur.execute("""
        CREATE TABLE IF NOT EXISTS games(
            chat_id INTEGER PRIMARY KEY,
            word TEXT NOT NULL,
            length INTEGER NOT NULL,
            starter_id INTEGER,
            attempts INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );""")
        # Leaderboard — career wins
        cur.execute("""
        CREATE TABLE IF NOT EXISTS leaderboard(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            wins INTEGER DEFAULT 0,
            games_played INTEGER DEFAULT 0
        );""")
        con.commit()
        con.close()

    async def init(self):
        async with self._init_lock:
            await asyncio.to_thread(self._init_sync)
        log.info("SQLite initialized at %s (WAL mode)", self.path)

    # ——— generic helpers ———
    async def _exec(self, sql: str, params: tuple = (), fetch: str = "none"):
        def _run():
            con = self._connect()
            try:
                cur = con.cursor()
                cur.execute(sql, params)
                if fetch == "one":
                    row = cur.fetchone()
                    con.commit()
                    return row
                elif fetch == "all":
                    rows = cur.fetchall()
                    con.commit()
                    return rows
                elif fetch == "scalar":
                    row = cur.fetchone()
                    con.commit()
                    return row[0] if row else None
                con.commit()
                return cur.rowcount
            finally:
                con.close()
        return await asyncio.to_thread(_run)

    async def _exec_many(self, sql: str, seq):
        def _run():
            con = self._connect()
            try:
                cur = con.cursor()
                cur.executemany(sql, seq)
                con.commit()
                return cur.rowcount
            finally:
                con.close()
        return await asyncio.to_thread(_run)

    # ─ warnings ─
    async def add_warn(self, chat_id: int, user_id: int, reason: str = "") -> int:
        def _run():
            con = self._connect()
            try:
                cur = con.cursor()
                cur.execute("INSERT OR IGNORE INTO warnings(chat_id, user_id, count) VALUES(?,?,0)", (chat_id, user_id))
                cur.execute("UPDATE warnings SET count = count + 1, last_reason=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND user_id=?",
                            (reason, chat_id, user_id))
                cur.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id))
                row = cur.fetchone()
                con.commit()
                return row[0] if row else 0
            finally:
                con.close()
        return await asyncio.to_thread(_run)

    async def get_warns(self, chat_id: int, user_id: int) -> int:
        row = await self._exec("SELECT count FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id), fetch="one")
        return row[0] if row else 0

    async def reset_warns(self, chat_id: int, user_id: int):
        await self._exec("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    # ─ rules ─
    async def set_rules(self, chat_id: int, content: str):
        await self._exec("INSERT INTO rules(chat_id, content) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET content=excluded.content, updated_at=CURRENT_TIMESTAMP", (chat_id, content))

    async def get_rules(self, chat_id: int) -> Optional[str]:
        row = await self._exec("SELECT content FROM rules WHERE chat_id=?", (chat_id,), fetch="one")
        return row[0] if row else None

    # ─ notes ─
    async def set_note(self, chat_id: int, name: str, content: str, created_by: int):
        await self._exec("INSERT INTO notes(chat_id, name, content, created_by) VALUES(?,?,?,?) ON CONFLICT(chat_id, name) DO UPDATE SET content=excluded.content, created_by=excluded.created_by, created_at=CURRENT_TIMESTAMP",
                         (chat_id, name.lower(), content, created_by))

    async def get_note(self, chat_id: int, name: str) -> Optional[str]:
        row = await self._exec("SELECT content FROM notes WHERE chat_id=? AND name=?", (chat_id, name.lower()), fetch="one")
        return row[0] if row else None

    async def del_note(self, chat_id: int, name: str) -> int:
        return await self._exec("DELETE FROM notes WHERE chat_id=? AND name=?", (chat_id, name.lower()))

    async def list_notes(self, chat_id: int) -> List[str]:
        rows = await self._exec("SELECT name FROM notes WHERE chat_id=? ORDER BY name", (chat_id,), fetch="all")
        return [r[0] for r in (rows or [])]

    # ─ trusted ─
    async def trust(self, chat_id: int, user_id: int, added_by: int):
        await self._exec("INSERT OR IGNORE INTO trusted(chat_id, user_id, added_by) VALUES(?,?,?)", (chat_id, user_id, added_by))

    async def untrust(self, chat_id: int, user_id: int):
        await self._exec("DELETE FROM trusted WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    async def is_trusted(self, chat_id: int, user_id: int) -> bool:
        row = await self._exec("SELECT 1 FROM trusted WHERE chat_id=? AND user_id=?", (chat_id, user_id), fetch="one")
        return bool(row)

    # ─ global banned words ─
    async def add_global_word(self, word: str, added_by: int):
        await self._exec("INSERT OR IGNORE INTO global_banned_words(word, added_by) VALUES(?,?)", (word.lower().strip(), added_by))

    async def remove_global_word(self, word: str) -> int:
        return await self._exec("DELETE FROM global_banned_words WHERE word=?", (word.lower().strip(),))

    async def list_global_words(self) -> List[str]:
        rows = await self._exec("SELECT word FROM global_banned_words ORDER BY word", fetch="all")
        return [r[0] for r in (rows or [])]

    async def is_global_banned(self, text: str) -> Optional[str]:
        """Return matched banned word if any, else None. Case-insensitive substring scan."""
        words = await self.list_global_words()
        low = text.lower()
        for w in words:
            if w and w.lower() in low:
                return w
        return None

    # ─ chat settings ─
    async def get_settings(self, chat_id: int) -> dict:
        row = await self._exec("SELECT antilink, antispam, ocr_protection, captcha_enabled FROM chat_settings WHERE chat_id=?", (chat_id,), fetch="one")
        if row:
            return {"antilink": bool(row[0]), "antispam": bool(row[1]), "ocr_protection": bool(row[2]), "captcha_enabled": bool(row[3])}
        # insert defaults
        await self._exec("INSERT OR IGNORE INTO chat_settings(chat_id, antilink, antispam, ocr_protection, captcha_enabled) VALUES(?,?,?,?,?)",
                         (chat_id, DEFAULT_SETTINGS["antilink"], DEFAULT_SETTINGS["antispam"], DEFAULT_SETTINGS["ocr_protection"], DEFAULT_SETTINGS["captcha_enabled"]))
        return {k: bool(v) for k, v in DEFAULT_SETTINGS.items()}

    async def set_setting(self, chat_id: int, key: str, value: bool):
        if key not in DEFAULT_SETTINGS:
            return
        # ensure row exists
        await self.get_settings(chat_id)
        await self._exec(f"UPDATE chat_settings SET {key}=? WHERE chat_id=?", (int(value), chat_id))

    # ─ active users ─
    async def upsert_active(self, chat_id: int, user_id: int, username: Optional[str], fullname: str):
        await self._exec("""
            INSERT INTO active_users(chat_id, user_id, username, fullname, last_seen)
            VALUES(?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET username=excluded.username, fullname=excluded.fullname, last_seen=CURRENT_TIMESTAMP
        """, (chat_id, user_id, username, fullname))

    async def get_active_users(self, chat_id: int, limit: int = 200) -> List[Tuple[int, Optional[str], str]]:
        rows = await self._exec("SELECT user_id, username, fullname FROM active_users WHERE chat_id=? ORDER BY last_seen DESC LIMIT ?", (chat_id, limit), fetch="all")
        return rows or []

    async def prune_inactive(self, days: int = 30):
        await self._exec("DELETE FROM active_users WHERE last_seen < datetime('now', ?)", (f"-{days} days",))

    # ─ games ─
    async def start_game(self, chat_id: int, word: str, starter_id: int):
        await self._exec("INSERT INTO games(chat_id, word, length, starter_id, attempts, is_active) VALUES(?,?,?,?,0,1) ON CONFLICT(chat_id) DO UPDATE SET word=excluded.word, length=excluded.length, starter_id=excluded.starter_id, attempts=0, is_active=1, started_at=CURRENT_TIMESTAMP",
                         (chat_id, word.lower(), len(word), starter_id))

    async def get_game(self, chat_id: int) -> Optional[Tuple[str, int, int, int]]:
        # returns (word, length, starter_id, attempts)
        row = await self._exec("SELECT word, length, starter_id, attempts FROM games WHERE chat_id=? AND is_active=1", (chat_id,), fetch="one")
        return row if row else None

    async def bump_attempts(self, chat_id: int):
        await self._exec("UPDATE games SET attempts=attempts+1 WHERE chat_id=?", (chat_id,))

    async def stop_game(self, chat_id: int):
        await self._exec("UPDATE games SET is_active=0 WHERE chat_id=?", (chat_id,))

    # ─ leaderboard ─
    async def add_win(self, user_id: int, username: Optional[str]):
        await self._exec("INSERT INTO leaderboard(user_id, username, wins, games_played) VALUES(?,?,1,1) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, wins=wins+1, games_played=games_played+1", (user_id, username or str(user_id)))

    async def add_played(self, user_id: int, username: Optional[str]):
        await self._exec("INSERT INTO leaderboard(user_id, username, games_played) VALUES(?,?,1) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, games_played=games_played+1", (user_id, username or str(user_id)))

    async def top_players(self, limit: int = 10) -> List[Tuple[int, str, int]]:
        rows = await self._exec("SELECT user_id, username, wins FROM leaderboard ORDER BY wins DESC, games_played DESC LIMIT ?", (limit,), fetch="all")
        return rows or []

db = Database(DB_PATH)

# ──────────────────────────────────────────
# Helpers & Decorators
# ──────────────────────────────────────────
def esc(s: str) -> str:
    return html.escape(s or "", quote=False)

def mention_html(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name)}</a>'

def parse_duration(s: str) -> Optional[datetime.timedelta]:
    """Parse '30m', '2h', '1d', '1w' into timedelta."""
    m = re.match(r"^\s*(\d+)\s*([smhdw])\s*$", s.strip(), re.I)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    mul = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return datetime.timedelta(seconds=n * mul[unit])

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: Optional[int] = None) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    uid = user_id or (update.effective_user.id if update.effective_user else None)
    if not uid:
        return False
    # Owner bypass
    if OWNER_ID and uid == OWNER_ID:
        return True
    try:
        m = await context.bot.get_chat_member(chat.id, uid)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False

async def is_owner_user(user_id: int) -> bool:
    return bool(OWNER_ID and user_id == OWNER_ID)

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[Tuple[int, str]]:
    """Resolve target user from reply or @username / ID arg."""
    msg = update.effective_message
    if not msg:
        return None
    # 1) reply target
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        return u.id, u.full_name
    # 2) arg
    if context.args:
        arg = context.args[0].lstrip("@")
        # numeric ID
        if arg.isdigit():
            return int(arg), f"user {arg}"
        # username — we can't resolve username to ID without extra lookup; store active pool attempt
        # try to find in active DB
        chat_id = update.effective_chat.id  # type: ignore
        actives = await db.get_active_users(chat_id, limit=500)
        for uid, uname, fname in actives:
            if uname and uname.lower() == arg.lower():
                return uid, fname
        # fallback: treat as unknown but return failure
        return None
    return None

async def get_target_or_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tgt = await resolve_target(update, context)
    return tgt

async def send_temp_notice(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, ttl: int = ILLEGAL_NOTICE_TTL):
    """Send a temporary bot notice that auto-deletes after ttl."""
    try:
        m = await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        # schedule deletion
        if context.job_queue:
            context.job_queue.run_once(lambda ctx: asyncio.create_task(_delete_later(ctx, chat_id, m.message_id)), ttl, name=f"del_notice_{chat_id}_{m.message_id}")
        else:
            asyncio.create_task(_auto_delete_later(context, chat_id, m.message_id, ttl))
        return m
    except Exception as e:
        log.debug("send_temp_notice failed: %s", e)

async def _delete_later(ctx, chat_id: int, msg_id: int):
    try:
        await ctx.bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

async def _auto_delete_later(context, chat_id, msg_id, ttl):
    await asyncio.sleep(ttl)
    try:
        await context.bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

def admin_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not await is_admin(update, context):
            if update.effective_message:
                await update.effective_message.reply_text("🚫 Only admins can use this command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def owner_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        uid = update.effective_user.id if update.effective_user else 0
        if not await is_owner_user(uid):
            if update.effective_message:
                await update.effective_message.reply_text("🔒 Owner-only command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# ──────────────────────────────────────────
# Moderation Commands
# ──────────────────────────────────────────
@admin_required
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    tgt = await get_target_or_reply(update, context)
    if not tgt:
        await msg.reply_text("Usage: reply to a user or <code>/ban @username|ID [reason]</code>", parse_mode=ParseMode.HTML)
        return
    uid, name = tgt
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Banned by admin"
    # prevent banning admins/owner/bot
    if OWNER_ID and uid == OWNER_ID:
        await msg.reply_text("I can't ban my owner.")
        return
    if await is_admin(update, context, uid):
        await msg.reply_text("I can't ban an admin — demote first.")
        return
    try:
        await context.bot.ban_chat_member(chat.id, uid)
        await msg.reply_html(f"🔨 Banned {mention_html(uid, name)} — <i>{esc(reason)}</i>")
        log.info("BAN %s -> %s (%s)", chat.id, uid, reason)
    except BadRequest as e:
        await msg.reply_text(f"Ban failed: {e.message}")
    except Exception as e:
        await msg.reply_text(f"Error: {e}")

@admin_required
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    tgt = await get_target_or_reply(update, context)
    if not tgt:
        await msg.reply_text("Usage: reply or <code>/unban @username|ID</code>", parse_mode=ParseMode.HTML)
        return
    uid, name = tgt
    try:
        await context.bot.unban_chat_member(chat.id, uid)
        await msg.reply_html(f"✅ Unbanned {mention_html(uid, name)}")
    except Exception as e:
        await msg.reply_text(f"Unban failed: {e}")

@admin_required
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    tgt = await get_target_or_reply(update, context)
    if not tgt:
        await msg.reply_text("Usage: reply or <code>/mute @username|ID [duration: 30m/2h/1d]</code>", parse_mode=ParseMode.HTML)
        return
    uid, name = tgt
    if await is_admin(update, context, uid):
        await msg.reply_text("Can't mute an admin.")
        return
    # optional duration is last arg if parseable
    until = None
    dur_text = ""
    if context.args and len(context.args) >= 2:
        dur = parse_duration(context.args[-1])
        if dur:
            until = datetime.datetime.now(datetime.timezone.utc) + dur
            dur_text = f" for {context.args[-1]}"
    try:
        perms = ChatPermissions(can_send_messages=False)
        await context.bot.restrict_chat_member(chat.id, uid, permissions=perms, until_date=until)
        await msg.reply_html(f"🔇 Muted {mention_html(uid, name)}{esc(dur_text)}")
    except Exception as e:
        await msg.reply_text(f"Mute failed: {e}")

@admin_required
async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    tgt = await get_target_or_reply(update, context)
    if not tgt:
        await msg.reply_text("Usage: reply or <code>/unmute @username|ID</code>", parse_mode=ParseMode.HTML)
        return
    uid, name = tgt
    try:
        perms = ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)
        await context.bot.restrict_chat_member(chat.id, uid, permissions=perms)
        await msg.reply_html(f"🔊 Unmuted {mention_html(uid, name)}")
    except Exception as e:
        await msg.reply_text(f"Unmute failed: {e}")

@admin_required
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    tgt = await get_target_or_reply(update, context)
    if not tgt:
        await msg.reply_text("Usage: reply or <code>/warn @username|ID [reason]</code>", parse_mode=ParseMode.HTML)
        return
    uid, name = tgt
    if await is_admin(update, context, uid):
        await msg.reply_text("Can't warn an admin.")
        return
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason"
    count = await db.add_warn(chat.id, uid, reason)
    await msg.reply_html(f"⚠️ Warned {mention_html(uid, name)} ({count}/{WARN_LIMIT}) — <i>{esc(reason)}</i>")
    if count >= WARN_LIMIT:
        try:
            await context.bot.ban_chat_member(chat.id, uid)
            await context.bot.send_message(chat.id, f"🚫 {mention_html(uid, name)} reached {WARN_LIMIT} warnings and was <b>banned</b>.", parse_mode=ParseMode.HTML)
            await db.reset_warns(chat.id, uid)
            log.info("AUTOBAN via warns %s -> %s", chat.id, uid)
        except Exception as e:
            await msg.reply_text(f"Auto-ban failed: {e}")
            await db.reset_warns(chat.id, uid)

async def cmd_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # check own or target
    chat = update.effective_chat
    msg = update.effective_message
    tgt = await get_target_or_reply(update, context)
    if tgt:
        uid, name = tgt
    else:
        # self check requires reply or sender
        if msg.reply_to_message and msg.reply_to_message.from_user:
            uid = msg.reply_to_message.from_user.id
            name = msg.reply_to_message.from_user.full_name
        else:
            uid = update.effective_user.id
            name = update.effective_user.full_name
    c = await db.get_warns(chat.id, uid)
    await msg.reply_html(f"📋 {mention_html(uid, name)} has {c}/{WARN_LIMIT} warnings.")

# ──────────────────────────────────────────
# Rules & Notes
# ──────────────────────────────────────────
async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    content = await db.get_rules(chat.id)
    if not content:
        await update.effective_message.reply_text("No rules set. Admins can set with /setrules.")
        return
    await update.effective_message.reply_html(f"📜 <b>Rules for {esc(chat.title or str(chat.id))}</b>:\n\n{content}")

@admin_required
async def cmd_setrules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    text = ""
    if context.args:
        text = " ".join(context.args)
    elif msg.reply_to_message and msg.reply_to_message.text:
        text = msg.reply_to_message.text
    if not text.strip():
        await msg.reply_text("Usage: <code>/setrules Your rules text</code> or reply to a message with /setrules", parse_mode=ParseMode.HTML)
        return
    # allow HTML
    await db.set_rules(chat.id, text)
    await msg.reply_text("✅ Rules updated.")

# Notes: #note trigger + commands
@admin_required
async def cmd_setnote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    if not context.args:
        await msg.reply_text("Usage: <code>/setnote &lt;name&gt; &lt;text&gt;</code> or reply with <code>/setnote &lt;name&gt;</code>", parse_mode=ParseMode.HTML)
        return
    name = context.args[0]
    if msg.reply_to_message and (msg.reply_to_message.text or msg.reply_to_message.caption):
        content = msg.reply_to_message.text or msg.reply_to_message.caption
    else:
        if len(context.args) < 2:
            await msg.reply_text("Provide note content or reply to a message.")
            return
        content = " ".join(context.args[1:])
    await db.set_note(chat.id, name, content, update.effective_user.id)
    await msg.reply_html(f"💾 Saved note <code>#{esc(name.lower())}</code>")

# alias /note uses same logic
cmd_note_alias = cmd_setnote

async def cmd_getnote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    if not context.args:
        notes = await db.list_notes(chat.id)
        if not notes:
            await msg.reply_text("No notes saved. Use /setnote <name> <text>")
            return
        await msg.reply_html("🗒 <b>Saved notes:</b>\n" + "\n".join(f"• <code>#{esc(n)}</code>" for n in notes))
        return
    name = context.args[0].lstrip("#")
    content = await db.get_note(chat.id, name)
    if not content:
        await msg.reply_html(f"Note <code>#{esc(name)}</code> not found.")
        return
    await msg.reply_html(content)

@admin_required
async def cmd_delnote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    if not context.args:
        await msg.reply_text("Usage: /delnote <name>")
        return
    name = context.args[0].lstrip("#")
    cnt = await db.del_note(chat.id, name)
    if cnt:
        await msg.reply_html(f"🗑 Deleted note <code>#{esc(name)}</code>")
    else:
        await msg.reply_html(f"Note <code>#{esc(name)}</code> not found.")

# hashtag auto-trigger for notes: messages like #mynote
async def hashtag_note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return
    # only single #word messages (or starting with #)
    m = re.match(r"^#(\w+)\b", msg.text.strip())
    if not m:
        return
    chat = update.effective_chat
    content = await db.get_note(chat.id, m.group(1))
    if content:
        await msg.reply_html(content)

# ──────────────────────────────────────────
# Trust system
# ──────────────────────────────────────────
@admin_required
async def cmd_trust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    tgt = await get_target_or_reply(update, context)
    if not tgt:
        await msg.reply_text("Reply to a user or use /trust @username|ID")
        return
    uid, name = tgt
    await db.trust(chat.id, uid, update.effective_user.id)
    await msg.reply_html(f"✅ Trusted {mention_html(uid, name)} — bypasses link/spam filters.")

@admin_required
async def cmd_untrust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    tgt = await get_target_or_reply(update, context)
    if not tgt:
        await msg.reply_text("Reply to a user or use /untrust @username|ID")
        return
    uid, name = tgt
    await db.untrust(chat.id, uid)
    await msg.reply_html(f"❌ Untrusted {mention_html(uid, name)}")

# ──────────────────────────────────────────
# Global Banned-Word Vault (Owner-only)
# ──────────────────────────────────────────
@owner_required
async def cmd_addbanword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: /addbword <word_or_phrase>\nOwner-only. Applies instantly across all groups.")
        return
    phrase = " ".join(context.args).strip()
    await db.add_global_word(phrase, update.effective_user.id)
    await update.effective_message.reply_html(f"🔒 Added to global vault: <code>{esc(phrase.lower())}</code>")

@owner_required
async def cmd_rmbanword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: /rmbword <word_or_phrase>")
        return
    phrase = " ".join(context.args).strip()
    cnt = await db.remove_global_word(phrase)
    if cnt:
        await update.effective_message.reply_html(f"🔓 Removed: <code>{esc(phrase.lower())}</code>")
    else:
        await update.effective_message.reply_text("Word not found in vault.")

@owner_required
async def cmd_listbanwords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    words = await db.list_global_words()
    if not words:
        await update.effective_message.reply_text("Global vault is empty.")
        return
    txt = "\n".join(f"• <code>{esc(w)}</code>" for w in words[:100])
    if len(words) > 100:
        txt += f"\n… and {len(words)-100} more"
    await update.effective_message.reply_html(f"🌐 <b>Global Banned Vault ({len(words)}):</b>\n{txt}")

# ──────────────────────────────────────────
# Security Locks, Anti-Link/Spam + OCR Pipeline
# ──────────────────────────────────────────
async def check_bypass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Trusted/admins bypass filters."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return True
    if await is_owner_user(user.id):
        return True
    if await is_admin(update, context, user.id):
        return True
    if await db.is_trusted(chat.id, user.id):
        return True
    return False

async def handle_security_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group 0: link/spam/banned-word guard for TEXT messages."""
    msg = update.effective_message
    if not msg or not update.effective_chat or update.effective_chat.type == "private":
        return
    # Ignore bots/commands that are not plain text triggers
    if msg.text and msg.text.startswith("/"):
        # still check banned words in commands? no
        return
    if await check_bypass(update, context):
        return
    chat_id = update.effective_chat.id
    settings = await db.get_settings(chat_id)
    text = msg.text or msg.caption or ""
    if not text:
        return

    # 1) Global banned-word vault (instant sync)
    matched = await db.is_global_banned(text)
    if matched:
        await do_illegal_action(update, context, reason=f"banned word: {matched}")
        return

    # 2) OCR blacklist text scan (also covers illegal text)
    low = text.lower()
    for bad in OCR_BLACKLIST:
        if bad.lower() in low:
            await do_illegal_action(update, context, reason=f"blacklist: {bad}")
            return

    # 3) Anti-link
    if settings.get("antilink") and LINK_RE.search(text):
        try:
            await msg.delete()
        except Exception:
            pass
        await send_temp_notice(context, chat_id, f"🔗 Links are not allowed here, {esc(update.effective_user.full_name)}.")
        return

    # 4) Anti-spam (repetitive / flood)
    if settings.get("antispam") and SPAM_RE.search(text):
        try:
            await msg.delete()
        except Exception:
            pass
        await send_temp_notice(context, chat_id, f"🚫 Spam detected and removed.")
        return

async def do_illegal_action(update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str = ""):
    """Delete offending message + send illegal notice then auto-delete notice."""
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    log.warning("ILLEGAL trigger in %s from %s: %s | %s", chat.id, update.effective_user.id if update.effective_user else "?", reason, (msg.text or msg.caption or "[media]")[:120])
    try:
        await msg.delete()
    except (BadRequest, Forbidden):
        # fallback: try via API
        try:
            await context.bot.delete_message(chat.id, msg.message_id)
        except Exception:
            pass
    except Exception:
        pass
    # temporary notice (must read exactly: "I found something illegal so I deleted it")
    await send_temp_notice(context, chat.id, ILLEGAL_NOTICE, ttl=ILLEGAL_NOTICE_TTL)

# — OCR pipeline for photos/documents —
async def ocr_inspect_image_bytes(image_bytes: bytes) -> str:
    """Run OCR in thread; return extracted text (lowercased). Graceful fallback if deps missing."""
    if not PIL_AVAILABLE or not TESS_AVAILABLE:
        return ""
    def _ocr() -> str:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            # preprocess: convert to grayscale for better OCR
            if img.mode != "RGB":
                img = img.convert("RGB")
            # optional resize: limit max dimension to 2000 to avoid huge images
            max_dim = 2000
            if max(img.size) > max_dim:
                ratio = max_dim / max(img.size)
                new_size = (int(img.size[0]*ratio), int(img.size[1]*ratio))
                # Pillow >=10 uses Resampling
                try:
                    img = img.resize(new_size, Image.Resampling.LANCZOS)  # type: ignore
                except AttributeError:
                    img = img.resize(new_size, Image.LANCZOS)  # type: ignore
            text = pytesseract.image_to_string(img)  # type: ignore
            return text or ""
        except Exception as e:
            log.debug("OCR error: %s", e)
            return ""
    return await asyncio.to_thread(_ocr)

async def handle_photo_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group 0: OCR scan for photos & image documents."""
    msg = update.effective_message
    if not msg or not update.effective_chat or update.effective_chat.type == "private":
        return
    if await check_bypass(update, context):
        return
    chat_id = update.effective_chat.id
    settings = await db.get_settings(chat_id)
    if not settings.get("ocr_protection"):
        return

    file_obj = None
    # photo
    if msg.photo:
        file_obj = msg.photo[-1].get_file  # will call later
        try:
            tg_file = await context.bot.get_file(msg.photo[-1].file_id)
        except Exception:
            return
        # file size guard: skip >20MB
        if tg_file.file_size and tg_file.file_size > 20 * 1024 * 1024:
            return
        try:
            bio = io.BytesIO()
            await tg_file.download_to_memory(bio)
            data = bio.getvalue()
        except Exception as e:
            log.debug("download failed: %s", e)
            return
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        try:
            tg_file = await context.bot.get_file(msg.document.file_id)
        except Exception:
            return
        if tg_file.file_size and tg_file.file_size > 20 * 1024 * 1024:
            return
        try:
            bio = io.BytesIO()
            await tg_file.download_to_memory(bio)
            data = bio.getvalue()
        except Exception:
            return
    else:
        return

    # Run OCR
    extracted = await ocr_inspect_image_bytes(data)
    if not extracted.strip():
        return
    low = extracted.lower()
    # Check against global banned vault + blacklist
    matched_global = await db.is_global_banned(low)
    if matched_global:
        await do_illegal_action(update, context, reason=f"OCR global match: {matched_global} | {low[:80]}")
        return
    for bad in OCR_BLACKLIST:
        if bad.lower() in low:
            await do_illegal_action(update, context, reason=f"OCR blacklist: {bad}")
            return
    # Optional: if no deps, we still block obviously named illegal files via filename
    # (handled by text scan already via caption)

# ──────────────────────────────────────────
# Locks admin commands (granular toggles)
# ──────────────────────────────────────────
@admin_required
async def cmd_locks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    s = await db.get_settings(chat.id)
    kb = [
        [
            InlineKeyboardButton(f"{'✅' if s['antilink'] else '❌'} Anti-Link", callback_data="toggle_antilink"),
            InlineKeyboardButton(f"{'✅' if s['antispam'] else '❌'} Anti-Spam", callback_data="toggle_antispam"),
        ],
        [
            InlineKeyboardButton(f"{'✅' if s['ocr_protection'] else '❌'} OCR Shield", callback_data="toggle_ocr_protection"),
            InlineKeyboardButton(f"{'✅' if s['captcha_enabled'] else '❌'} CAPTCHA Gate", callback_data="toggle_captcha_enabled"),
        ],
        [InlineKeyboardButton("Close", callback_data="close_locks")],
    ]
    await update.effective_message.reply_html(
        f"🔐 <b>Protection Panel — {esc(chat.title or '')}</b>\n"
        f"Anti-Link: {'ON' if s['antilink'] else 'OFF'}\n"
        f"Anti-Spam: {'ON' if s['antispam'] else 'OFF'}\n"
        f"OCR Shield: {'ON' if s['ocr_protection'] else 'OFF'}\n"
        f"CAPTCHA Gate: {'ON' if s['captcha_enabled'] else 'OFF'}\n\n"
        "Tap to toggle. Trusted users & admins bypass all locks.",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def cb_toggle_locks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data:
        return
    await q.answer()
    if q.data == "close_locks":
        try:
            await q.message.delete()
        except Exception:
            pass
        return
    if not q.data.startswith("toggle_"):
        return
    key = q.data.replace("toggle_", "")
    if key not in DEFAULT_SETTINGS:
        return
    # admin check
    if not await is_admin(update, context):
        await q.answer("Admins only.", show_alert=True)
        return
    chat_id = update.effective_chat.id  # type: ignore
    cur = await db.get_settings(chat_id)
    new_val = not cur.get(key, True)
    await db.set_setting(chat_id, key, new_val)
    # update keyboard
    s = await db.get_settings(chat_id)
    kb = [
        [
            InlineKeyboardButton(f"{'✅' if s['antilink'] else '❌'} Anti-Link", callback_data="toggle_antilink"),
            InlineKeyboardButton(f"{'✅' if s['antispam'] else '❌'} Anti-Spam", callback_data="toggle_antispam"),
        ],
        [
            InlineKeyboardButton(f"{'✅' if s['ocr_protection'] else '❌'} OCR Shield", callback_data="toggle_ocr_protection"),
            InlineKeyboardButton(f"{'✅' if s['captcha_enabled'] else '❌'} CAPTCHA Gate", callback_data="toggle_captcha_enabled"),
        ],
        [InlineKeyboardButton("Close", callback_data="close_locks")],
    ]
    try:
        await q.edit_message_text(
            f"🔐 <b>Protection Panel</b>\n"
            f"Anti-Link: {'ON' if s['antilink'] else 'OFF'}\n"
            f"Anti-Spam: {'ON' if s['antispam'] else 'OFF'}\n"
            f"OCR Shield: {'ON' if s['ocr_protection'] else 'OFF'}\n"
            f"CAPTCHA Gate: {'ON' if s['captcha_enabled'] else 'OFF'}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )
    except BadRequest:
        pass

# ──────────────────────────────────────────
# Activity tracking & CAPTCHA gate
# ──────────────────────────────────────────
async def track_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group -1: always track active members."""
    if not update.effective_chat or not update.effective_user:
        return
    if update.effective_user.is_bot:
        return
    chat = update.effective_chat
    if chat.type == "private":
        return
    user = update.effective_user
    try:
        await db.upsert_active(chat.id, user.id, user.username, user.full_name)
    except Exception as e:
        log.debug("track_activity failed: %s", e)

async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gate new joiners with inline verification."""
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or chat.type == "private":
        return
    if not msg.new_chat_members:
        return
    settings = await db.get_settings(chat.id)
    if not settings.get("captcha_enabled"):
        return
    bot_id = context.bot.id
    for member in msg.new_chat_members:
        if member.is_bot:
            continue
        # Restrict immediately — cannot send messages/media
        try:
            await context.bot.restrict_chat_member(
                chat.id, member.id,
                permissions=ChatPermissions(can_send_messages=False, can_send_media_messages=False, can_send_other_messages=False, can_add_web_page_previews=False),
            )
        except (BadRequest, Forbidden) as e:
            log.warning("CAPTCHA restrict failed (bot needs admin): %s", e)
            continue

        # Build verify button — callback contains user_id to prevent others verifying for them
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify You Are Human", callback_data=f"captcha_{member.id}")]])
        try:
            sent = await msg.reply_html(
                f"👋 Welcome {mention_html(member.id, member.full_name)}!\n"
                f"Please verify you are human within <b>{CAPTCHA_TIMEOUT//60} min</b> or you will be removed.\n"
                f"<i>Only you can press the button.</i>",
                reply_markup=kb,
            )
        except Exception as e:
            log.debug("captcha send failed: %s", e)
            continue

        # Schedule ban on timeout via JobQueue or asyncio fallback
        job_name = f"captcha_{chat.id}_{member.id}"
        # cancel old if exists
        old = pending_captcha.get((chat.id, member.id))
        if old and hasattr(old, "schedule_removal"):
            try:
                old.schedule_removal()
            except Exception:
                pass

        async def captcha_timeout_job(ctx: ContextTypes.DEFAULT_TYPE):
            # called by JobQueue
            data = ctx.job.data if ctx.job else {}
            c_id = data.get("chat_id", chat.id)
            u_id = data.get("user_id", member.id)
            msg_id = data.get("msg_id", sent.message_id)
            # still restricted? check status
            try:
                m = await ctx.bot.get_chat_member(c_id, u_id)
                # if already unrestricted (verified), do nothing
                # ChatMemberRestricted has can_send_messages False; if verified they have True via unrestrict
                if m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                    # need to check if they were unrestricted after verification — we'd have removed pending
                    # if pending still exists, they didn't verify -> ban
                    if (c_id, u_id) in pending_captcha:
                        if CAPTCHA_BAN_ON_FAIL:
                            try:
                                await ctx.bot.ban_chat_member(c_id, u_id)
                                await ctx.bot.unban_chat_member(c_id, u_id)  # kick, not permanent
                                await ctx.bot.send_message(c_id, f"⏰ {mention_html(u_id, member.full_name)} failed verification and was removed.", parse_mode=ParseMode.HTML)
                            except Exception:
                                pass
                        try:
                            await ctx.bot.delete_message(c_id, msg_id)
                        except Exception:
                            pass
                        pending_captcha.pop((c_id, u_id), None)
                elif m.status == ChatMemberStatus.RESTRICTED:
                    # still restricted -> timeout
                    if (c_id, u_id) in pending_captcha:
                        if CAPTCHA_BAN_ON_FAIL:
                            try:
                                await ctx.bot.ban_chat_member(c_id, u_id)
                                await ctx.bot.unban_chat_member(c_id, u_id)
                                await ctx.bot.send_message(c_id, f"⏰ {mention_html(u_id, member.full_name)} failed verification and was removed.", parse_mode=ParseMode.HTML)
                            except Exception:
                                pass
                        try:
                            await ctx.bot.delete_message(c_id, msg_id)
                        except Exception:
                            pass
                        pending_captcha.pop((c_id, u_id), None)
            except Exception as e:
                log.debug("captcha timeout check failed: %s", e)

        if context.job_queue:
            job = context.job_queue.run_once(
                captcha_timeout_job,
                CAPTCHA_TIMEOUT,
                data={"chat_id": chat.id, "user_id": member.id, "msg_id": sent.message_id},
                name=job_name,
            )
            pending_captcha[(chat.id, member.id)] = job
        else:
            # asyncio fallback
            async def _fallback():
                await asyncio.sleep(CAPTCHA_TIMEOUT)
                # emulate job
                class _FakeJob:
                    data = {"chat_id": chat.id, "user_id": member.id, "msg_id": sent.message_id}
                    job = type("obj", (), {"data": data})()
                class _FakeCtx:
                    bot = context.bot
                    job = _FakeJob.job
                if (chat.id, member.id) in pending_captcha:
                    await captcha_timeout_job(_FakeCtx())  # type: ignore
            task = asyncio.create_task(_fallback())
            pending_captcha[(chat.id, member.id)] = task

async def cb_captcha_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data or not q.data.startswith("captcha_"):
        return
    try:
        target_id = int(q.data.split("_")[1])
    except Exception:
        await q.answer("Invalid.", show_alert=True)
        return
    clicker = update.effective_user
    if not clicker:
        return
    # Only the gated user themselves can verify (strict)
    if clicker.id != target_id:
        await q.answer("This button is not for you. Only the new member can verify.", show_alert=True)
        return
    chat = update.effective_chat
    if not chat:
        return
    # Unrestrict
    try:
        perms = ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)
        await context.bot.restrict_chat_member(chat.id, target_id, permissions=perms)
    except Exception as e:
        await q.answer(f"Failed: {e}", show_alert=True)
        return
    # Cancel timeout job
    key = (chat.id, target_id)
    job = pending_captcha.pop(key, None)
    if job:
        try:
            if hasattr(job, "schedule_removal"):
                job.schedule_removal()
            else:
                job.cancel()  # asyncio task
        except Exception:
            pass
    try:
        await q.edit_message_text(f"✅ Verified — welcome {esc(clicker.full_name)}! You can now chat.")
    except Exception:
        await q.answer("Verified — welcome!", show_alert=False)
    else:
        await q.answer("Verified!")

# ──────────────────────────────────────────
# Throttled Mass Mentions
# ──────────────────────────────────────────
@admin_required
async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tag active members in batches of 5 every 3s."""
    chat = update.effective_chat
    msg = update.effective_message
    if not chat:
        return
    custom_text = " ".join(context.args) if context.args else "Attention everyone!"
    actives = await db.get_active_users(chat.id, limit=500)
    # Filter out bots? DB already excludes bots via tracking
    if not actives:
        # fallback to admin list
        await msg.reply_text("No active user pool yet. Chat a bit first so I can build the pool.")
        return
    # Exclude the command issuer? No, include.
    await msg.reply_text(f"📢 Broadcasting to {len(actives)} active members in batches of {TAG_BATCH_SIZE}…")

    # Pace batches
    for i in range(0, len(actives), TAG_BATCH_SIZE):
        batch = actives[i:i+TAG_BATCH_SIZE]
        mentions = []
        for uid, uname, fname in batch:
            # Prefer username mention if available for client highlights, but use HTML mention for reliability
            mentions.append(mention_html(uid, fname))
        text = f"📣 <b>{esc(custom_text)}</b>\n\n" + " ".join(mentions)
        # Optional payload: if command was reply, include replied message link? just text for now.
        try:
            await context.bot.send_message(chat.id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning("tagall batch failed: %s", e)
            # flood wait handling: PTB handles RetryAfter automatically if using job queue? We'll sleep a bit longer
            await asyncio.sleep(2)
        if i + TAG_BATCH_SIZE < len(actives):
            await asyncio.sleep(TAG_BATCH_DELAY)

@admin_required
async def cmd_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    if not chat:
        return
    custom_text = " ".join(context.args) if context.args else "Admins — please attend!"
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except Exception as e:
        await msg.reply_text(f"Failed to fetch admins: {e}")
        return
    # Filter bots except we keep? skip bots
    human_admins = [a for a in admins if not a.user.is_bot]
    if not human_admins:
        await msg.reply_text("No human admins found.")
        return
    await msg.reply_text(f"👮 Tagging {len(human_admins)} admins…")
    # batch
    admin_users = [(a.user.id, a.user.username, a.user.full_name) for a in human_admins]
    for i in range(0, len(admin_users), TAG_BATCH_SIZE):
        batch = admin_users[i:i+TAG_BATCH_SIZE]
        mentions = [mention_html(uid, fname) for uid, _, fname in batch]
        text = f"👮 <b>{esc(custom_text)}</b>\n\n" + " ".join(mentions)
        try:
            await context.bot.send_message(chat.id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning("admin tag failed: %s", e)
        if i + TAG_BATCH_SIZE < len(admin_users):
            await asyncio.sleep(TAG_BATCH_DELAY)

# ──────────────────────────────────────────
# Shadow-Reply (Anonymous Admin Sending)
# ──────────────────────────────────────────
@admin_required
async def cmd_shadowreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    # payload is all args joined
    payload = " ".join(context.args) if context.args else ""
    # also handle reply mode: if no args but replying, take replied text
    if not payload and msg.reply_to_message and (msg.reply_to_message.text or msg.reply_to_message.caption):
        payload = msg.reply_to_message.text or msg.reply_to_message.caption or ""
    if not payload.strip():
        await msg.reply_text("Usage: <code>/shadowreply Your anonymous message</code>\nI’ll delete your command and repost as the bot.", parse_mode=ParseMode.HTML)
        return
    # Delete trigger instantly
    try:
        await msg.delete()
    except Exception:
        try:
            await context.bot.delete_message(chat.id, msg.message_id)
        except Exception:
            pass
    # Republish as bot identity (clean payload, no trace)
    try:
        await context.bot.send_message(chat.id, payload, parse_mode=ParseMode.HTML)
    except BadRequest:
        # fallback plain
        await context.bot.send_message(chat.id, payload)

# ──────────────────────────────────────────
# Wordle / Jumble Multiplayer Game
# ──────────────────────────────────────────
def score_guess(secret: str, guess: str) -> List[str]:
    """
    Duplicate-aware Wordle scoring.
    🎯 = correct letter & position (green)
    🔀 = correct letter, wrong position (yellow)
    🚫 = absent
    """
    n = len(secret)
    res = ["🚫"] * n
    # count remaining letters in secret after greens
    secret_chars = list(secret.lower())
    guess_chars = list(guess.lower())
    # First pass: greens
    remaining = {}
    for i in range(n):
        if guess_chars[i] == secret_chars[i]:
            res[i] = "🎯"
        else:
            remaining[secret_chars[i]] = remaining.get(secret_chars[i], 0) + 1
    # Second pass: yellows (respect counts)
    for i in range(n):
        if res[i] == "🎯":
            continue
        g = guess_chars[i]
        if remaining.get(g, 0) > 0:
            res[i] = "🔀"
            remaining[g] -= 1
    return res

async def cmd_newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or not msg:
        return
    # Determine length from command: /new, /new4, /new5, /new6
    raw_cmd = ""
    if msg.text:
        raw_cmd = msg.text.split()[0].split("@")[0].lstrip("/").lower()
    length = 5
    if raw_cmd in ("new4", "new_4", "word4"):
        length = 4
    elif raw_cmd in ("new5", "new_5", "word5"):
        length = 5
    elif raw_cmd in ("new6", "new_6", "word6"):
        length = 6
    elif raw_cmd in ("new", "newword", "newgame", "word", "wordle"):
        length = 5
        # allow arg to override
        if context.args and context.args[0].isdigit() and int(context.args[0]) in (4,5,6):
            length = int(context.args[0])
    else:
        # generic handler fallback
        length = 5

    # Pick random word
    pool = WORD_BANK.get(length, WORD_BANK[5])
    word = random.choice(pool)
    await db.start_game(chat.id, word, update.effective_user.id if update.effective_user else 0)
    await msg.reply_html(
        f"🎲 <b>Word Jumble Started!</b> — {length} letters\n"
        f"Guess with plain text ({length} letters). I’ll score: 🎯 correct spot, 🔀 wrong spot, 🚫 not in word.\n"
        f"Commands: <code>/game</code> status, <code>/top</code> leaderboard\n"
        f"<i>Word chosen — begin!</i>"
    )
    log.info("GAME start chat=%s length=%s word=%s by=%s", chat.id, length, word, update.effective_user.id if update.effective_user else "?")

async def cmd_gamestatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    game = await db.get_game(chat.id)  # type: ignore
    if not game:
        await update.effective_message.reply_text("No active game. Start one with /new, /new4, /new5, /new6")
        return
    word, length, starter, attempts = game
    await update.effective_message.reply_html(
        f"🎮 <b>Game Active</b>\nLetters: {length}\nAttempts: {attempts}\nStarter: <code>{starter}</code>\n"
        f"Send a {length}-letter word to guess!"
    )

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db.top_players(limit=10)
    if not rows:
        await update.effective_message.reply_text("No wins yet. Play with /new!")
        return
    lines = ["🏆 <b>Career Wins — Top Players</b>"]
    for idx, (uid, uname, wins) in enumerate(rows, 1):
        display = esc(uname) if uname else f"ID {uid}"
        lines.append(f"{idx}. {display} — {wins} win{'s' if wins!=1 else ''}")
    await update.effective_message.reply_html("\n".join(lines))

async def handle_wordle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Plain text handler — scores guesses if game active and length matches."""
    msg = update.effective_message
    if not msg or not msg.text or not update.effective_chat:
        return
    if update.effective_chat.type == "private":
        return
    text = msg.text.strip()
    # Ignore commands, mentions, hashtags handled elsewhere, very long/short
    if text.startswith("/") or text.startswith("#") or text.startswith("@"):
        return
    # Only consider single-word alphabetic guesses
    if " " in text or not text.isalpha():
        return
    chat_id = update.effective_chat.id
    game = await db.get_game(chat_id)
    if not game:
        return
    word, length, starter, attempts = game
    if len(text) != length:
        return
    # Treat as guess
    guess = text.lower()
    secret = word.lower()
    scoring = score_guess(secret, guess)
    await db.bump_attempts(chat_id)
    # Build reply
    scored_line = " ".join(scoring)
    user = update.effective_user
    mention = mention_html(user.id, user.full_name) if user else esc(guess)
    if guess == secret:
        # win!
        await db.add_win(user.id, user.username if user else None)
        await db.stop_game(chat_id)
        await msg.reply_html(
            f"{scored_line}\n🎉 <b>Correct!</b> {mention} guessed <code>{esc(secret.upper())}</code>!\n"
            f"Start a new round with /new, /new4, /new5, /new6"
        )
        log.info("GAME win chat=%s winner=%s word=%s attempts=%s", chat_id, user.id if user else "?", secret, attempts+1)
    else:
        await db.add_played(user.id, user.username if user else None)
        await msg.reply_html(
            f"{mention} → <code>{esc(guess.upper())}</code>\n{scored_line}\n"
            f"<i>Hint: 🎯 right spot, 🔀 wrong spot, 🚫 not in word</i>"
        )

# ──────────────────────────────────────────
# Utility Commands: /start /help /locks /id
# ──────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "✨ <b>Miss Sona — Group Guardian</b> ✨\n\n"
        "I merge industrial moderation with Miss Rose elegance.\n\n"
        "<b>Moderation:</b> /ban /unban /mute /unmute /warn /warns\n"
        "<b>Content:</b> /rules /setrules /note /setnote /delnote  (use #notename)\n"
        "<b>Trust:</b> /trust /untrust  (bypass filters)\n"
        "<b>Security:</b> /locks — anti-link • anti-spam • OCR Shield • CAPTCHA Gate\n"
        "<b>Global Vault (owner):</b> /addbword /rmbword /listbwords\n"
        "<b>Mentions:</b> /all [text] • /admins [text]  (5 per 3s, flood-safe)\n"
        "<b>Shadow:</b> /shadowreply [message]\n"
        "<b>Games:</b> /new /new4 /new5 /new6 • /game • /top\n\n"
        "Add me as admin with delete + ban permissions for full power."
    )
    await update.effective_message.reply_html(txt)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    lines = [f"👤 You: <code>{user.id}</code> — {esc(user.full_name)}" if user else ""]
    if chat:
        lines.append(f"💬 Chat: <code>{chat.id}</code> — {esc(chat.title or chat.type)}")
    if msg and msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        lines.append(f"↩️ Replied user: <code>{u.id}</code> — {esc(u.full_name)}")
    await msg.reply_html("\n".join(lines))

async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_id(update, context)

# ──────────────────────────────────────────
# Global Error Handler
# ──────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Update %s caused error: %s", update, context.error)
    # Don't spam user unless it's a command interaction
    if isinstance(update, Update) and update.effective_message:
        try:
            # Only notify for non-flood errors during commands
            if "Flood" not in str(context.error):
                pass
        except Exception:
            pass

# ──────────────────────────────────────────
# Application Factory
# ──────────────────────────────────────────
def build_app() -> Application:
    if BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
        log.warning("BOT_TOKEN not set — set env var BOT_TOKEN before starting.")
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()

    # ─ Handler Groups — explicit, robust structure ─
    GROUP_TRACK = -1   # activity pooling (runs first)
    GROUP_SECURITY = 0 # locks / OCR / anti-spam
    GROUP_MAIN = 1     # moderation / captcha / games
    GROUP_FALLBACK = 2 # notes hashtag, plain guesses

    # Activity tracker — runs on every group message (lowest group)
    app.add_handler(MessageHandler(filters.ALL, track_activity), group=GROUP_TRACK)

    # Security pipeline
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_security_message), group=GROUP_SECURITY)
    app.add_handler(MessageHandler(filters.CaptionEntity(filters.ALL), handle_security_message), group=GROUP_SECURITY)
    # OCR: photos + image documents (separate handler)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo_document), group=GROUP_SECURITY)

    # CAPTCHA: new members + verify callback
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members), group=GROUP_MAIN)
    app.add_handler(CallbackQueryHandler(cb_captcha_verify, pattern=r"^captcha_\d+$"), group=GROUP_MAIN)

    # Locks panel
    app.add_handler(CommandHandler("locks", cmd_locks, filters=filters.ChatType.GROUPS), group=GROUP_MAIN)
    app.add_handler(CommandHandler("lock", cmd_locks, filters=filters.ChatType.GROUPS), group=GROUP_MAIN)
    app.add_handler(CallbackQueryHandler(cb_toggle_locks, pattern=r"^(toggle_|close_locks)"), group=GROUP_MAIN)

    # Moderation
    app.add_handler(CommandHandler(["ban","dban","sban"], cmd_ban), group=GROUP_MAIN)
    app.add_handler(CommandHandler("unban", cmd_unban), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["mute","restrict"], cmd_mute), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["unmute","unrestrict"], cmd_unmute), group=GROUP_MAIN)
    app.add_handler(CommandHandler("warn", cmd_warn), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["warns","warnings"], cmd_warns), group=GROUP_MAIN)

    # Rules / Notes
    app.add_handler(CommandHandler("rules", cmd_rules), group=GROUP_MAIN)
    app.add_handler(CommandHandler("setrules", cmd_setrules), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["setnote","savenote","addnote","note"], cmd_setnote), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["getnote","notes"], cmd_getnote), group=GROUP_MAIN)
    app.add_handler(CommandHandler("delnote", cmd_delnote), group=GROUP_MAIN)
    # Also allow /#notename? handled by hashtag handler

    # Trust
    app.add_handler(CommandHandler("trust", cmd_trust), group=GROUP_MAIN)
    app.add_handler(CommandHandler("untrust", cmd_untrust), group=GROUP_MAIN)

    # Global banned-word vault (owner)
    app.add_handler(CommandHandler(["addbword","addbanword","gbanword","addglobalban"], cmd_addbanword), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["rmbword","removebanword","delbword","delbanword","rmglobalban"], cmd_rmbanword), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["listbwords","bannedwords","globalbans","listbans"], cmd_listbanwords), group=GROUP_MAIN)

    # Throttled mass mentions
    app.add_handler(CommandHandler(["all","tagall","everyone","mentionall"], cmd_all), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["admins","admin","tagadmins"], cmd_admins), group=GROUP_MAIN)

    # Shadow reply
    app.add_handler(CommandHandler("shadowreply", cmd_shadowreply), group=GROUP_MAIN)

    # Wordle / Jumble
    app.add_handler(CommandHandler(["new","new4","new5","new6","newword","newgame","word","wordle","word4","word5","word6"], cmd_newgame), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["game","gamestatus","status"], cmd_gamestatus), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["top","leaderboard","lb"], cmd_top), group=GROUP_MAIN)

    # Utility
    app.add_handler(CommandHandler("start", cmd_start), group=GROUP_MAIN)
    app.add_handler(CommandHandler("help", cmd_help), group=GROUP_MAIN)
    app.add_handler(CommandHandler(["id","chatid","myid"], cmd_id), group=GROUP_MAIN)

    # Fallback group — hashtag notes + wordle guesses (must be after commands)
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^#\w+"), hashtag_note_handler), group=GROUP_FALLBACK)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.ALL, handle_wordle_guess), group=GROUP_FALLBACK)

    # Error handler
    app.add_error_handler(error_handler)

    return app

# ──────────────────────────────────────────
# Lifecycle
# ──────────────────────────────────────────
async def post_init(app: Application):
    await db.init()
    me = await app.bot.get_me()
    log.info("Miss Sona online as @%s (id=%s) — WAL SQLite at %s", me.username, me.id, DB_PATH)
    if OWNER_ID:
        log.info("Owner ID: %s", OWNER_ID)
    else:
        log.info("OWNER_ID not set — global vault will be owner-restricted only if configured.")

async def post_shutdown(app: Application):
    log.info("Miss Sona shutting down…")

def main():
    app = build_app()
    app.post_init = post_init  # type: ignore
    app.post_shutdown = post_shutdown  # type: ignore
    # Run with clean HTML, drop pending updates = True for fresh deploys
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
    )

if __name__ == "__main__":
    main()
