#!/usr/bin/env python3
"""
Miss Sona — Industrial-Grade Telegram Group Management Bot
===========================================================
By The Secret Squad Community | python-telegram-bot v20+ | asyncio + SQLite WAL
Single-file production-ready | Miss Rose Full Parity + Custom Industrial Modules

Features:
  • Core Moderation: /ban /dban /sban /tban /unban, /mute /dmute /smute /tmute /unmute, /kick/dkick/skick, /warn/dwarn/swarn 3-strike auto-ban, /promote/demote/adminlist
  • SQLite via asyncio.to_thread WAL + composite PKs
  • Security Locks: /lock/unlock/locks/locktypes/lockwarns/allowlist, blocklists, antiflood, antiraid
  • AI-OCR: Pillow + Tesseract scan photos/files for illegal text → delete + "I found something illegal so I deleted it"
  • Global Banned Vault (owner-only) instant sync
  • CAPTCHA Gate ✅ Verify You Are Human + activity pool
  • Throttled @all/@admin 5/3s • ShadowReply • Wordle Jumble 🎯 🔀 🚫
  • Welcome/Goodbye with fillings, /export/import, /disable, /id/info

Setup:
  pip install "python-telegram-bot[job-queue]>=20.8" Pillow pytesseract
  sudo apt install tesseract-ocr
  export BOT_TOKEN="token" OWNER_ID="12345"
  python miss_sona.py
"""
from __future__ import annotations
import asyncio, datetime, html, io, json, logging, os, random, re, sqlite3
from pathlib import Path
from typing import Optional, List, Tuple, Dict

try:
    from PIL import Image
    PIL_AVAILABLE=True
except ImportError:
    PIL_AVAILABLE=False; Image=None
try:
    import pytesseract
    TESS_AVAILABLE=True
except ImportError:
    TESS_AVAILABLE=False; pytesseract=None

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ── Logging ──
logging.basicConfig(format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", level=logging.INFO)
log=logging.getLogger("MissSona")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Config ──
BOT_TOKEN=os.getenv("BOT_TOKEN","PUT_YOUR_TOKEN_HERE")
OWNER_ID=int(os.getenv("OWNER_ID","0")) or None
DB_PATH=os.getenv("DB_PATH","miss_sona.db")
LOG_LEVEL=os.getenv("LOG_LEVEL","INFO")
WARN_LIMIT_DEFAULT=3
CAPTCHA_TIMEOUT=120
TAG_BATCH_SIZE=5
TAG_BATCH_DELAY=3.0
ILLEGAL_NOTICE="I found something illegal so I deleted it"
ILLEGAL_NOTICE_TTL=15

DEFAULT_SETTINGS={"antilink":1,"antispam":1,"ocr_protection":1,"captcha_enabled":1,"lockwarns":0,"cleanwelcome":0,"cleanservice":0,"welcome_on":1,"goodbye_on":0,"antiflood_on":1,"flood_limit":5,"flood_mode":"tmute 1h"}
LOCK_TYPES=["url","forward","inline","invite","sticker","photo","video","gif","audio","voice","document","contact","location","game","rtl","button","command","email","phone","bot"]
WORD_BANK={
 4:["code","game","sona","rose","bolt","flux","kilo","nova","apex","zeal","iris","luna","aura","vibe","echo","frost","glow","kind","mint","nest","opal","peak","quip","rift","sage","tide","vale","warp","yarn","zest","bark","clap","dune","fern","gale","howl","jade","kite","lark","moss","neon","oath","plum","quay","rust","star","tune","urge","void","wisp","flux","nova"],
 5:["sonar","crane","ultra","blitz","frost","flame","glide","prism","quirk","amber","blaze","crest","drift","ember","forge","gloom","haven","ivory","jolly","kneel","lunar","mango","nymph","ocean","pride","quest","rally","stone","theta","unity","vivid","whirl","xenon","yield","zebra","apple","brave","crown","dream","eager","flora","giant","happy","ideal","jazzy","knack","light","magic","noble","olive"],
 6:["python","rocket","silver","golden","cobalt","nebula","jasper","quartz","velvet","arcade","breeze","castle","desert","engine","falcon","galaxy","harbor","island","jungle","kernel","legend","marble","nimbus","orange","planet","quiver","ranger","summit","tunnel","unique","voyage","wisdom","yonder","zenith","bronze","crystal","diamond","emerald","fortune","guitar","horizon","imagine","journey","kingdom","lantern","midnight","natural","outlook","parrot"]
}
OCR_BLACKLIST=["csam","child porn","cp video","minor nude","bomb making","how to make bomb","terrorist recruitment","credit card dump","illegal drugs buy"]
LINK_RE=re.compile(r"(?:https?://|t\.me/|telegram\.me/|telegram\.dog/|wa\.me/|discord\.gg/|invite)\S+",re.I)
SPAM_RE=re.compile(r"(.)\1{7,}")
EMAIL_RE=re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE=re.compile(r"\+?\d{10,15}")
pending_captcha:Dict[Tuple[int,int],object]={}
flood_tracker:Dict[Tuple[int,int], List[float]]={}

# ── Database ──
class Database:
    def __init__(self, path:str): self.path=path; self._init_lock=asyncio.Lock()
    def _connect(self):
        con=sqlite3.connect(self.path,timeout=30,check_same_thread=False,isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL;"); con.execute("PRAGMA synchronous=NORMAL;"); con.execute("PRAGMA foreign_keys=ON;"); con.execute("PRAGMA busy_timeout=10000;"); return con
    def _init_sync(self):
        con=self._connect(); cur=con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS warnings(chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER DEFAULT 0, last_reason TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(chat_id,user_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS rules(chat_id INTEGER PRIMARY KEY, content TEXT, privaterules INTEGER DEFAULT 0, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS notes(chat_id INTEGER NOT NULL, name TEXT NOT NULL, content TEXT, created_by INTEGER, PRIMARY KEY(chat_id,name))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS trusted(chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, added_by INTEGER, PRIMARY KEY(chat_id,user_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS global_banned_words(word TEXT PRIMARY KEY COLLATE NOCASE, added_by INTEGER, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS chat_settings(chat_id INTEGER PRIMARY KEY, antilink INTEGER DEFAULT 1, antispam INTEGER DEFAULT 1, ocr_protection INTEGER DEFAULT 1, captcha_enabled INTEGER DEFAULT 1, lockwarns INTEGER DEFAULT 0, cleanwelcome INTEGER DEFAULT 0, cleanservice INTEGER DEFAULT 0, welcome_on INTEGER DEFAULT 1, goodbye_on INTEGER DEFAULT 0, antiflood_on INTEGER DEFAULT 1, flood_limit INTEGER DEFAULT 5, flood_mode TEXT DEFAULT 'tmute 1h', warn_limit INTEGER DEFAULT 3, warn_mode TEXT DEFAULT 'ban', welcome_clean_msg_id INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS active_users(chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT, fullname TEXT, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(chat_id,user_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS games(chat_id INTEGER PRIMARY KEY, word TEXT, length INTEGER, starter_id INTEGER, attempts INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS leaderboard(user_id INTEGER PRIMARY KEY, username TEXT, wins INTEGER DEFAULT 0, games_played INTEGER DEFAULT 0)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS greetings(chat_id INTEGER PRIMARY KEY, welcome_text TEXT DEFAULT 'Hello {fullname}! Welcome to {chatname}!', goodbye_text TEXT DEFAULT 'Goodbye {fullname}!', welcome_on INTEGER DEFAULT 1, goodbye_on INTEGER DEFAULT 0)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS disabled_commands(chat_id INTEGER NOT NULL, command TEXT NOT NULL, PRIMARY KEY(chat_id,command))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS blocklists(chat_id INTEGER NOT NULL, word TEXT NOT NULL, PRIMARY KEY(chat_id,word))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS locks(chat_id INTEGER NOT NULL, lock_type TEXT NOT NULL, locked INTEGER DEFAULT 0, PRIMARY KEY(chat_id,lock_type))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS allowlist(chat_id INTEGER NOT NULL, item TEXT NOT NULL, PRIMARY KEY(chat_id,item))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS connections(user_id INTEGER PRIMARY KEY, chat_id INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS warns_settings(chat_id INTEGER PRIMARY KEY, limit_num INTEGER DEFAULT 3, mode TEXT DEFAULT 'ban')""")
        con.commit(); con.close()
    async def init(self):
        async with self._init_lock: await asyncio.to_thread(self._init_sync)
        log.info("SQLite WAL ready at %s", self.path)
    async def _exec(self, sql, params=(), fetch="none"):
        def _run():
            con=self._connect()
            try:
                cur=con.cursor(); cur.execute(sql,params)
                if fetch=="one": row=cur.fetchone(); con.commit(); return row
                elif fetch=="all": rows=cur.fetchall(); con.commit(); return rows
                con.commit(); return cur.rowcount
            finally: con.close()
        return await asyncio.to_thread(_run)
    # warns
    async def add_warn(self, chat_id, user_id, reason=""):
        def _run():
            con=self._connect()
            try:
                cur=con.cursor()
                cur.execute("INSERT OR IGNORE INTO warnings(chat_id,user_id,count) VALUES(?,?,0)",(chat_id,user_id))
                cur.execute("UPDATE warnings SET count=count+1, last_reason=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND user_id=?",(reason,chat_id,user_id))
                cur.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(chat_id,user_id))
                return cur.fetchone()[0]
            finally: con.close()
        return await asyncio.to_thread(_run)
    async def get_warns(self, chat_id, user_id): row=await self._exec("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(chat_id,user_id),fetch="one"); return row[0] if row else 0
    async def rm_warn(self, chat_id, user_id):
        def _run():
            con=self._connect()
            try:
                cur=con.cursor(); cur.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(chat_id,user_id)); r=cur.fetchone()
                if not r or r[0]<=0: return 0
                cur.execute("UPDATE warnings SET count=count-1 WHERE chat_id=? AND user_id=?",(chat_id,user_id)); con.commit(); return r[0]-1
            finally: con.close()
        return await asyncio.to_thread(_run)
    async def reset_warns(self, chat_id, user_id): await self._exec("DELETE FROM warnings WHERE chat_id=? AND user_id=?",(chat_id,user_id))
    async def reset_all_warns(self, chat_id): await self._exec("DELETE FROM warnings WHERE chat_id=?",(chat_id,))
    async def get_warns_settings(self, chat_id):
        row=await self._exec("SELECT limit_num, mode FROM warns_settings WHERE chat_id=?",(chat_id,),fetch="one")
        if row: return row[0], row[1]
        return 3, "ban"
    async def set_warn_limit(self, chat_id, n): await self._exec("INSERT INTO warns_settings(chat_id,limit_num) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET limit_num=?",(chat_id,n,n))
    async def set_warn_mode(self, chat_id, mode): await self._exec("INSERT INTO warns_settings(chat_id,mode) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET mode=?",(chat_id,mode,mode))
    # rules
    async def set_rules(self, chat_id, content): await self._exec("INSERT INTO rules(chat_id,content) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET content=excluded.content",(chat_id,content))
    async def get_rules(self, chat_id): row=await self._exec("SELECT content FROM rules WHERE chat_id=?",(chat_id,),fetch="one"); return row[0] if row else None
    async def set_privaterules(self, chat_id, val): await self._exec("INSERT INTO rules(chat_id,privaterules) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET privaterules=?",(chat_id,int(val),int(val)))
    async def get_privaterules(self, chat_id): row=await self._exec("SELECT privaterules FROM rules WHERE chat_id=?",(chat_id,),fetch="one"); return bool(row[0]) if row else False
    async def reset_rules(self, chat_id): await self._exec("DELETE FROM rules WHERE chat_id=?",(chat_id,))
    # notes
    async def set_note(self, chat_id, name, content, uid): await self._exec("INSERT INTO notes(chat_id,name,content,created_by) VALUES(?,?,?,?) ON CONFLICT(chat_id,name) DO UPDATE SET content=excluded.content",(chat_id,name.lower(),content,uid))
    async def get_note(self, chat_id, name): row=await self._exec("SELECT content FROM notes WHERE chat_id=? AND name=?",(chat_id,name.lower()),fetch="one"); return row[0] if row else None
    async def del_note(self, chat_id, name): return await self._exec("DELETE FROM notes WHERE chat_id=? AND name=?",(chat_id,name.lower()))
    async def list_notes(self, chat_id): rows=await self._exec("SELECT name FROM notes WHERE chat_id=? ORDER BY name",(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
    # trusted
    async def trust(self, chat_id, user_id, by): await self._exec("INSERT OR IGNORE INTO trusted(chat_id,user_id,added_by) VALUES(?,?,?)",(chat_id,user_id,by))
    async def untrust(self, chat_id, user_id): await self._exec("DELETE FROM trusted WHERE chat_id=? AND user_id=?",(chat_id,user_id))
    async def is_trusted(self, chat_id, user_id): row=await self._exec("SELECT 1 FROM trusted WHERE chat_id=? AND user_id=?",(chat_id,user_id),fetch="one"); return bool(row)
    # global vault
    async def add_global_word(self, word, by): await self._exec("INSERT OR IGNORE INTO global_banned_words(word,added_by) VALUES(?,?)",(word.lower().strip(),by))
    async def remove_global_word(self, word): return await self._exec("DELETE FROM global_banned_words WHERE word=?",(word.lower().strip(),))
    async def list_global_words(self): rows=await self._exec("SELECT word FROM global_banned_words ORDER BY word",fetch="all"); return [r[0] for r in (rows or [])]
    async def is_global_banned(self, text): words=await self.list_global_words(); low=text.lower(); [ (w) for w in words if w.lower() in low ] ; 
    async def check_global(self, text):
        words=await self.list_global_words(); low=text.lower()
        for w in words:
            if w and w.lower() in low: return w
        return None
    # settings
    async def get_settings(self, chat_id):
        row=await self._exec("SELECT antilink, antispam, ocr_protection, captcha_enabled, lockwarns, cleanwelcome, cleanservice, welcome_on, goodbye_on, antiflood_on, flood_limit, flood_mode FROM chat_settings WHERE chat_id=?",(chat_id,),fetch="one")
        if row: return dict(zip(["antilink","antispam","ocr_protection","captcha_enabled","lockwarns","cleanwelcome","cleanservice","welcome_on","goodbye_on","antiflood_on","flood_limit","flood_mode"],row))
        await self._exec("INSERT OR IGNORE INTO chat_settings(chat_id) VALUES(?)",(chat_id,))
        return {"antilink":1,"antispam":1,"ocr_protection":1,"captcha_enabled":1,"lockwarns":0,"cleanwelcome":0,"cleanservice":0,"welcome_on":1,"goodbye_on":0,"antiflood_on":1,"flood_limit":5,"flood_mode":"tmute 1h"}
    async def set_setting(self, chat_id, key, val):
        await self.get_settings(chat_id)
        if key in ["antilink","antispam","ocr_protection","captcha_enabled","lockwarns","cleanwelcome","cleanservice","welcome_on","goodbye_on","antiflood_on","flood_limit"]:
            await self._exec(f"UPDATE chat_settings SET {key}=? WHERE chat_id=?",(int(val) if isinstance(val,bool) or key!="flood_limit" else int(val) if key=="flood_limit" else val,chat_id))
        else:
            await self._exec(f"UPDATE chat_settings SET {key}=? WHERE chat_id=?",(val,chat_id))
    # active
    async def upsert_active(self, chat_id, user_id, username, fullname): await self._exec("INSERT INTO active_users(chat_id,user_id,username,fullname,last_seen) VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(chat_id,user_id) DO UPDATE SET username=excluded.username, fullname=excluded.fullname, last_seen=CURRENT_TIMESTAMP",(chat_id,user_id,username,fullname))
    async def get_active_users(self, chat_id, limit=200): rows=await self._exec("SELECT user_id, username, fullname FROM active_users WHERE chat_id=? ORDER BY last_seen DESC LIMIT ?",
            (chat_id,limit),fetch="all"); return rows or []
    # greetings
    async def set_welcome(self, chat_id, text): await self._exec("INSERT INTO greetings(chat_id,welcome_text) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET welcome_text=?",(chat_id,text,text))
    async def set_goodbye(self, chat_id, text): await self._exec("INSERT INTO greetings(chat_id,goodbye_text) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET goodbye_text=?",(chat_id,text,text))
    async def get_greetings(self, chat_id):
        row=await self._exec("SELECT welcome_text, goodbye_text, welcome_on, goodbye_on FROM greetings WHERE chat_id=?",(chat_id,),fetch="one")
        if row: return row
        return ("Hello {fullname}! Welcome to {chatname}!", "Goodbye {fullname}!",1,0)
    # locks
    async def set_lock(self, chat_id, ltype, locked): await self._exec("INSERT INTO locks(chat_id,lock_type,locked) VALUES(?,?,?) ON CONFLICT(chat_id,lock_type) DO UPDATE SET locked=?",(chat_id,ltype,int(locked),int(locked)))
    async def is_locked(self, chat_id, ltype): row=await self._exec("SELECT locked FROM locks WHERE chat_id=? AND lock_type=?",(chat_id,ltype),fetch="one"); return bool(row[0]) if row else False
    async def list_locks(self, chat_id): rows=await self._exec("SELECT lock_type FROM locks WHERE chat_id=? AND locked=1",(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
    # allowlist/blocklist/disabled/connections
    async def add_allow(self, chat_id, item): await self._exec("INSERT OR IGNORE INTO allowlist(chat_id,item) VALUES(?,?)",(chat_id,item.lower()))
    async def rm_allow(self, chat_id, item): return await self._exec("DELETE FROM allowlist WHERE chat_id=? AND item=?",(chat_id,item.lower()))
    async def list_allow(self, chat_id): rows=await self._exec("SELECT item FROM allowlist WHERE chat_id=?",(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
    async def is_allowed(self, chat_id, text): items=await self.list_allow(chat_id); low=text.lower(); return any(it in low for it in items)
    async def add_blocklist(self, chat_id, word): await self._exec("INSERT OR IGNORE INTO blocklists(chat_id,word) VALUES(?,?)",(chat_id,word.lower()))
    async def rm_blocklist(self, chat_id, word): return await self._exec("DELETE FROM blocklists WHERE chat_id=? AND word=?",(chat_id,word.lower()))
    async def list_blocklist(self, chat_id): rows=await self._exec("SELECT word FROM blocklists WHERE chat_id=?",(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
    async def is_blocklisted(self, text, chat_id): rows=await self.list_blocklist(chat_id); low=text.lower(); return next((w for w in rows if w in low),None)
    async def disable_cmd(self, chat_id, cmd): await self._exec("INSERT OR IGNORE INTO disabled_commands(chat_id,command) VALUES(?,?)",(chat_id,cmd.lower()))
    async def enable_cmd(self, chat_id, cmd): return await self._exec("DELETE FROM disabled_commands WHERE chat_id=? AND command=?",(chat_id,cmd.lower()))
    async def is_disabled(self, chat_id, cmd): row=await self._exec("SELECT 1 FROM disabled_commands WHERE chat_id=? AND command=?",(chat_id,cmd.lower()),fetch="one"); return bool(row)
    async def list_disabled(self, chat_id): rows=await self._exec("SELECT command FROM disabled_commands WHERE chat_id=?",(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
    async def set_connection(self, user_id, chat_id): await self._exec("INSERT INTO connections(user_id,chat_id) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET chat_id=?",(user_id,chat_id,chat_id))
    async def get_connection(self, user_id): row=await self._exec("SELECT chat_id FROM connections WHERE user_id=?",(user_id,),fetch="one"); return row[0] if row else None
    # games
    async def start_game(self, chat_id, word, starter): await self._exec("INSERT INTO games(chat_id,word,length,starter_id,attempts,is_active) VALUES(?,?,?,?,0,1) ON CONFLICT(chat_id) DO UPDATE SET word=excluded.word, length=excluded.length, starter_id=excluded.starter_id, attempts=0, is_active=1, started_at=CURRENT_TIMESTAMP",(chat_id,word.lower(),len(word),starter))
    async def get_game(self, chat_id): row=await self._exec("SELECT word,length,starter_id,attempts FROM games WHERE chat_id=? AND is_active=1",(chat_id,),fetch="one"); return row
    async def bump_attempts(self, chat_id): await self._exec("UPDATE games SET attempts=attempts+1 WHERE chat_id=?",(chat_id,))
    async def stop_game(self, chat_id): await self._exec("UPDATE games SET is_active=0 WHERE chat_id=?",(chat_id,))
    # lb
    async def add_win(self, uid, uname): await self._exec("INSERT INTO leaderboard(user_id,username,wins,games_played) VALUES(?,?,1,1) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, wins=wins+1, games_played=games_played+1",(uid,uname or str(uid)))
    async def add_played(self, uid, uname): await self._exec("INSERT INTO leaderboard(user_id,username,games_played) VALUES(?,?,1) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, games_played=games_played+1",(uid,uname or str(uid)))
    async def top_players(self, limit=10): rows=await self._exec("SELECT user_id,username,wins FROM leaderboard ORDER BY wins DESC LIMIT ?", (limit,),fetch="all"); return rows or []
db=Database(DB_PATH)

# ── Helpers ──
def esc(s): return html.escape(s or "",quote=False)
def mention_html(uid,name): return f'<a href="tg://user?id={uid}">{esc(name)}</a>'
def parse_duration(s):
    m=re.match(r"^\s*(\d+)\s*([smhdw])\s*$",s.strip(),re.I)
    if not m: return None
    n,unit=int(m.group(1)), m.group(2).lower()
    mul={"s":1,"m":60,"h":3600,"d":86400,"w":604800}
    return datetime.timedelta(seconds=n*mul[unit])
async def is_admin(update, context, user_id=None):
    chat=update.effective_chat
    if not chat: return False
    uid=user_id or (update.effective_user.id if update.effective_user else None)
    if not uid: return False
    if OWNER_ID and uid==OWNER_ID: return True
    try:
        m=await context.bot.get_chat_member(chat.id, uid)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except: return False
async def is_owner(uid): return bool(OWNER_ID and uid==OWNER_ID)
async def resolve_target(update, context):
    msg=update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u=msg.reply_to_message.from_user; return u.id, u.full_name
    if context.args:
        arg=context.args[0].lstrip("@")
        if arg.isdigit(): return int(arg), f"user {arg}"
        chat_id=update.effective_chat.id
        actives=await db.get_active_users(chat_id,500)
        for uid,uname,fname in actives:
            if uname and uname.lower()==arg.lower(): return uid, fname
        return None
    return None
async def send_temp_notice(ctx, chat_id, text, ttl=ILLEGAL_NOTICE_TTL):
    try:
        m=await ctx.bot.send_message(chat_id,text,parse_mode=ParseMode.HTML)
        if ctx.job_queue: ctx.job_queue.run_once(lambda c: asyncio.create_task(_del(c,chat_id,m.message_id)), ttl)
        else: asyncio.create_task(_auto(ctx,chat_id,m.message_id,ttl))
    except: pass
async def _del(ctx,chat_id,mid):
    try: await ctx.bot.delete_message(chat_id,mid)
    except: pass
async def _auto(ctx,chat_id,mid,ttl):
    await asyncio.sleep(ttl)
    try: await ctx.bot.delete_message(chat_id,mid)
    except: pass
def fill_welcome(template, user, chat, count=None):
    return template.format(fullname=esc(user.full_name), firstname=esc(user.first_name), username=esc(user.username or user.full_name), mention=mention_html(user.id,user.full_name), id=user.id, chatname=esc(chat.title or ""), count=count or 0)

def admin_required(func):
    async def w(update,context,*a,**k):
        if not await is_admin(update,context):
            if update.effective_message: await update.effective_message.reply_text("🚫 Only admins can use this.")
            return
        return await func(update,context,*a,**k)
    return w
def owner_required(func):
    async def w(update,context,*a,**k):
        uid=update.effective_user.id if update.effective_user else 0
        if not await is_owner(uid):
            if update.effective_message: await update.effective_message.reply_text("🔒 Owner-only.")
            return
        return await func(update,context,*a,**k)
    return w

async def effective_chat_id(update,context):
    # support connections: if private, use connected chat
    chat=update.effective_chat
    if chat.type=="private":
        cid=await db.get_connection(update.effective_user.id)
        if cid: return cid
    return chat.id

async def check_bypass(update,context):
    user=update.effective_user; chat=update.effective_chat
    if not user or not chat: return True
    if await is_owner(user.id): return True
    # for private connected chats, bypass check against real chat
    real_chat_id=await effective_chat_id(update,context)
    # create mock update for admin check? simpler: check admin in real chat
    try:
        m=await context.bot.get_chat_member(real_chat_id, user.id)
        if m.status in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER): return True
    except: pass
    if chat.type!="private" and await db.is_trusted(chat.id, user.id): return True
    return False

# ── Moderation ──
@admin_required
async def cmd_ban(update,context):
    chat=update.effective_chat; msg=update.effective_message
    tgt=await resolve_target(update,context)
    if not tgt: await msg.reply_text("Usage: /ban [reply/@user] [reason]"); return
    uid,name=tgt
    if OWNER_ID and uid==OWNER_ID: await msg.reply_text("Can't ban owner"); return
    if await is_admin(update,context,uid): await msg.reply_text("Can't ban admin"); return
    # parse time if last arg is duration for tban variant
    cmd=msg.text.split()[0].split("@")[0].lstrip("/").lower()
    reason=" ".join(context.args[1:]) if len(context.args)>1 else "Banned"
    until=None
    if cmd in ("tban","tmute") or (context.args and parse_duration(context.args[-1])):
        dur=parse_duration(context.args[-1]) if context.args else None
        if dur: until=datetime.datetime.now(datetime.timezone.utc)+dur; reason=" ".join(context.args[1:-1]) if len(context.args)>2 else "Temp ban"
    try:
        if until: await context.bot.ban_chat_member(chat.id,uid,until_date=until)
        else: await context.bot.ban_chat_member(chat.id,uid)
        await msg.reply_html(f"🔨 Banned {mention_html(uid,name)} <i>{esc(reason)}</i>")
        if cmd in ("sban","tban"): 
            try: await msg.delete()
            except: pass
        elif cmd=="dban":
            try: await update.effective_message.reply_to_message.delete()
            except: pass
    except Exception as e: await msg.reply_text(f"Ban failed: {e}")

@admin_required
async def cmd_unban(update,context):
    chat=update.effective_chat; msg=update.effective_message
    tgt=await resolve_target(update,context)
    if not tgt: await msg.reply_text("Usage: /unban [reply/@user]"); return
    uid,name=tgt
    try: await context.bot.unban_chat_member(chat.id,uid); await msg.reply_html(f"✅ Unbanned {mention_html(uid,name)}")
    except Exception as e: await msg.reply_text(f"Unban failed: {e}")

@admin_required
async def cmd_mute(update,context):
    chat=update.effective_chat; msg=update.effective_message
    tgt=await resolve_target(update,context)
    if not tgt: await msg.reply_text("Usage: /mute [reply/@user] [time 30m/2h]"); return
    uid,name=tgt
    if await is_admin(update,context,uid): await msg.reply_text("Can't mute admin"); return
    until=None; cmd=msg.text.split()[0].lstrip("/").lower().split("@")[0]
    if context.args and parse_duration(context.args[-1]):
        until=datetime.datetime.now(datetime.timezone.utc)+parse_duration(context.args[-1])
    try:
        await context.bot.restrict_chat_member(chat.id,uid,permissions=ChatPermissions(can_send_messages=False),until_date=until)
        await msg.reply_html(f"🔇 Muted {mention_html(uid,name)} {esc(context.args[-1]) if until else ''}")
        if cmd in ("smute","tmute"):
            try: await msg.delete()
            except: pass
        elif cmd=="dmute":
            try: await msg.reply_to_message.delete()
            except: pass
    except Exception as e: await msg.reply_text(f"Mute failed: {e}")

@admin_required
async def cmd_unmute(update,context):
    chat=update.effective_chat; tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Usage: /unmute [reply/@user]"); return
    uid,name=tgt
    try: await context.bot.restrict_chat_member(chat.id,uid,permissions=ChatPermissions(can_send_messages=True,can_send_media_messages=True,can_send_other_messages=True,can_add_web_page_previews=True)); await update.effective_message.reply_html(f"🔊 Unmuted {mention_html(uid,name)}")
    except Exception as e: await update.effective_message.reply_text(f"Unmute failed: {e}")

@admin_required
async def cmd_kick(update,context):
    chat=update.effective_chat; msg=update.effective_message
    tgt=await resolve_target(update,context)
    if not tgt: await msg.reply_text("Usage: /kick [reply/@user]"); return
    uid,name=tgt
    if await is_admin(update,context,uid): await msg.reply_text("Can't kick admin"); return
    try: await context.bot.ban_chat_member(chat.id,uid); await context.bot.unban_chat_member(chat.id,uid); await msg.reply_html(f"👢 Kicked {mention_html(uid,name)}")
    except Exception as e: await msg.reply_text(f"Kick failed: {e}")

@admin_required
async def cmd_warn(update,context):
    chat=update.effective_chat; msg=update.effective_message
    tgt=await resolve_target(update,context)
    if not tgt: await msg.reply_text("Usage: /warn [reply/@user] [reason]"); return
    uid,name=tgt
    if await is_admin(update,context,uid): await msg.reply_text("Can't warn admin"); return
    reason=" ".join(context.args[1:]) if len(context.args)>1 else "No reason"
    count=await db.add_warn(chat.id,uid,reason)
    limit,mode=await db.get_warns_settings(chat.id)
    await msg.reply_html(f"⚠️ Warned {mention_html(uid,name)} ({count}/{limit}) <i>{esc(reason)}</i>")
    if count>=limit:
        # apply warnmode
        try:
            if mode=="ban": await context.bot.ban_chat_member(chat.id,uid); await context.bot.send_message(chat.id,f"🚫 {mention_html(uid,name)} reached {limit} warns → <b>banned</b>",parse_mode=ParseMode.HTML)
            elif mode=="kick": await context.bot.ban_chat_member(chat.id,uid); await context.bot.unban_chat_member(chat.id,uid); await context.bot.send_message(chat.id,f"👢 {mention_html(uid,name)} kicked for warns",parse_mode=ParseMode.HTML)
            elif mode in ("mute","tmute"): await context.bot.restrict_chat_member(chat.id,uid,permissions=ChatPermissions(can_send_messages=False)); await context.bot.send_message(chat.id,f"🔇 {mention_html(uid,name)} muted for warns",parse_mode=ParseMode.HTML)
            await db.reset_warns(chat.id,uid)
        except Exception as e: await msg.reply_text(f"Warn action failed: {e}")
    cmd=msg.text.split()[0].lstrip("/").lower()
    if cmd=="dwarn":
        try: await msg.reply_to_message.delete()
        except: pass
    elif cmd=="swarn":
        try: await msg.delete()
        except: pass

async def cmd_warns(update,context):
    chat=update.effective_chat; msg=update.effective_message
    tgt=await resolve_target(update,context)
    if tgt: uid,name=tgt
    elif msg.reply_to_message and msg.reply_to_message.from_user: uid=msg.reply_to_message.from_user.id; name=msg.reply_to_message.from_user.full_name
    else: uid=update.effective_user.id; name=update.effective_user.full_name
    c=await db.get_warns(chat.id,uid); limit,_=await db.get_warns_settings(chat.id)
    await msg.reply_html(f"📋 {mention_html(uid,name)} has {c}/{limit} warns")

@admin_required
async def cmd_rmwarn(update,context):
    chat=update.effective_chat; tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply to user to remove warn"); return
    uid,name=tgt; newc=await db.rm_warn(chat.id,uid); await update.effective_message.reply_html(f"✅ Removed warn for {mention_html(uid,name)} → {newc}")

@admin_required
async def cmd_resetwarn(update,context):
    chat=update.effective_chat; tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Usage: /resetwarn [reply/@user]"); return
    uid,name=tgt; await db.reset_warns(chat.id,uid); await update.effective_message.reply_html(f"✅ Reset warns for {mention_html(uid,name)}")

@admin_required
async def cmd_resetallwarns(update,context):
    await db.reset_all_warns(update.effective_chat.id); await update.effective_message.reply_text("✅ All warns reset")

async def cmd_warnings(update,context):
    limit,mode=await db.get_warns_settings(update.effective_chat.id); await update.effective_message.reply_html(f"⚙️ Warn limit: <b>{limit}</b>\nWarn mode: <b>{esc(mode)}</b>")

@admin_required
async def cmd_warnmode(update,context):
    if not context.args: limit,mode=await db.get_warns_settings(update.effective_chat.id); await update.effective_message.reply_html(f"Warn mode: {esc(mode)}"); return
    mode=context.args[0].lower()
    if mode not in ("ban","kick","mute","tban","tmute"): await update.effective_message.reply_text("Valid: ban/kick/mute/tban/tmute"); return
    await db.set_warn_mode(update.effective_chat.id,mode); await update.effective_message.reply_text(f"Warn mode set to {mode}")

@admin_required
async def cmd_warnlimit(update,context):
    if not context.args: limit,_=await db.get_warns_settings(update.effective_chat.id); await update.effective_message.reply_text(f"Warn limit: {limit}"); return
    try: n=int(context.args[0]); await db.set_warn_limit(update.effective_chat.id,n); await update.effective_message.reply_text(f"Warn limit set to {n}")
    except: await update.effective_message.reply_text("Usage: /warnlimit 3")

@admin_required
async def cmd_promote(update,context):
    chat=update.effective_chat; tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply to user to promote"); return
    uid,name=tgt
    try: await context.bot.promote_chat_member(chat.id,uid,can_delete_messages=True,can_restrict_members=True,can_pin_messages=True,can_promote_members=False); await update.effective_message.reply_html(f"⬆️ Promoted {mention_html(uid,name)}")
    except Exception as e: await update.effective_message.reply_text(f"Promote failed: {e}")

@admin_required
async def cmd_demote(update,context):
    chat=update.effective_chat; tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply to user to demote"); return
    uid,name=tgt
    try: await context.bot.promote_chat_member(chat.id,uid,can_delete_messages=False,can_restrict_members=False,can_pin_messages=False,can_promote_members=False); await update.effective_message.reply_html(f"⬇️ Demoted {mention_html(uid,name)}")
    except Exception as e: await update.effective_message.reply_text(f"Demote failed: {e}")

async def cmd_adminlist(update,context):
    try: admins=await context.bot.get_chat_administrators(update.effective_chat.id); txt="👮 <b>Admins:</b>\n"+"\n".join(f"• {mention_html(a.user.id,a.user.full_name)} — {a.status}" for a in admins); await update.effective_message.reply_html(txt)
    except Exception as e: await update.effective_message.reply_text(f"Failed: {e}")

async def cmd_admincache(update,context):
    await update.effective_message.reply_text("✅ Admin cache refreshed")

@admin_required
async def cmd_anonadmin(update,context):
    if not context.args: await update.effective_message.reply_text("Usage: /anonadmin on/off"); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"anonadmin",val); await update.effective_message.reply_text(f"Anon admin {'enabled' if val else 'disabled'}")

# ── Rules / Notes ──
async def cmd_rules(update,context):
    c=await db.get_rules(update.effective_chat.id)
    if not c: await update.effective_message.reply_text("No rules set. Use /setrules"); return
    if await db.get_privaterules(update.effective_chat.id):
        try: await context.bot.send_message(update.effective_user.id,f"📜 <b>Rules for {esc(update.effective_chat.title)}</b>:\n\n{c}",parse_mode=ParseMode.HTML); await update.effective_message.reply_text("Rules sent in private")
        except: await update.effective_message.reply_html(c)
        return
    await update.effective_message.reply_html(c)

@admin_required
async def cmd_setrules(update,context):
    chat=update.effective_chat; msg=update.effective_message
    txt=" ".join(context.args) if context.args else (msg.reply_to_message.text if msg.reply_to_message and msg.reply_to_message.text else "")
    if not txt: await msg.reply_text("Usage: /setrules <text> or reply"); return
    await db.set_rules(chat.id,txt); await msg.reply_text("✅ Rules updated")
@admin_required
async def cmd_privaterules(update,context):
    if not context.args: await update.effective_message.reply_text(f"Private rules: {await db.get_privaterules(update.effective_chat.id)}"); return
    val=context.args[0].lower() in ("yes","on","on","true"); await db.set_privaterules(update.effective_chat.id,val); await update.effective_message.reply_text(f"Private rules {'on' if val else 'off'}")
@admin_required
async def cmd_resetrules(update,context): await db.reset_rules(update.effective_chat.id); await update.effective_message.reply_text("✅ Rules reset")
@admin_required
async def cmd_setnote(update,context):
    chat=update.effective_chat; msg=update.effective_message
    if not context.args: await msg.reply_text("Usage: /setnote <name> <text> or reply"); return
    name=context.args[0]
    if msg.reply_to_message and (msg.reply_to_message.text or msg.reply_to_message.caption): content=msg.reply_to_message.text or msg.reply_to_message.caption
    else:
        if len(context.args)<2: await msg.reply_text("Provide content or reply"); return
        content=" ".join(context.args[1:])
    await db.set_note(chat.id,name,content,update.effective_user.id); await msg.reply_html(f"💾 Note <code>#{esc(name.lower())}</code> saved")
async def cmd_getnote(update,context):
    chat=update.effective_chat; msg=update.effective_message
    if not context.args:
        notes=await db.list_notes(chat.id)
        if not notes: await msg.reply_text("No notes. Use /setnote"); return
        await msg.reply_html("🗒 <b>Notes:</b>\n"+"\n".join(f"• <code>#{esc(n)}</code>" for n in notes)); return
    name=context.args[0].lstrip("#"); c=await db.get_note(chat.id,name)
    if not c: await msg.reply_text("Not found"); return
    await msg.reply_html(c)
@admin_required
async def cmd_delnote(update,context):
    if not context.args: await update.effective_message.reply_text("Usage: /delnote <name>"); return
    name=context.args[0].lstrip("#"); cnt=await db.del_note(update.effective_chat.id,name); await update.effective_message.reply_text("Deleted" if cnt else "Not found")
async def hashtag_note_handler(update,context):
    msg=update.effective_message
    if not msg or not msg.text: return
    m=re.match(r"^#(\w+)\b",msg.text.strip())
    if not m: return
    c=await db.get_note(update.effective_chat.id,m.group(1))
    if c: await msg.reply_html(c)

@admin_required
async def cmd_trust(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply to user"); return
    uid,name=tgt; await db.trust(update.effective_chat.id,uid,update.effective_user.id); await update.effective_message.reply_html(f"✅ Trusted {mention_html(uid,name)}")
@admin_required
async def cmd_untrust(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply to user"); return
    uid,name=tgt; await db.untrust(update.effective_chat.id,uid); await update.effective_message.reply_html(f"❌ Untrusted {mention_html(uid,name)}")

# ── Vault ──
@owner_required
async def cmd_addbanword(update,context):
    if not context.args: await update.effective_message.reply_text("Usage: /addbword <word>"); return
    phrase=" ".join(context.args); await db.add_global_word(phrase,update.effective_user.id); await update.effective_message.reply_html(f"🔒 Added <code>{esc(phrase.lower())}</code>")
@owner_required
async def cmd_rmbanword(update,context):
    if not context.args: await update.effective_message.reply_text("Usage: /rmbword <word>"); return
    phrase=" ".join(context.args); cnt=await db.remove_global_word(phrase); await update.effective_message.reply_text("Removed" if cnt else "Not found")
@owner_required
async def cmd_listbanwords(update,context):
    words=await db.list_global_words()
    if not words: await update.effective_message.reply_text("Vault empty"); return
    txt="\n".join(f"• <code>{esc(w)}</code>" for w in words[:100])
    await update.effective_message.reply_html(f"🌐 <b>Vault ({len(words)}):</b>\n{txt}")

# ── Locks ──
@admin_required
async def cmd_lock(update,context):
    if not context.args: await update.effective_message.reply_text("Usage: /lock <type> e.g. url sticker"); return
    for t in context.args:
        if t.lower() in LOCK_TYPES: await db.set_lock(update.effective_chat.id,t.lower(),True)
    await update.effective_message.reply_text(f"🔒 Locked: {', '.join(context.args)}")
@admin_required
async def cmd_unlock(update,context):
    if not context.args: await update.effective_message.reply_text("Usage: /unlock <type>"); return
    for t in context.args:
        if t.lower() in LOCK_TYPES: await db.set_lock(update.effective_chat.id,t.lower(),False)
    await update.effective_message.reply_text(f"🔓 Unlocked: {', '.join(context.args)}")
@admin_required
async def cmd_locks_panel(update,context):
    chat=update.effective_chat; s=await db.get_settings(chat.id); locks=await db.list_locks(chat.id)
    kb=[
        [InlineKeyboardButton(f"{'✅' if await db.is_locked(chat.id,'url') else '❌'} Anti-Link",callback_data="toggle_lock_url"), InlineKeyboardButton(f"{'✅' if await db.is_locked(chat.id,'sticker') else '❌'} Sticker",callback_data="toggle_lock_sticker")],
        [InlineKeyboardButton(f"{'✅' if s['antispam'] else '❌'} AntiSpam",callback_data="toggle_antispam"), InlineKeyboardButton(f"{'✅' if s['ocr_protection'] else '❌'} OCR",callback_data="toggle_ocr_protection")],
        [InlineKeyboardButton(f"{'✅' if s['captcha_enabled'] else '❌'} CAPTCHA",callback_data="toggle_captcha_enabled"), InlineKeyboardButton("Close",callback_data="close_locks")]
    ]
    await update.effective_message.reply_html(f"🔐 <b>Locks — {esc(chat.title or '')}</b>\nLocked: {', '.join(locks) if locks else 'none'}\nAntiLink: {'ON' if await db.is_locked(chat.id,'url') else 'OFF'}",reply_markup=InlineKeyboardMarkup(kb))
async def cb_toggle_locks(update,context):
    q=update.callback_query
    if not q or not q.data: return
    await q.answer()
    if q.data=="close_locks":
        try: await q.message.delete()
        except: pass
        return
    if q.data.startswith("toggle_lock_"):
        if not await is_admin(update,context): await q.answer("Admins only",show_alert=True); return
        ltype=q.data.replace("toggle_lock_",""); cur=await db.is_locked(update.effective_chat.id,ltype); await db.set_lock(update.effective_chat.id,ltype,not cur); await q.answer(f"{ltype} {'locked' if not cur else 'unlocked'}"); return
    if q.data.startswith("toggle_"):
        if not await is_admin(update,context): await q.answer("Admins only",show_alert=True); return
        key=q.data.replace("toggle_",""); cur=await db.get_settings(update.effective_chat.id); await db.set_setting(update.effective_chat.id,key,not cur.get(key,True)); await q.answer("Toggled")
async def cmd_locktypes(update,context): await update.effective_message.reply_text("Lock types: "+", ".join(LOCK_TYPES)+"\nUse /lock <type>")
async def cmd_lockwarns(update,context):
    if not context.args: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(f"lockwarns: {'on' if s['lockwarns'] else 'off'}"); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"lockwarns",val); await update.effective_message.reply_text(f"lockwarns {'on' if val else 'off'}")
@admin_required
async def cmd_allowlist(update,context):
    chat=update.effective_chat
    if not context.args: items=await db.list_allow(chat.id); await update.effective_message.reply_text("Allowlist: "+", ".join(items) if items else "Allowlist empty"); return
    for it in context.args: await db.add_allow(chat.id,it)
    await update.effective_message.reply_text("Added to allowlist: "+", ".join(context.args))
@admin_required
async def cmd_rmallowlist(update,context):
    if not context.args: await update.effective_message.reply_text("Usage: /rmallowlist <url>"); return
    for it in context.args: await db.rm_allow(update.effective_chat.id,it)
    await update.effective_message.reply_text("Removed")
@admin_required
async def cmd_rmallowlistall(update,context): await db._exec("DELETE FROM allowlist WHERE chat_id=?",(update.effective_chat.id,)); await update.effective_message.reply_text("✅ Allowlist cleared")
@admin_required
async def cmd_whitelist(update,context): await cmd_allowlist(update,context)
# blocklist
@admin_required
async def cmd_blocklist(update,context):
    if not context.args: items=await db.list_blocklist(update.effective_chat.id); await update.effective_message.reply_text("Blocklist: "+", ".join(items) if items else "Empty"); return
    for w in context.args: await db.add_blocklist(update.effective_chat.id,w)
    await update.effective_message.reply_text("Added to blocklist")
@admin_required
async def cmd_rmblocklist(update,context):
    if not context.args: await update.effective_message.reply_text("Usage: /rmblocklist <word>"); return
    for w in context.args: await db.rm_blocklist(update.effective_chat.id,w)
    await update.effective_message.reply_text("Removed")

# ── Antiflood / AntiRaid / Clean ──
@admin_required
async def cmd_setflood(update,context):
    if not context.args: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(f"Flood limit: {s['flood_limit']}"); return
    try: n=int(context.args[0]); await db.set_setting(update.effective_chat.id,"flood_limit",n); await db.set_setting(update.effective_chat.id,"antiflood_on",True); await update.effective_message.reply_text(f"Flood limit set to {n}")
    except: await update.effective_message.reply_text("Usage: /setflood 5")
@admin_required
async def cmd_setfloodmode(update,context):
    if not context.args: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(f"Flood mode: {s['flood_mode']}"); return
    mode=" ".join(context.args); await db.set_setting(update.effective_chat.id,"flood_mode",mode); await update.effective_message.reply_text(f"Flood mode set to {mode}")
async def cmd_cleanwelcome(update,context):
    if context.args: val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"cleanwelcome",val); await update.effective_message.reply_text(f"cleanwelcome {'on' if val else 'off'}")
    else: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(f"cleanwelcome: {s['cleanwelcome']}")
async def cmd_cleanservice(update,context):
    if context.args: val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"cleanservice",val); await update.effective_message.reply_text(f"cleanservice {'on' if val else 'off'}")
    else: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(f"cleanservice: {s['cleanservice']}")

# ── Security handlers ──
async def do_illegal(update,context,reason=""):
    msg=update.effective_message; chat=update.effective_chat
    if not msg or not chat: return
    log.warning("ILLEGAL %s %s %s",chat.id,update.effective_user.id if update.effective_user else "?",reason)
    try: await msg.delete()
    except: 
        try: await context.bot.delete_message(chat.id,msg.message_id)
        except: pass
    await send_temp_notice(context,chat.id,ILLEGAL_NOTICE)

async def ocr_inspect(data:bytes):
    if not PIL_AVAILABLE or not TESS_AVAILABLE: return ""
    def _ocr():
        try:
            img=Image.open(io.BytesIO(data))
            if img.mode!="RGB": img=img.convert("RGB")
            max_dim=2000
            if max(img.size)>max_dim:
                ratio=max_dim/max(img.size); ns=(int(img.size[0]*ratio),int(img.size[1]*ratio))
                try: img=img.resize(ns, Image.Resampling.LANCZOS)
                except: img=img.resize(ns, Image.LANCZOS)
            return pytesseract.image_to_string(img) or ""
        except Exception as e: log.debug("OCR err %s",e); return ""
    return await asyncio.to_thread(_ocr)

async def handle_security(update,context):
    msg=update.effective_message
    if not msg or update.effective_chat.type=="private": return
    if msg.text and msg.text.startswith("/"): return
    if await check_bypass(update,context): return
    chat_id=update.effective_chat.id; s=await db.get_settings(chat_id); text=msg.text or msg.caption or ""
    if not text: return
    # allowlist bypass
    if await db.is_allowed(chat_id,text): return
    # global vault
    m=await db.check_global(text)
    if m: await do_illegal(update,context,f"global:{m}"); return
    # blocklist
    b=await db.is_blocklisted(text,chat_id)
    if b: await do_illegal(update,context,f"blocklist:{b}"); return
    # blacklist
    low=text.lower()
    for bad in OCR_BLACKLIST:
        if bad.lower() in low: await do_illegal(update,context,f"blacklist:{bad}"); return
    # locks
    if LINK_RE.search(text) and (await db.is_locked(chat_id,"url") or s["antilink"]):
        if not await db.is_allowed(chat_id,text):
            try: await msg.delete()
            except: pass
            if s["lockwarns"]: await db.add_warn(chat_id,update.effective_user.id,"locked link")
            await send_temp_notice(context,chat_id,"🔗 Links not allowed")
            return
    if EMAIL_RE.search(text) and await db.is_locked(chat_id,"email"):
        try: await msg.delete()
        except: pass
        await send_temp_notice(context,chat_id,"📧 Emails locked"); return
    if PHONE_RE.search(text) and await db.is_locked(chat_id,"phone"):
        try: await msg.delete()
        except: pass
        await send_temp_notice(context,chat_id,"📞 Phones locked"); return
    if msg.forward_from and await db.is_locked(chat_id,"forward"):
        try: await msg.delete()
        except: pass
        await send_temp_notice(context,chat_id,"🔁 Forwards locked"); return
    if SPAM_RE.search(text) and s["antispam"]:
        try: await msg.delete()
        except: pass
        await send_temp_notice(context,chat_id,"🚫 Spam deleted"); return
    # antiflood
    if s["antiflood_on"] and text:
        key=(chat_id,update.effective_user.id); now=asyncio.get_event_loop().time()
        lst=flood_tracker.get(key,[]); lst=[t for t in lst if now-t<10]; lst.append(now); flood_tracker[key]=lst
        if len(lst)>s["flood_limit"]:
            try: await msg.delete()
            except: pass
            mode=s["flood_mode"]
            if "ban" in mode:
                try: await context.bot.ban_chat_member(chat_id,update.effective_user.id)
                except: pass
            elif "mute" in mode or "tmute" in mode:
                dur=parse_duration(mode.split()[-1]) if len(mode.split())>1 else None
                until=datetime.datetime.now(datetime.timezone.utc)+dur if dur else None
                try: await context.bot.restrict_chat_member(chat_id,update.effective_user.id,permissions=ChatPermissions(can_send_messages=False),until_date=until)
                except: pass
            await send_temp_notice(context,chat_id,"🚨 Flood detected — muted")
            flood_tracker[key]=[]

async def handle_photo(update,context):
    msg=update.effective_message
    if not msg or update.effective_chat.type=="private": return
    if await check_bypass(update,context): return
    s=await db.get_settings(update.effective_chat.id)
    if not s["ocr_protection"]: return
    if await db.is_locked(update.effective_chat.id,"photo") or await db.is_locked(update.effective_chat.id,"document"):
        try: await msg.delete()
        except: pass
        await send_temp_notice(context,update.effective_chat.id,"🖼️ Media locked"); return
    tg_file=None; data=None
    if msg.photo:
        try: tg_file=await context.bot.get_file(msg.photo[-1].file_id)
        except: return
        if tg_file.file_size and tg_file.file_size>20*1024*1024: return
        try: bio=io.BytesIO(); await tg_file.download_to_memory(bio); data=bio.getvalue()
        except: return
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        try: tg_file=await context.bot.get_file(msg.document.file_id)
        except: return
        try: bio=io.BytesIO(); await tg_file.download_to_memory(bio); data=bio.getvalue()
        except: return
    else: return
    if not data: return
    extracted=await ocr_inspect(data)
    if not extracted.strip(): return
    low=extracted.lower()
    m=await db.check_global(low)
    if m: await do_illegal(update,context,f"ocr:{m}"); return
    for bad in OCR_BLACKLIST:
        if bad.lower() in low: await do_illegal(update,context,f"ocr:{bad}"); return

# ── Activity + Greetings + CAPTCHA ──
async def track_activity(update,context):
    if not update.effective_chat or not update.effective_user or update.effective_user.is_bot: return
    if update.effective_chat.type=="private": return
    await db.upsert_active(update.effective_chat.id,update.effective_user.id,update.effective_user.username,update.effective_user.full_name)
    # cleanservice: delete service messages
    if update.effective_message and (update.effective_message.new_chat_members or update.effective_message.left_chat_member):
        s=await db.get_settings(update.effective_chat.id)
        if s["cleanservice"]:
            try: await update.effective_message.delete()
            except: pass

async def greet_new_members(update,context):
    msg=update.effective_message; chat=update.effective_chat
    if not msg or not msg.new_chat_members or chat.type=="private": return
    s=await db.get_settings(chat.id)
    # greetings text
    wtext,gtext,won,gon=await db.get_greetings(chat.id)
    for member in msg.new_chat_members:
        if member.is_bot: continue
        # welcome
        if won and s["welcome_on"]:
            try:
                cnt=await context.bot.get_chat_member_count(chat.id)
                fill=fill_welcome(wtext,member,chat,cnt)
                # support buttons? simple HTML
                sent=await msg.reply_html(fill)
                if s["cleanwelcome"]:
                    # delete previous welcome if exists
                    old=await db._exec("SELECT welcome_clean_msg_id FROM chat_settings WHERE chat_id=?",(chat.id,),fetch="one")
                    if old and old[0]:
                        try: await context.bot.delete_message(chat.id, old[0])
                        except: pass
                    await db._exec("UPDATE chat_settings SET welcome_clean_msg_id=? WHERE chat_id=?",(sent.message_id,chat.id))
            except: pass
        # captcha gate
        if not s["captcha_enabled"]: continue
        try:
            await context.bot.restrict_chat_member(chat.id,member.id,permissions=ChatPermissions(can_send_messages=False,can_send_media_messages=False,can_send_other_messages=False,can_add_web_page_previews=False))
        except: continue
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify You Are Human",callback_data=f"captcha_{member.id}")]])
        try: sent2=await msg.reply_html(f"👋 Welcome {mention_html(member.id,member.full_name)}! Verify in <b>{CAPTCHA_TIMEOUT//60} min</b>",reply_markup=kb)
        except: continue
        job_name=f"captcha_{chat.id}_{member.id}"
        async def captcha_job(ctx):
            data=ctx.job.data if ctx.job else {}
            c_id=data.get("chat_id",chat.id); u_id=data.get("user_id",member.id); mid=data.get("msg_id",sent2.message_id)
            try:
                m=await ctx.bot.get_chat_member(c_id,u_id)
                if m.status==ChatMemberStatus.RESTRICTED or (c_id,u_id) in pending_captcha:
                    if (c_id,u_id) in pending_captcha:
                        try: await ctx.bot.ban_chat_member(c_id,u_id); await ctx.bot.unban_chat_member(c_id,u_id); await ctx.bot.send_message(c_id,f"⏰ {esc(member.full_name)} failed verification")
                        except: pass
                        try: await ctx.bot.delete_message(c_id,mid)
                        except: pass
                        pending_captcha.pop((c_id,u_id),None)
            except: pass
        if context.job_queue:
            job=context.job_queue.run_once(captcha_job,CAPTCHA_TIMEOUT,data={"chat_id":chat.id,"user_id":member.id,"msg_id":sent2.message_id},name=job_name)
            pending_captcha[(chat.id,member.id)]=job
        else:
            async def _fallback():
                await asyncio.sleep(CAPTCHA_TIMEOUT)
                class _F: job=type("o",(),{"data":{"chat_id":chat.id,"user_id":member.id,"msg_id":sent2.message_id}})(); bot=context.bot
                if (chat.id,member.id) in pending_captcha: await captcha_job(_F())
            task=asyncio.create_task(_fallback()); pending_captcha[(chat.id,member.id)]=task

async def cb_captcha(update,context):
    q=update.callback_query
    if not q or not q.data.startswith("captcha_"): return
    try: target=int(q.data.split("_")[1])
    except: await q.answer("Invalid",show_alert=True); return
    if update.effective_user.id!=target: await q.answer("Not for you — only new member",show_alert=True); return
    try: await context.bot.restrict_chat_member(update.effective_chat.id,target,permissions=ChatPermissions(can_send_messages=True,can_send_media_messages=True,can_send_other_messages=True,can_add_web_page_previews=True))
    except Exception as e: await q.answer(f"Failed {e}",show_alert=True); return
    key=(update.effective_chat.id,target); job=pending_captcha.pop(key,None)
    if job:
        try:
            if hasattr(job,"schedule_removal"): job.schedule_removal()
            else: job.cancel()
        except: pass
    try: await q.edit_message_text(f"✅ Verified — welcome {esc(update.effective_user.full_name)}!")
    except: await q.answer("Verified!")

async def cmd_setwelcome(update,context):
    chat=update.effective_chat; txt=" ".join(context.args) if context.args else (update.effective_message.reply_to_message.text if update.effective_message.reply_to_message else "")
    if not txt: await update.effective_message.reply_text("Usage: /setwelcome <text> — fillings: {fullname} {firstname} {username} {mention} {id} {chatname} {count}"); return
    # check private connection
    cid=await effective_chat_id(update,context); await db.set_welcome(cid,txt); await update.effective_message.reply_text("✅ Welcome updated")
async def cmd_resetwelcome(update,context): cid=await effective_chat_id(update,context); await db.set_welcome(cid,"Hello {fullname}! Welcome to {chatname}!"); await update.effective_message.reply_text("✅ Welcome reset")
async def cmd_setgoodbye(update,context):
    txt=" ".join(context.args) if context.args else ""
    if not txt: await update.effective_message.reply_text("Usage: /setgoodbye <text>"); return
    cid=await effective_chat_id(update,context); await db.set_goodbye(cid,txt); await update.effective_message.reply_text("✅ Goodbye updated")
async def cmd_resetgoodbye(update,context): cid=await effective_chat_id(update,context); await db.set_goodbye(cid,"Goodbye {fullname}!"); await update.effective_message.reply_text("✅ Goodbye reset")
async def cmd_welcome(update,context):
    if not context.args: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(f"welcome: {'on' if s['welcome_on'] else 'off'}"); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"welcome_on",val); await update.effective_message.reply_text(f"welcome {'on' if val else 'off'}")
async def cmd_goodbye(update,context):
    if not context.args: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(f"goodbye: {'on' if s['goodbye_on'] else 'off'}"); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"goodbye_on",val); await update.effective_message.reply_text(f"goodbye {'on' if val else 'off'}")

# ── Mass Mentions ──
@admin_required
async def cmd_all(update,context):
    chat=update.effective_chat; custom=" ".join(context.args) if context.args else "Attention!"
    actives=await db.get_active_users(chat.id,500)
    if not actives: await update.effective_message.reply_text("No active pool — chat first"); return
    await update.effective_message.reply_text(f"📢 Broadcasting to {len(actives)} in batches of {TAG_BATCH_SIZE}…")
    for i in range(0,len(actives),TAG_BATCH_SIZE):
        batch=actives[i:i+TAG_BATCH_SIZE]; mentions=" ".join(mention_html(uid,fname) for uid,_,fname in batch)
        try: await context.bot.send_message(chat.id,f"📣 <b>{esc(custom)}</b>\n\n{mentions}",parse_mode=ParseMode.HTML)
        except: await asyncio.sleep(2)
        if i+TAG_BATCH_SIZE < len(actives): await asyncio.sleep(TAG_BATCH_DELAY)
@admin_required
async def cmd_admins(update,context):
    chat=update.effective_chat; custom=" ".join(context.args) if context.args else "Admins please attend!"
    try: admins=await context.bot.get_chat_administrators(chat.id)
    except Exception as e: await update.effective_message.reply_text(f"Failed {e}"); return
    human=[a for a in admins if not a.user.is_bot]
    if not human: await update.effective_message.reply_text("No human admins"); return
    await update.effective_message.reply_text(f"👮 Tagging {len(human)} admins…")
    users=[(a.user.id,a.user.username,a.user.full_name) for a in human]
    for i in range(0,len(users),TAG_BATCH_SIZE):
        batch=users[i:i+TAG_BATCH_SIZE]; mentions=" ".join(mention_html(uid,fname) for uid,_,fname in batch)
        try: await context.bot.send_message(chat.id,f"👮 <b>{esc(custom)}</b>\n\n{mentions}",parse_mode=ParseMode.HTML)
        except: pass
        if i+TAG_BATCH_SIZE < len(users): await asyncio.sleep(TAG_BATCH_DELAY)

# ── ShadowReply ──
@admin_required
async def cmd_shadowreply(update,context):
    msg=update.effective_message; chat=update.effective_chat
    payload=" ".join(context.args) if context.args else ""
    if not payload and msg.reply_to_message and (msg.reply_to_message.text or msg.reply_to_message.caption): payload=msg.reply_to_message.text or msg.reply_to_message.caption
    if not payload.strip(): await msg.reply_text("Usage: /shadowreply <message>"); return
    try: await msg.delete()
    except:
        try: await context.bot.delete_message(chat.id,msg.message_id)
        except: pass
    try: await context.bot.send_message(chat.id,payload,parse_mode=ParseMode.HTML)
    except: await context.bot.send_message(chat.id,payload)

# ── Wordle ──
def score_guess(secret,guess):
    n=len(secret); res=["🚫"]*n; secret=list(secret.lower()); guessl=list(guess.lower())
    rem={}
    for i in range(n):
        if guessl[i]==secret[i]: res[i]="🎯"
        else: rem[secret[i]]=rem.get(secret[i],0)+1
    for i in range(n):
        if res[i]=="🎯": continue
        g=guessl[i]
        if rem.get(g,0)>0: res[i]="🔀"; rem[g]-=1
    return res
async def cmd_newgame(update,context):
    chat=update.effective_chat; raw=update.effective_message.text.split()[0].split("@")[0].lstrip("/").lower() if update.effective_message.text else "new"
    length=5
    if raw in ("new4","word4"): length=4
    elif raw in ("new5","word5"): length=5
    elif raw in ("new6","word6"): length=6
    elif raw in ("new","newword","newgame","word","wordle"): length=5
    if context.args and context.args[0].isdigit() and int(context.args[0]) in (4,5,6): length=int(context.args[0])
    pool=WORD_BANK.get(length,WORD_BANK[5]); word=random.choice(pool)
    await db.start_game(chat.id,word,update.effective_user.id)
    await update.effective_message.reply_html(f"🎲 <b>Word Jumble Started!</b> — {length} letters\nGuess plain text ({length} letters). 🎯 = correct, 🔀 = wrong spot, 🚫 = absent\n<code>/game</code> status, <code>/top</code> leaderboard")
async def cmd_gamestatus(update,context):
    g=await db.get_game(update.effective_chat.id)
    if not g: await update.effective_message.reply_text("No game. /new /new4 /new5 /new6"); return
    w,l,s,a=g; await update.effective_message.reply_html(f"🎮 <b>Game Active</b> Letters:{l} Attempts:{a}")
async def cmd_top(update,context):
    rows=await db.top_players(10)
    if not rows: await update.effective_message.reply_text("No wins yet. /new"); return
    lines=["🏆 <b>Top Players</b>"]+[f"{i}. {esc(uname or str(uid))} — {wins} win{'s' if wins!=1 else ''}" for i,(uid,uname,wins) in enumerate(rows,1)]
    await update.effective_message.reply_html("\n".join(lines))
async def handle_wordle(update,context):
    msg=update.effective_message
    if not msg or not msg.text or update.effective_chat.type=="private": return
    txt=msg.text.strip()
    if txt.startswith("/") or txt.startswith("#") or txt.startswith("@"): return
    if " " in txt or not txt.isalpha(): return
    g=await db.get_game(update.effective_chat.id)
    if not g: return
    secret,length,starter,attempts=g
    if len(txt)!=length: return
    sc=score_guess(secret,txt.lower()); await db.bump_attempts(update.effective_chat.id)
    mention=mention_html(update.effective_user.id,update.effective_user.full_name)
    line=" ".join(sc)
    if txt.lower()==secret.lower():
        await db.add_win(update.effective_user.id,update.effective_user.username); await db.stop_game(update.effective_chat.id)
        await msg.reply_html(f"{line}\n🎉 <b>Correct!</b> {mention} guessed <code>{esc(secret.upper())}</code>! /new for next")
    else:
        await db.add_played(update.effective_user.id,update.effective_user.username)
        await msg.reply_html(f"{mention} → <code>{esc(txt.upper())}</code>\n{line}\n<i>🎯 right spot, 🔀 wrong spot, 🚫 absent</i>")

# ── Connections / Disable / Other ──
async def cmd_connect(update,context):
    if update.effective_chat.type!="private": await update.effective_message.reply_text("Use this in private with bot"); return
    if not context.args:
        cid=await db.get_connection(update.effective_user.id)
        if cid: await update.effective_message.reply_text(f"Connected to {cid}. /disconnect to leave")
        else: await update.effective_message.reply_text("Usage: /connect <chat_id> or add me to group as admin then /connect")
        return
    arg=context.args[0]
    try: cid=int(arg)
    except: await update.effective_message.reply_text("Pass chat_id ( /id in group to get it )"); return
    # verify user is admin in that chat
    try: m=await context.bot.get_chat_member(cid,update.effective_user.id)
    except: await update.effective_message.reply_text("Not in that chat or bot not in chat"); return
    if m.status not in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER) and not await is_owner(update.effective_user.id):
        await update.effective_message.reply_text("Only admins can connect"); return
    await db.set_connection(update.effective_user.id,cid); await update.effective_message.reply_text(f"✅ Connected to {cid}. Now send commands here as if in group.")
async def cmd_disconnect(update,context): await db._exec("DELETE FROM connections WHERE user_id=?",(update.effective_user.id,)); await update.effective_message.reply_text("🔌 Disconnected")
@admin_required
async def cmd_disable(update,context):
    if not context.args: items=await db.list_disabled(update.effective_chat.id); await update.effective_message.reply_text("Disabled: "+", ".join(items) if items else "None"); return
    for c in context.args: await db.disable_cmd(update.effective_chat.id,c.lower())
    await update.effective_message.reply_text("Disabled: "+", ".join(context.args))
@admin_required
async def cmd_enable(update,context):
    if not context.args: await update.effective_message.reply_text("Usage: /enable <command>"); return
    for c in context.args: await db.enable_cmd(update.effective_chat.id,c.lower())
    await update.effective_message.reply_text("Enabled: "+", ".join(context.args))
async def cmd_setlang(update,context): await update.effective_message.reply_text("✅ Language set (stub — multilanguage coming)")
async def cmd_export(update,context):
    chat=update.effective_chat; cats=context.args if context.args else ["notes","filters","rules","warns","locks","greetings"]
    data={}
    if "notes" in cats: data["notes"]=[{"name":n,"content":await db.get_note(chat.id,n)} for n in await db.list_notes(chat.id)]
    if "rules" in cats: data["rules"]=await db.get_rules(chat.id)
    if "greetings" in cats: data["greetings"]=await db.get_greetings(chat.id)
    if "warns" in cats: rows=await db._exec("SELECT user_id,count FROM warnings WHERE chat_id=?",(chat.id,),fetch="all"); data["warns"]=rows
    if "locks" in cats: data["locks"]=await db.list_locks(chat.id)
    if "blocklists" in cats: data["blocklists"]=await db.list_blocklist(chat.id)
    bio=io.BytesIO(json.dumps(data,indent=2,ensure_ascii=False).encode()); bio.name=f"export_{chat.id}.json"
    await context.bot.send_document(chat.id,bio,caption=f"Exported {', '.join(cats)}")
async def cmd_import(update,context):
    msg=update.effective_message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text("Reply to exported JSON file with /import"); return
    try: f=await context.bot.get_file(msg.reply_to_message.document.file_id); bio=io.BytesIO(); await f.download_to_memory(bio); data=json.loads(bio.getvalue().decode())
    except Exception as e: await msg.reply_text(f"Import failed: {e}"); return
    chat=update.effective_chat
    if "notes" in data:
        for n in data["notes"]: await db.set_note(chat.id,n["name"],n["content"],update.effective_user.id)
    if "rules" in data and data["rules"]: await db.set_rules(chat.id,data["rules"])
    await msg.reply_text("✅ Imported")

# ── Utility ──
async def cmd_start(update,context):
    await update.effective_message.reply_html("✨ <b>Miss Sona — The Secret Squad Guardian</b> ✨\n\nRose-parity + industrial modules.\n<b>Mod:</b> /ban /tban /mute /warn /promote /adminlist\n<b>Locks:</b> /lock /unlock /locks /allowlist /setflood\n<b>Welcome:</b> /setwelcome {fullname} /welcome on + CAPTCHA ✅\n<b>Vault:</b> /addbword /listbwords (owner)\n<b>Broadcast:</b> /all /admins 5/3s\n<b>Shadow:</b> /shadowreply\n<b>Game:</b> /new /new4 /new5 /new6 /top\n<b>Utility:</b> /connect (DM), /export /import, /disable\n\nBuilt for The Secret Squad")
async def cmd_help(update,context): await cmd_start(update,context)
async def cmd_id(update,context):
    msg=update.effective_message; chat=update.effective_chat; user=update.effective_user
    lines=[f"👤 You: <code>{user.id}</code> — {esc(user.full_name)}"]
    if chat: lines.append(f"💬 Chat: <code>{chat.id}</code> — {esc(chat.title or chat.type)}")
    if msg.reply_to_message and msg.reply_to_message.from_user: u=msg.reply_to_message.from_user; lines.append(f"↩️ Reply: <code>{u.id}</code> — {esc(u.full_name)}")
    await msg.reply_html("\n".join(lines))

async def error_handler(update,context): log.exception("Update %s error %s",update,context.error)

def build_app():
    app=Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    G_TRACK=-1; G_SEC=0; G_MAIN=1; G_FB=2
    app.add_handler(MessageHandler(filters.ALL, track_activity), group=G_TRACK)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_security), group=G_SEC)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo), group=G_SEC)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, greet_new_members), group=G_MAIN)
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, track_activity), group=G_MAIN)
    app.add_handler(CallbackQueryHandler(cb_captcha, pattern=r"^captcha_\d+$"), group=G_MAIN)
    app.add_handler(CallbackQueryHandler(cb_toggle_locks, pattern=r"^(toggle_|close_locks)"), group=G_MAIN)
    # moderation
    app.add_handler(CommandHandler(["ban","dban","sban","tban"], cmd_ban), group=G_MAIN)
    app.add_handler(CommandHandler("unban", cmd_unban), group=G_MAIN)
    app.add_handler(CommandHandler(["mute","dmute","smute","tmute"], cmd_mute), group=G_MAIN)
    app.add_handler(CommandHandler("unmute", cmd_unmute), group=G_MAIN)
    app.add_handler(CommandHandler(["kick","dkick","skick"], cmd_kick), group=G_MAIN)
    app.add_handler(CommandHandler(["warn","dwarn","swarn"], cmd_warn), group=G_MAIN)
    app.add_handler(CommandHandler(["warns","warnings"], cmd_warns), group=G_MAIN)
    app.add_handler(CommandHandler("rmwarn", cmd_rmwarn), group=G_MAIN)
    app.add_handler(CommandHandler("resetwarn", cmd_resetwarn), group=G_MAIN)
    app.add_handler(CommandHandler("resetallwarns", cmd_resetallwarns), group=G_MAIN)
    app.add_handler(CommandHandler("warnings", cmd_warnings), group=G_MAIN)
    app.add_handler(CommandHandler("warnmode", cmd_warnmode), group=G_MAIN)
    app.add_handler(CommandHandler("warnlimit", cmd_warnlimit), group=G_MAIN)
    app.add_handler(CommandHandler("promote", cmd_promote), group=G_MAIN)
    app.add_handler(CommandHandler("demote", cmd_demote), group=G_MAIN)
    app.add_handler(CommandHandler("adminlist", cmd_adminlist), group=G_MAIN)
    app.add_handler(CommandHandler("admincache", cmd_admincache), group=G_MAIN)
    app.add_handler(CommandHandler("anonadmin", cmd_anonadmin), group=G_MAIN)
    # rules/notes/trust
    app.add_handler(CommandHandler("rules", cmd_rules), group=G_MAIN)
    app.add_handler(CommandHandler("setrules", cmd_setrules), group=G_MAIN)
    app.add_handler(CommandHandler("privaterules", cmd_privaterules), group=G_MAIN)
    app.add_handler(CommandHandler("resetrules", cmd_resetrules), group=G_MAIN)
    app.add_handler(CommandHandler(["setnote","note","savenote","addnote"], cmd_setnote), group=G_MAIN)
    app.add_handler(CommandHandler(["getnote","notes"], cmd_getnote), group=G_MAIN)
    app.add_handler(CommandHandler("delnote", cmd_delnote), group=G_MAIN)
    app.add_handler(CommandHandler("trust", cmd_trust), group=G_MAIN)
    app.add_handler(CommandHandler("untrust", cmd_untrust), group=G_MAIN)
    # vault
    app.add_handler(CommandHandler(["addbword","addbanword","gbanword"], cmd_addbanword), group=G_MAIN)
    app.add_handler(CommandHandler(["rmbword","delbword","removebanword"], cmd_rmbanword), group=G_MAIN)
    app.add_handler(CommandHandler(["listbwords","globalbans","listbans"], cmd_listbanwords), group=G_MAIN)
    # locks
    app.add_handler(CommandHandler("lock", cmd_lock), group=G_MAIN)
    app.add_handler(CommandHandler("unlock", cmd_unlock), group=G_MAIN)
    app.add_handler(CommandHandler(["locks","lock"], cmd_locks_panel), group=G_MAIN)
    app.add_handler(CommandHandler("locktypes", cmd_locktypes), group=G_MAIN)
    app.add_handler(CommandHandler("lockwarns", cmd_lockwarns), group=G_MAIN)
    app.add_handler(CommandHandler("allowlist", cmd_allowlist), group=G_MAIN)
    app.add_handler(CommandHandler("rmallowlist", cmd_rmallowlist), group=G_MAIN)
    app.add_handler(CommandHandler("rmallowlistall", cmd_rmallowlistall), group=G_MAIN)
    app.add_handler(CommandHandler("whitelist", cmd_whitelist), group=G_MAIN)
    app.add_handler(CommandHandler("blocklist", cmd_blocklist), group=G_MAIN)
    app.add_handler(CommandHandler("rmblocklist", cmd_rmblocklist), group=G_MAIN)
    app.add_handler(CommandHandler("setflood", cmd_setflood), group=G_MAIN)
    app.add_handler(CommandHandler("setfloodmode", cmd_setfloodmode), group=G_MAIN)
    app.add_handler(CommandHandler("cleanwelcome", cmd_cleanwelcome), group=G_MAIN)
    app.add_handler(CommandHandler("cleanservice", cmd_cleanservice), group=G_MAIN)
    # greetings
    app.add_handler(CommandHandler("setwelcome", cmd_setwelcome), group=G_MAIN)
    app.add_handler(CommandHandler("resetwelcome", cmd_resetwelcome), group=G_MAIN)
    app.add_handler(CommandHandler("setgoodbye", cmd_setgoodbye), group=G_MAIN)
    app.add_handler(CommandHandler("resetgoodbye", cmd_resetgoodbye), group=G_MAIN)
    app.add_handler(CommandHandler("welcome", cmd_welcome), group=G_MAIN)
    app.add_handler(CommandHandler("goodbye", cmd_goodbye), group=G_MAIN)
    # mentions
    app.add_handler(CommandHandler(["all","tagall","everyone","mentionall"], cmd_all), group=G_MAIN)
    app.add_handler(CommandHandler(["admins","admin","tagadmins"], cmd_admins), group=G_MAIN)
    app.add_handler(CommandHandler("shadowreply", cmd_shadowreply), group=G_MAIN)
    # game
    app.add_handler(CommandHandler(["new","new4","new5","new6","newword","newgame","word","wordle","word4","word5","word6"], cmd_newgame), group=G_MAIN)
    app.add_handler(CommandHandler(["game","gamestatus","status"], cmd_gamestatus), group=G_MAIN)
    app.add_handler(CommandHandler(["top","leaderboard","lb"], cmd_top), group=G_MAIN)
    # connections etc
    app.add_handler(CommandHandler("connect", cmd_connect), group=G_MAIN)
    app.add_handler(CommandHandler("disconnect", cmd_disconnect), group=G_MAIN)
    app.add_handler(CommandHandler("disable", cmd_disable), group=G_MAIN)
    app.add_handler(CommandHandler("enable", cmd_enable), group=G_MAIN)
    app.add_handler(CommandHandler("setlang", cmd_setlang), group=G_MAIN)
    app.add_handler(CommandHandler("export", cmd_export), group=G_MAIN)
    app.add_handler(CommandHandler("import", cmd_import), group=G_MAIN)
    app.add_handler(CommandHandler(["start","help"], cmd_start), group=G_MAIN)
    app.add_handler(CommandHandler(["id","chatid","myid"], cmd_id), group=G_MAIN)
    # fallback
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^#\w+"), hashtag_note_handler), group=G_FB)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.ALL, handle_wordle), group=G_FB)
    app.add_error_handler(error_handler)
    return app

async def post_init(app): await db.init(); me=await app.bot.get_me(); log.info("Miss Sona @%s id=%s WAL %s — The Secret Squad",me.username,me.id,DB_PATH)
async def post_shutdown(app): log.info("Shutting down Miss Sona")
def main():
    app=build_app(); app.post_init=post_init; app.post_shutdown=post_shutdown
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, close_loop=False)
if __name__=="__main__": main()
