#!/usr/bin/env python3
"""
Miss Sona v3.0 — Industrial-Grade Telegram Group Management Bot
===========================================================
By The Secret Squad Community | python-telegram-bot v20+ | asyncio + SQLite WAL
Single-file production-ready | Miss Sona Full Parity + Custom Industrial Modules

Setup:
  pip install "python-telegram-bot[job-queue]>=20.8" Pillow pytesseract
  sudo apt install tesseract-ocr
  export BOT_TOKEN="token" OWNER_ID="12345" SUPPORT_CHAT_ID="-1001234567890"
  python miss_sona.py
"""
from __future__ import annotations
import asyncio, datetime, html, io, json, logging, os, random, re, sqlite3
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

# ── Config + Branding ──
BOT_TOKEN=os.getenv("BOT_TOKEN","PUT_YOUR_TOKEN_HERE")
OWNER_ID=int(os.getenv("OWNER_ID","0")) or None
DB_PATH=os.getenv("DB_PATH","miss_sona.db")
SUPPORT_CHAT_ID=os.getenv("SUPPORT_CHAT_ID","") or os.getenv("LOG_CHANNEL_ID","")
SUPPORT_CHAT_ID=int(SUPPORT_CHAT_ID) if SUPPORT_CHAT_ID and SUPPORT_CHAT_ID.lstrip("-").isdigit() else None
SUPPORT_LINK=os.getenv("SUPPORT_LINK","https://t.me/SecretSquadSupport")
BRAND_NAME="The Secret Squad"
BRAND_TAG="Built with ❤️ by The Secret Squad"
BRANDED_FOOTER=f"\n\n— ✨ {BRAND_TAG} | Support: {SUPPORT_LINK}"
def branded(text:str)->str: return text + BRANDED_FOOTER

CAPTCHA_TIMEOUT=120
TAG_BATCH_SIZE=5
TAG_BATCH_DELAY=3.0
ILLEGAL_NOTICE="I found something illegal so I deleted it"
ILLEGAL_NOTICE_TTL=15

DEFAULT_SETTINGS={"antilink":1,"antispam":1,"ocr_protection":1,"captcha_enabled":1,"lockwarns":0,"cleanwelcome":0,"cleanservice":0,"welcome_on":1,"goodbye_on":0,"antiflood_on":1,"flood_limit":5,"flood_mode":"tmute 1h"}
LOCK_TYPES=["url","forward","inline","invite","sticker","photo","video","gif","audio","voice","document","contact","location","game","rtl","button","command","email","phone","bot"]
WORD_BANK={
 4:["code","game","sona","rose","bolt","flux","kilo","nova","apex","zeal","iris","luna","aura","vibe","echo","frost","glow","kind","mint","nest","opal","peak","quip","rift","sage","tide","vale","warp","yarn","zest","bark","clap","dune","fern","gale","howl","jade","kite","lark","moss","neon","oath","plum","quay","rust","star","tune","urge","void","wisp"],
 5:["sonar","crane","ultra","blitz","frost","flame","glide","prism","quirk","amber","blaze","crest","drift","ember","forge","gloom","haven","ivory","jolly","kneel","lunar","mango","nymph","ocean","pride","quest","rally","stone","theta","unity","vivid","whirl","xenon","yield","zebra","apple","brave","crown","dream","eager","flora","giant","happy","ideal","jazzy","knack","light","magic","noble","olive","brack","blues","basks","books","beats"],
 6:["python","rocket","silver","golden","cobalt","nebula","jasper","quartz","velvet","arcade","breeze","castle","desert","engine","falcon","galaxy","harbor","island","jungle","kernel","legend","marble","nimbus","orange","planet","quiver","ranger","summit","tunnel","unique","voyage","wisdom","yonder","zenith","bronze","crystal","diamond","emerald","fortune"]
}
OCR_BLACKLIST=["csam","child porn","cp video","minor nude","bomb making","how to make bomb","terrorist recruitment","credit card dump","illegal drugs buy"]
LINK_RE=re.compile(r"(?:https?://|t\.me/|telegram\.me/|telegram\.dog/|wa\.me/|discord\.gg/|invite)\S+",re.I)
SPAM_RE=re.compile(r"(.)\1{7,}")
EMAIL_RE=re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE=re.compile(r"\+?\d{10,15}")
pending_captcha:Dict[Tuple[int,int],object]={}
flood_tracker:Dict[Tuple[int,int], List[float]]={}

# ── Stack Emojify Book Class (MANDATORY) ──
class Emojify:
    """Book-like stacked Wordle display. Purely understandable stacked board."""
    @staticmethod
    def score_word(secret: str, guess: str) -> List[str]:
        n=len(secret); res=["🚫"]*n; s=list(secret.lower()); g=list(guess.lower())
        rem={}
        for i in range(n):
            if g[i]==s[i]: res[i]="🎯"
            else: rem[s[i]]=rem.get(s[i],0)+1
        for i in range(n):
            if res[i]=="🎯": continue
            if rem.get(g[i],0)>0: res[i]="🔀"; rem[g[i]]-=1
        return res
    @staticmethod
    def emojify_word(word:str, scored:List[str])->str:
        return f"{word.upper()}  {' '.join(scored)}"
    @staticmethod
    def stack_emojify(secret_word:str, guesses:List[dict], length:int, max_display:int=10) -> str:
        # header
        header=f"📚 <b>Miss Sona Wordle — {length} Letters</b> | Attempts: {len(guesses)}/6\n"
        header+=f"<code>┌{'─'*28}┐</code>\n"
        if not guesses:
            body=f"<code>│ {'No guesses yet. Send a '+str(length)+'-letter word!':28} │</code>\n"
        else:
            body=""
            for g in guesses[-max_display:]:
                w=g['word'].upper()
                scored=' '.join(g['scored'])
                # line like: │ BRACK 🚫 🎯 🚫 🔀 🚫 by Alice │
                uname=g.get('username','?')[:12]
                line=f"{w:6} {scored} by {uname}"
                # truncate/pad to 28 visible chars (emoji counts as 2 but we approximate)
                # keep HTML safe: escape username already
                body+=f"<code>│ {line[:28].ljust(28)} │</code>\n"
        footer=f"<code>└{'─'*28}┘</code>\n"
        footer+=f"<i>🎯=perfect 🔀=misplaced 🚫=absent</i>{BRANDED_FOOTER}"
        return header+body+footer

# Alias as requested
StackEmojify=Emojify
Stack=Emojify.stack_emojify

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
        cur.execute("""CREATE TABLE IF NOT EXISTS games(chat_id INTEGER PRIMARY KEY, word TEXT, length INTEGER, starter_id INTEGER, attempts INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, guesses_json TEXT DEFAULT '[]', board_msg_id INTEGER, started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS leaderboard(user_id INTEGER PRIMARY KEY, username TEXT, wins INTEGER DEFAULT 0, games_played INTEGER DEFAULT 0)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS greetings(chat_id INTEGER PRIMARY KEY, welcome_text TEXT DEFAULT 'Hello {fullname}! Welcome to {chatname}!', goodbye_text TEXT DEFAULT 'Goodbye {fullname}!', welcome_on INTEGER DEFAULT 1, goodbye_on INTEGER DEFAULT 0)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS disabled_commands(chat_id INTEGER NOT NULL, command TEXT NOT NULL, PRIMARY KEY(chat_id,command))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS blocklists(chat_id INTEGER NOT NULL, word TEXT NOT NULL, PRIMARY KEY(chat_id,word))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS locks(chat_id INTEGER NOT NULL, lock_type TEXT NOT NULL, locked INTEGER DEFAULT 0, PRIMARY KEY(chat_id,lock_type))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS allowlist(chat_id INTEGER NOT NULL, item TEXT NOT NULL, PRIMARY KEY(chat_id,item))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS connections(user_id INTEGER PRIMARY KEY, chat_id INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS warns_settings(chat_id INTEGER PRIMARY KEY, limit_num INTEGER DEFAULT 3, mode TEXT DEFAULT 'ban')""")
        cur.execute("""CREATE TABLE IF NOT EXISTS bot_config(key TEXT PRIMARY KEY, value TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS support_logs(log_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, actor_id INTEGER, action TEXT, target_id INTEGER, detail TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        # migrations for existing DBs
        for col, typ in [("guesses_json","TEXT DEFAULT '[]'"),("board_msg_id","INTEGER")]:
            try: cur.execute(f"ALTER TABLE games ADD COLUMN {col} {typ}")
            except: pass
        con.commit(); con.close()
    async def init(self):
        async with self._init_lock: await asyncio.to_thread(self._init_sync)
        log.info("SQLite WAL ready at %s — Support: %s", self.path, SUPPORT_CHAT_ID or "not set")
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
            await self._exec(f"UPDATE chat_settings SET {key}=? WHERE chat_id=?",(int(val) if isinstance(val,bool) else int(val) if key=="flood_limit" else val,chat_id))
        else:
            await self._exec(f"UPDATE chat_settings SET {key}=? WHERE chat_id=?",(val,chat_id))
    # active
    async def upsert_active(self, chat_id, user_id, username, fullname): await self._exec("INSERT INTO active_users(chat_id,user_id,username,fullname,last_seen) VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(chat_id,user_id) DO UPDATE SET username=excluded.username, fullname=excluded.fullname, last_seen=CURRENT_TIMESTAMP",(chat_id,user_id,username,fullname))
    async def get_active_users(self, chat_id, limit=200): rows=await self._exec("SELECT user_id, username, fullname FROM active_users WHERE chat_id=? ORDER BY last_seen DESC LIMIT ?",(chat_id,limit),fetch="all"); return rows or []
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
    # bot config + support logs
    async def set_config(self, key, value): await self._exec("INSERT INTO bot_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?",(key,value,value))
    async def get_config(self, key): row=await self._exec("SELECT value FROM bot_config WHERE key=?",(key,),fetch="one"); return row[0] if row else None
    async def log_support(self, chat_id, actor_id, action, target_id=None, detail=""): await self._exec("INSERT INTO support_logs(chat_id,actor_id,action,target_id,detail) VALUES(?,?,?,?,?)",(chat_id,actor_id,action,target_id,detail))
    # games with guesses_json
    async def start_game(self, chat_id, word, starter): await self._exec("INSERT INTO games(chat_id,word,length,starter_id,attempts,is_active,guesses_json,board_msg_id) VALUES(?,?,?,?,0,1,'[]',NULL) ON CONFLICT(chat_id) DO UPDATE SET word=excluded.word, length=excluded.length, starter_id=excluded.starter_id, attempts=0, is_active=1, guesses_json='[]', board_msg_id=NULL, started_at=CURRENT_TIMESTAMP",(chat_id,word.lower(),len(word),starter))
    async def get_game(self, chat_id): row=await self._exec("SELECT word,length,starter_id,attempts,guesses_json,board_msg_id FROM games WHERE chat_id=? AND is_active=1",(chat_id,),fetch="one"); return row
    async def get_game_raw(self, chat_id): row=await self._exec("SELECT word,guesses_json FROM games WHERE chat_id=? AND is_active=1",(chat_id,),fetch="one"); return row
    async def bump_attempts(self, chat_id): await self._exec("UPDATE games SET attempts=attempts+1 WHERE chat_id=?",(chat_id,))
    async def set_guesses(self, chat_id, guesses_json): await self._exec("UPDATE games SET guesses_json=? WHERE chat_id=?",(guesses_json,chat_id))
    async def set_board_msg(self, chat_id, msg_id): await self._exec("UPDATE games SET board_msg_id=? WHERE chat_id=?",(msg_id,chat_id))
    async def stop_game(self, chat_id): await self._exec("UPDATE games SET is_active=0 WHERE chat_id=?",(chat_id,))
    async def get_game_word_only(self, chat_id): row=await self._exec("SELECT word FROM games WHERE chat_id=? AND is_active=1",(chat_id,),fetch="one"); return row[0] if row else None
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
    real_id=await effective_chat_id(update,context) if isinstance(chat, type(update.effective_user)) else chat.id
    # handle private connections: check admin in connected chat
    check_id=await effective_chat_id(update,context)
    try:
        m=await context.bot.get_chat_member(check_id if update.effective_chat.type=="private" else chat.id, uid)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except: return False
async def is_owner(uid): return bool(OWNER_ID and uid==OWNER_ID)
async def effective_chat_id(update,context):
    chat=update.effective_chat
    if chat.type=="private":
        cid=await db.get_connection(update.effective_user.id)
        if cid: return cid
    return chat.id

# Unified Target Resolution (MANDATORY)
async def resolve_target(update, context) -> Optional[Tuple[int,str]]:
    msg=update.effective_message
    # 1. Reply priority
    if msg and msg.reply_to_message and msg.reply_to_message.from_user:
        u=msg.reply_to_message.from_user; return u.id, u.full_name
    # 2 & 3: args
    if context.args:
        arg=context.args[0].lstrip("@")
        # numeric ID
        if arg.lstrip("-").isdigit(): return int(arg), f"user {arg}"
        # @username via active pool
        chat_id=update.effective_chat.id if update.effective_chat else None
        if chat_id:
            actives=await db.get_active_users(chat_id,500)
            for uid,uname,fname in actives:
                if uname and uname.lower()==arg.lower(): return uid, fname
            # fallback get_chat_member
            try:
                m=await context.bot.get_chat_member(chat_id, int(arg)) if arg.isdigit() else None
                if m: return m.user.id, m.user.full_name
            except: pass
        return None
    return None

async def get_support_chat_id():
    # priority: env > db config
    if SUPPORT_CHAT_ID: return SUPPORT_CHAT_ID
    v=await db.get_config("SUPPORT_CHAT_ID")
    if v and v.lstrip("-").isdigit(): return int(v)
    return None

async def log_to_support(context, action_text:str, chat_id=None, actor_id=None, target_id=None, detail=""):
    # insert table
    try: await db.log_support(chat_id, actor_id, action_text, target_id, detail)
    except: pass
    sup=await get_support_chat_id()
    if not sup: return
    try:
        chat_title=""
        if chat_id:
            try: c=await context.bot.get_chat(chat_id); chat_title=esc(c.title or str(c.id))
            except: chat_title=str(chat_id)
        actor_txt=f"<code>{actor_id}</code>" if actor_id else "?"
        target_txt=f"<code>{target_id}</code>" if target_id else "-"
        now=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        txt=f"🛡️ <b>Miss Sona Log | {esc(BRAND_NAME)}</b>\nChat: {chat_title} (<code>{chat_id}</code>)\nAction: {esc(action_text)}\nBy: {actor_txt}\nTarget: {target_txt}\nTime: {now}\nDetail: {esc(detail[:200])}"
        await context.bot.send_message(sup,txt,parse_mode=ParseMode.HTML)
    except Exception as e: log.debug("support log fail %s",e)

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
            if update.effective_message: await update.effective_message.reply_text("🚫 Only admins."+BRANDED_FOOTER)
            return
        return await func(update,context,*a,**k)
    return w
def owner_required(func):
    async def w(update,context,*a,**k):
        uid=update.effective_user.id if update.effective_user else 0
        if not await is_owner(uid):
            if update.effective_message: await update.effective_message.reply_text("🔒 Owner-only."+BRANDED_FOOTER)
            return
        return await func(update,context,*a,**k)
    return w
async def check_bypass(update,context):
    user=update.effective_user; chat=update.effective_chat
    if not user or not chat: return True
    if await is_owner(user.id): return True
    real_chat_id=await effective_chat_id(update,context)
    try:
        m=await context.bot.get_chat_member(real_chat_id if chat.type!="private" else chat.id, user.id)
        if m.status in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER): return True
    except: pass
    if chat.type!="private" and await db.is_trusted(chat.id, user.id): return True
    return False

# ── Moderation ──
@admin_required
async def cmd_ban(update,context):
    chat=update.effective_chat; msg=update.effective_message
    tgt=await resolve_target(update,context)
    if not tgt: await msg.reply_text(branded("Usage: Reply to user or /ban <@username|ID> [reason]")); return
    uid,name=tgt
    if OWNER_ID and uid==OWNER_ID: await msg.reply_text("Can't ban owner"+BRANDED_FOOTER); return
    if await is_admin(update,context,uid): await msg.reply_text("Can't ban admin"+BRANDED_FOOTER); return
    cmd=msg.text.split()[0].split("@")[0].lstrip("/").lower()
    reason=" ".join(context.args[1:]) if len(context.args)>1 else "Banned"
    until=None
    if cmd in ("tban","tmute") or (context.args and len(context.args)>1 and parse_duration(context.args[-1])):
        dur=parse_duration(context.args[-1]) if context.args else None
        if dur: until=datetime.datetime.now(datetime.timezone.utc)+dur; reason=" ".join(context.args[1:-1]) if len(context.args)>2 else "Temp ban"
    try:
        if until: await context.bot.ban_chat_member(chat.id,uid,until_date=until)
        else: await context.bot.ban_chat_member(chat.id,uid)
        await msg.reply_html(branded(f"🔨 Banned {mention_html(uid,name)} <i>{esc(reason)}</i>"))
        await log_to_support(context,f"BAN {uid} in {chat.id} reason:{reason}",chat.id,update.effective_user.id,uid,reason)
        if cmd in ("sban","tban"): 
            try: await msg.delete()
            except: pass
        elif cmd=="dban":
            try: await update.effective_message.reply_to_message.delete()
            except: pass
    except Exception as e: await msg.reply_text(f"Ban failed: {e}"+BRANDED_FOOTER)

@admin_required
async def cmd_unban(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Usage: /unban <@username|ID>")); return
    uid,name=tgt
    try: await context.bot.unban_chat_member(update.effective_chat.id,uid); await update.effective_message.reply_html(branded(f"✅ Unbanned {mention_html(uid,name)}")); await log_to_support(context,f"UNBAN {uid}",update.effective_chat.id,update.effective_user.id,uid)
    except Exception as e: await update.effective_message.reply_text(f"Unban failed: {e}"+BRANDED_FOOTER)

@admin_required
async def cmd_mute(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Usage: /mute <@username|ID> [time]")); return
    uid,name=tgt
    if await is_admin(update,context,uid): await update.effective_message.reply_text("Can't mute admin"+BRANDED_FOOTER); return
    until=None; cmd=update.effective_message.text.split()[0].lstrip("/").lower().split("@")[0]
    if context.args and len(context.args)>1 and parse_duration(context.args[-1]): until=datetime.datetime.now(datetime.timezone.utc)+parse_duration(context.args[-1])
    elif context.args and parse_duration(context.args[0]): until=datetime.datetime.now(datetime.timezone.utc)+parse_duration(context.args[0])
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id,uid,permissions=ChatPermissions(can_send_messages=False),until_date=until)
        await update.effective_message.reply_html(branded(f"🔇 Muted {mention_html(uid,name)}"))
        await log_to_support(context,f"MUTE {uid} until {until}",update.effective_chat.id,update.effective_user.id,uid)
        if cmd in ("smute","tmute"):
            try: await update.effective_message.delete()
            except: pass
        elif cmd=="dmute":
            try: await update.effective_message.reply_to_message.delete()
            except: pass
    except Exception as e: await update.effective_message.reply_text(f"Mute failed: {e}"+BRANDED_FOOTER)

@admin_required
async def cmd_unmute(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Usage: /unmute <@username|ID>")); return
    uid,name=tgt
    try: await context.bot.restrict_chat_member(update.effective_chat.id,uid,permissions=ChatPermissions(can_send_messages=True,can_send_media_messages=True,can_send_other_messages=True,can_add_web_page_previews=True)); await update.effective_message.reply_html(branded(f"🔊 Unmuted {mention_html(uid,name)}")); await log_to_support(context,f"UNMUTE {uid}",update.effective_chat.id,update.effective_user.id,uid)
    except Exception as e: await update.effective_message.reply_text(f"Unmute failed: {e}"+BRANDED_FOOTER)

@admin_required
async def cmd_kick(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Usage: /kick <@username|ID>")); return
    uid,name=tgt
    if await is_admin(update,context,uid): await update.effective_message.reply_text("Can't kick admin"+BRANDED_FOOTER); return
    try: await context.bot.ban_chat_member(update.effective_chat.id,uid); await context.bot.unban_chat_member(update.effective_chat.id,uid); await update.effective_message.reply_html(branded(f"👢 Kicked {mention_html(uid,name)}")); await log_to_support(context,f"KICK {uid}",update.effective_chat.id,update.effective_user.id,uid)
    except Exception as e: await update.effective_message.reply_text(f"Kick failed: {e}"+BRANDED_FOOTER)

async def cmd_promote(update,context):
    # allow via resolver + log
    if not await is_admin(update,context): await update.effective_message.reply_text("🚫 Only admins."+BRANDED_FOOTER); return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Reply or /promote <@username|ID>")); return
    uid,name=tgt
    try: await context.bot.promote_chat_member(update.effective_chat.id,uid,can_delete_messages=True,can_restrict_members=True,can_pin_messages=True); await update.effective_message.reply_html(branded(f"⬆️ Promoted {mention_html(uid,name)}")); await log_to_support(context,f"PROMOTE {uid}",update.effective_chat.id,update.effective_user.id,uid)
    except Exception as e: await update.effective_message.reply_text(f"Promote failed: {e}"+BRANDED_FOOTER)

async def cmd_demote(update,context):
    if not await is_admin(update,context): await update.effective_message.reply_text("🚫 Only admins."+BRANDED_FOOTER); return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Reply or /demote <@username|ID>")); return
    uid,name=tgt
    try: await context.bot.promote_chat_member(update.effective_chat.id,uid,can_delete_messages=False,can_restrict_members=False,can_pin_messages=False); await update.effective_message.reply_html(branded(f"⬇️ Demoted {mention_html(uid,name)}")); await log_to_support(context,f"DEMOTE {uid}",update.effective_chat.id,update.effective_user.id,uid)
    except Exception as e: await update.effective_message.reply_text(f"Demote failed: {e}"+BRANDED_FOOTER)

@admin_required
async def cmd_warn(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Usage: /warn <@username|ID> [reason]")); return
    uid,name=tgt
    if await is_admin(update,context,uid): await update.effective_message.reply_text("Can't warn admin"+BRANDED_FOOTER); return
    reason=" ".join(context.args[1:]) if len(context.args)>1 else "No reason"
    count=await db.add_warn(update.effective_chat.id,uid,reason)
    limit,mode=await db.get_warns_settings(update.effective_chat.id)
    await update.effective_message.reply_html(branded(f"⚠️ Warned {mention_html(uid,name)} ({count}/{limit}) <i>{esc(reason)}</i>"))
    await log_to_support(context,f"WARN {uid} {count}/{limit} {reason}",update.effective_chat.id,update.effective_user.id,uid,reason)
    if count>=limit:
        try:
            if mode=="ban": await context.bot.ban_chat_member(update.effective_chat.id,uid); await context.bot.send_message(update.effective_chat.id,branded(f"🚫 {mention_html(uid,name)} reached {limit} warns → <b>banned</b>"),parse_mode=ParseMode.HTML)
            elif mode=="kick": await context.bot.ban_chat_member(update.effective_chat.id,uid); await context.bot.unban_chat_member(update.effective_chat.id,uid)
            elif mode in ("mute","tmute"): await context.bot.restrict_chat_member(update.effective_chat.id,uid,permissions=ChatPermissions(can_send_messages=False))
            await db.reset_warns(update.effective_chat.id,uid)
            await log_to_support(context,f"AUTO {mode} after warns {uid}",update.effective_chat.id,uid,uid)
        except Exception as e: await update.effective_message.reply_text(f"Warn action failed: {e}"+BRANDED_FOOTER)

async def cmd_warns(update,context):
    tgt=await resolve_target(update,context)
    if tgt: uid,name=tgt
    elif update.effective_message.reply_to_message and update.effective_message.reply_to_message.from_user: uid=update.effective_message.reply_to_message.from_user.id; name=update.effective_message.reply_to_message.from_user.full_name
    else: uid=update.effective_user.id; name=update.effective_user.full_name
    c=await db.get_warns(update.effective_chat.id,uid); limit,_=await db.get_warns_settings(update.effective_chat.id)
    await update.effective_message.reply_html(branded(f"📋 {mention_html(uid,name)} has {c}/{limit} warns"))
@admin_required
async def cmd_rmwarn(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Reply or /rmwarn <@username|ID>")); return
    uid,name=tgt; newc=await db.rm_warn(update.effective_chat.id,uid); await update.effective_message.reply_html(branded(f"✅ Removed warn for {mention_html(uid,name)} → {newc}"))
@admin_required
async def cmd_resetwarn(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Usage: /resetwarn <@username|ID>")); return
    uid,name=tgt; await db.reset_warns(update.effective_chat.id,uid); await update.effective_message.reply_html(branded(f"✅ Reset warns for {mention_html(uid,name)}"))
@admin_required
async def cmd_resetallwarns(update,context): await db.reset_all_warns(update.effective_chat.id); await update.effective_message.reply_text(branded("✅ All warns reset"))
async def cmd_warnings(update,context): limit,mode=await db.get_warns_settings(update.effective_chat.id); await update.effective_message.reply_html(branded(f"⚙️ Warn limit: <b>{limit}</b>\nWarn mode: <b>{esc(mode)}</b>"))
@admin_required
async def cmd_warnmode(update,context):
    if not context.args: limit,mode=await db.get_warns_settings(update.effective_chat.id); await update.effective_message.reply_html(branded(f"Warn mode: {esc(mode)}")); return
    mode=context.args[0].lower()
    if mode not in ("ban","kick","mute","tban","tmute"): await update.effective_message.reply_text("Valid: ban/kick/mute/tban/tmute"+BRANDED_FOOTER); return
    await db.set_warn_mode(update.effective_chat.id,mode); await update.effective_message.reply_text(branded(f"Warn mode set to {mode}"))
@admin_required
async def cmd_warnlimit(update,context):
    if not context.args: limit,_=await db.get_warns_settings(update.effective_chat.id); await update.effective_message.reply_text(branded(f"Warn limit: {limit}")); return
    try: n=int(context.args[0]); await db.set_warn_limit(update.effective_chat.id,n); await update.effective_message.reply_text(branded(f"Warn limit set to {n}"))
    except: await update.effective_message.reply_text("Usage: /warnlimit 3"+BRANDED_FOOTER)
async def cmd_adminlist(update,context):
    try: admins=await context.bot.get_chat_administrators(update.effective_chat.id); txt="👮 <b>Admins:</b>\n"+"\n".join(f"• {mention_html(a.user.id,a.user.full_name)} — {a.status}" for a in admins); await update.effective_message.reply_html(branded(txt))
    except Exception as e: await update.effective_message.reply_text(f"Failed: {e}"+BRANDED_FOOTER)
async def cmd_admincache(update,context): await update.effective_message.reply_text(branded("✅ Admin cache refreshed"))
@admin_required
async def cmd_anonadmin(update,context):
    if not context.args: await update.effective_message.reply_text(branded(f"Anon admin: {await db.get_settings(update.effective_chat.id)}")); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"anonadmin",val); await update.effective_message.reply_text(branded(f"Anon admin {'enabled' if val else 'disabled'}"))

# ── Approve (alias trust) ──
async def cmd_approve(update,context):
    if not await is_admin(update,context): await update.effective_message.reply_text("🚫 Only admins."+BRANDED_FOOTER); return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Reply or /approve <@username|ID>")); return
    uid,name=tgt; await db.trust(update.effective_chat.id,uid,update.effective_user.id); await update.effective_message.reply_html(branded(f"✅ Approved {mention_html(uid,name)}")); await log_to_support(context,f"APPROVE {uid}",update.effective_chat.id,update.effective_user.id,uid)
async def cmd_unapprove(update,context):
    if not await is_admin(update,context): await update.effective_message.reply_text("🚫 Only admins."+BRANDED_FOOTER); return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Reply or /unapprove <@username|ID>")); return
    uid,name=tgt; await db.untrust(update.effective_chat.id,uid); await update.effective_message.reply_html(branded(f"❌ Unapproved {mention_html(uid,name)}")); await log_to_support(context,f"UNAPPROVE {uid}",update.effective_chat.id,update.effective_user.id,uid)

# ── Rules / Notes ──
async def cmd_rules(update,context):
    c=await db.get_rules(update.effective_chat.id)
    if not c: await update.effective_message.reply_text(branded("No rules. Use /setrules")); return
    if await db.get_privaterules(update.effective_chat.id):
        try: await context.bot.send_message(update.effective_user.id,branded(f"📜 <b>Rules for {esc(update.effective_chat.title)}</b>:\n\n{c}"),parse_mode=ParseMode.HTML); await update.effective_message.reply_text(branded("Rules sent in private")); return
        except: pass
    await update.effective_message.reply_html(branded(c))
@admin_required
async def cmd_setrules(update,context):
    txt=" ".join(context.args) if context.args else (update.effective_message.reply_to_message.text if update.effective_message.reply_to_message and update.effective_message.reply_to_message.text else "")
    if not txt: await update.effective_message.reply_text(branded("Usage: /setrules <text>")); return
    await db.set_rules(update.effective_chat.id,txt); await update.effective_message.reply_text(branded("✅ Rules updated")); await log_to_support(context,"SET RULES",update.effective_chat.id,update.effective_user.id)
@admin_required
async def cmd_privaterules(update,context):
    if not context.args: await update.effective_message.reply_text(branded(f"Private: {await db.get_privaterules(update.effective_chat.id)}")); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_privaterules(update.effective_chat.id,val); await update.effective_message.reply_text(branded(f"Private rules {'on' if val else 'off'}"))
@admin_required
async def cmd_resetrules(update,context): await db.reset_rules(update.effective_chat.id); await update.effective_message.reply_text(branded("✅ Rules reset"))
@admin_required
async def cmd_setnote(update,context):
    if not context.args: await update.effective_message.reply_text(branded("Usage: /setnote <name> <text>")); return
    name=context.args[0]
    if update.effective_message.reply_to_message and (update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption): content=update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption
    else:
        if len(context.args)<2: await update.effective_message.reply_text(branded("Provide content or reply")); return
        content=" ".join(context.args[1:])
    await db.set_note(update.effective_chat.id,name,content,update.effective_user.id); await update.effective_message.reply_html(branded(f"💾 Note <code>#{esc(name.lower())}</code> saved"))
async def cmd_getnote(update,context):
    if not context.args:
        notes=await db.list_notes(update.effective_chat.id)
        if not notes: await update.effective_message.reply_text(branded("No notes. Use /setnote")); return
        await update.effective_message.reply_html(branded("🗒 <b>Notes:</b>\n"+"\n".join(f"• <code>#{esc(n)}</code>" for n in notes))); return
    name=context.args[0].lstrip("#"); c=await db.get_note(update.effective_chat.id,name)
    if not c: await update.effective_message.reply_text(branded("Not found")); return
    await update.effective_message.reply_html(branded(c))
@admin_required
async def cmd_delnote(update,context):
    if not context.args: await update.effective_message.reply_text(branded("Usage: /delnote <name>")); return
    name=context.args[0].lstrip("#"); cnt=await db.del_note(update.effective_chat.id,name); await update.effective_message.reply_text(branded("Deleted" if cnt else "Not found"))
async def hashtag_note_handler(update,context):
    if not update.effective_message or not update.effective_message.text: return
    m=re.match(r"^#(\w+)\b",update.effective_message.text.strip())
    if not m: return
    c=await db.get_note(update.effective_chat.id,m.group(1))
    if c: await update.effective_message.reply_html(branded(c))
@admin_required
async def cmd_trust(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Reply or /trust <@username|ID>")); return
    uid,name=tgt; await db.trust(update.effective_chat.id,uid,update.effective_user.id); await update.effective_message.reply_html(branded(f"✅ Trusted {mention_html(uid,name)}"))
@admin_required
async def cmd_untrust(update,context):
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text(branded("Reply or /untrust <@username|ID>")); return
    uid,name=tgt; await db.untrust(update.effective_chat.id,uid); await update.effective_message.reply_html(branded(f"❌ Untrusted {mention_html(uid,name)}"))

# ── Vault ──
@owner_required
async def cmd_addbanword(update,context):
    if not context.args: await update.effective_message.reply_text(branded("Usage: /addbword <word>")); return
    phrase=" ".join(context.args); await db.add_global_word(phrase,update.effective_user.id); await update.effective_message.reply_html(branded(f"🔒 Added <code>{esc(phrase.lower())}</code>")); await log_to_support(context,f"ADD VAULT {phrase}",None,update.effective_user.id)
@owner_required
async def cmd_rmbanword(update,context):
    if not context.args: await update.effective_message.reply_text(branded("Usage: /rmbword <word>")); return
    phrase=" ".join(context.args); cnt=await db.remove_global_word(phrase); await update.effective_message.reply_text(branded("Removed" if cnt else "Not found"))
@owner_required
async def cmd_listbanwords(update,context):
    words=await db.list_global_words()
    if not words: await update.effective_message.reply_text(branded("Vault empty")); return
    txt="\n".join(f"• <code>{esc(w)}</code>" for w in words[:100])
    await update.effective_message.reply_html(branded(f"🌐 <b>Vault ({len(words)}):</b>\n{txt}"))

# ── Locks ──
@admin_required
async def cmd_lock(update,context):
    if not context.args: await update.effective_message.reply_text(branded("Usage: /lock <type>")); return
    for t in context.args:
        if t.lower() in LOCK_TYPES: await db.set_lock(update.effective_chat.id,t.lower(),True)
    await update.effective_message.reply_text(branded(f"🔒 Locked: {', '.join(context.args)}")); await log_to_support(context,f"LOCK {context.args}",update.effective_chat.id,update.effective_user.id)
@admin_required
async def cmd_unlock(update,context):
    if not context.args: await update.effective_message.reply_text(branded("Usage: /unlock <type>")); return
    for t in context.args:
        if t.lower() in LOCK_TYPES: await db.set_lock(update.effective_chat.id,t.lower(),False)
    await update.effective_message.reply_text(branded(f"🔓 Unlocked: {', '.join(context.args)}"))
@admin_required
async def cmd_locks_panel(update,context):
    s=await db.get_settings(update.effective_chat.id); locks=await db.list_locks(update.effective_chat.id)
    kb=[
        [InlineKeyboardButton(f"{'✅' if await db.is_locked(update.effective_chat.id,'url') else '❌'} Link",callback_data="toggle_lock_url"), InlineKeyboardButton(f"{'✅' if await db.is_locked(update.effective_chat.id,'sticker') else '❌'} Sticker",callback_data="toggle_lock_sticker")],
        [InlineKeyboardButton(f"{'✅' if s['antispam'] else '❌'} AntiSpam",callback_data="toggle_antispam"), InlineKeyboardButton(f"{'✅' if s['ocr_protection'] else '❌'} OCR",callback_data="toggle_ocr_protection")],
        [InlineKeyboardButton(f"{'✅' if s['captcha_enabled'] else '❌'} CAPTCHA",callback_data="toggle_captcha_enabled"), InlineKeyboardButton("Close",callback_data="close_locks")]
    ]
    await update.effective_message.reply_html(branded(f"🔐 <b>Locks — {esc(update.effective_chat.title or '')}</b>\nLocked: {', '.join(locks) if locks else 'none'}"),reply_markup=InlineKeyboardMarkup(kb))
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
async def cmd_locktypes(update,context): await update.effective_message.reply_text(branded("Lock types: "+", ".join(LOCK_TYPES)))
async def cmd_lockwarns(update,context):
    if not context.args: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(branded(f"lockwarns: {'on' if s['lockwarns'] else 'off'}")); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"lockwarns",val); await update.effective_message.reply_text(branded(f"lockwarns {'on' if val else 'off'}"))
@admin_required
async def cmd_allowlist(update,context):
    if not context.args: items=await db.list_allow(update.effective_chat.id); await update.effective_message.reply_text(branded("Allowlist: "+", ".join(items) if items else "Empty")); return
    for it in context.args: await db.add_allow(update.effective_chat.id,it)
    await update.effective_message.reply_text(branded("Added: "+", ".join(context.args)))
@admin_required
async def cmd_rmallowlist(update,context):
    if not context.args: await update.effective_message.reply_text(branded("Usage: /rmallowlist <url>")); return
    for it in context.args: await db.rm_allow(update.effective_chat.id,it)
    await update.effective_message.reply_text(branded("Removed"))
@admin_required
async def cmd_rmallowlistall(update,context): await db._exec("DELETE FROM allowlist WHERE chat_id=?",(update.effective_chat.id,)); await update.effective_message.reply_text(branded("✅ Allowlist cleared"))
@admin_required
async def cmd_whitelist(update,context): await cmd_allowlist(update,context)
@admin_required
async def cmd_blocklist(update,context):
    if not context.args: items=await db.list_blocklist(update.effective_chat.id); await update.effective_message.reply_text(branded("Blocklist: "+", ".join(items) if items else "Empty")); return
    for w in context.args: await db.add_blocklist(update.effective_chat.id,w)
    await update.effective_message.reply_text(branded("Added to blocklist"))
@admin_required
async def cmd_rmblocklist(update,context):
    if not context.args: await update.effective_message.reply_text(branded("Usage: /rmblocklist <word>")); return
    for w in context.args: await db.rm_blocklist(update.effective_chat.id,w)
    await update.effective_message.reply_text(branded("Removed"))
@admin_required
async def cmd_setflood(update,context):
    if not context.args: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(branded(f"Flood limit: {s['flood_limit']}")); return
    try: n=int(context.args[0]); await db.set_setting(update.effective_chat.id,"flood_limit",n); await update.effective_message.reply_text(branded(f"Flood limit {n}"))
    except: await update.effective_message.reply_text(branded("Usage: /setflood 5"))
@admin_required
async def cmd_setfloodmode(update,context):
    if not context.args: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(branded(f"Flood mode: {s['flood_mode']}")); return
    mode=" ".join(context.args); await db.set_setting(update.effective_chat.id,"flood_mode",mode); await update.effective_message.reply_text(branded(f"Flood mode {mode}"))
async def cmd_cleanwelcome(update,context):
    if context.args: val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"cleanwelcome",val); await update.effective_message.reply_text(branded(f"cleanwelcome {'on' if val else 'off'}"))
    else: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(branded(f"cleanwelcome: {s['cleanwelcome']}"))
async def cmd_cleanservice(update,context):
    if context.args: val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"cleanservice",val); await update.effective_message.reply_text(branded(f"cleanservice {'on' if val else 'off'}"))
    else: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(branded(f"cleanservice: {s['cleanservice']}"))

# ── Support Log Commands ──
async def cmd_support(update,context):
    sup=await get_support_chat_id()
    status=f"Log Channel: <code>{sup}</code>" if sup else "Log Channel: not set (env SUPPORT_CHAT_ID or /setlog)"
    await update.effective_message.reply_html(branded(f"🆘 <b>Support — {esc(BRAND_NAME)}</b>\nLink: {esc(SUPPORT_LINK)}\n{status}\n\nAdmin actions are logged there."))
@admin_required
async def cmd_setlog(update,context):
    if not context.args: await update.effective_message.reply_text(branded(f"Usage: /setlog <chat_id> e.g. -100123...\nCurrent: {await get_support_chat_id()}")); return
    val=context.args[0]
    if not val.lstrip("-").isdigit(): await update.effective_message.reply_text(branded("Invalid ID")); return
    await db.set_config("SUPPORT_CHAT_ID",val); await update.effective_message.reply_html(branded(f"✅ Log channel set to <code>{esc(val)}</code> — logs will go there."))

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
    await log_to_support(context,f"ILLEGAL delete {reason} msg:{(msg.text or msg.caption or '')[:80]}",chat.id,update.effective_user.id if update.effective_user else None,None,reason)

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
    if await db.is_allowed(chat_id,text): return
    m=await db.check_global(text)
    if m: await do_illegal(update,context,f"global:{m}"); return
    b=await db.is_blocklisted(text,chat_id)
    if b: await do_illegal(update,context,f"blocklist:{b}"); return
    low=text.lower()
    for bad in OCR_BLACKLIST:
        if bad.lower() in low: await do_illegal(update,context,f"blacklist:{bad}"); return
    if LINK_RE.search(text) and (await db.is_locked(chat_id,"url") or s["antilink"]):
        if not await db.is_allowed(chat_id,text):
            try: await msg.delete()
            except: pass
            if s["lockwarns"]: await db.add_warn(chat_id,update.effective_user.id,"locked link")
            await send_temp_notice(context,chat_id,"🔗 Links not allowed"+BRANDED_FOOTER)
            return
    if EMAIL_RE.search(text) and await db.is_locked(chat_id,"email"):
        try: await msg.delete()
        except: pass
        await send_temp_notice(context,chat_id,"📧 Emails locked"+BRANDED_FOOTER); return
    if PHONE_RE.search(text) and await db.is_locked(chat_id,"phone"):
        try: await msg.delete()
        except: pass
        await send_temp_notice(context,chat_id,"📞 Phones locked"+BRANDED_FOOTER); return
    if msg.forward_from and await db.is_locked(chat_id,"forward"):
        try: await msg.delete()
        except: pass
        await send_temp_notice(context,chat_id,"🔁 Forwards locked"+BRANDED_FOOTER); return
    if SPAM_RE.search(text) and s["antispam"]:
        try: await msg.delete()
        except: pass
        await send_temp_notice(context,chat_id,"🚫 Spam deleted"+BRANDED_FOOTER); return
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
            await send_temp_notice(context,chat_id,"🚨 Flood muted"+BRANDED_FOOTER)
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
        await send_temp_notice(context,update.effective_chat.id,"🖼️ Media locked"+BRANDED_FOOTER); return
    data=None
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
    if update.effective_message and (update.effective_message.new_chat_members or update.effective_message.left_chat_member):
        s=await db.get_settings(update.effective_chat.id)
        if s["cleanservice"]:
            try: await update.effective_message.delete()
            except: pass

async def greet_new_members(update,context):
    msg=update.effective_message; chat=update.effective_chat
    if not msg or not msg.new_chat_members or chat.type=="private": return
    s=await db.get_settings(chat.id)
    wtext,gtext,won,gon=await db.get_greetings(chat.id)
    for member in msg.new_chat_members:
        if member.is_bot: continue
        if won and s["welcome_on"]:
            try:
                cnt=await context.bot.get_chat_member_count(chat.id)
                fill=fill_welcome(wtext,member,chat,cnt)
                sent=await msg.reply_html(branded(fill))
                if s["cleanwelcome"]:
                    old=await db._exec("SELECT welcome_clean_msg_id FROM chat_settings WHERE chat_id=?",(chat.id,),fetch="one")
                    if old and old[0]:
                        try: await context.bot.delete_message(chat.id, old[0])
                        except: pass
                    await db._exec("UPDATE chat_settings SET welcome_clean_msg_id=? WHERE chat_id=?",(sent.message_id,chat.id))
                await log_to_support(context,f"JOIN {member.id} {member.full_name}",chat.id,member.id)
            except: pass
        if not s["captcha_enabled"]: continue
        try:
            await context.bot.restrict_chat_member(chat.id,member.id,permissions=ChatPermissions(can_send_messages=False,can_send_media_messages=False,can_send_other_messages=False,can_add_web_page_previews=False))
        except: continue
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify You Are Human",callback_data=f"captcha_{member.id}")]])
        try: sent2=await msg.reply_html(branded(f"👋 Welcome {mention_html(member.id,member.full_name)}! Verify in <b>{CAPTCHA_TIMEOUT//60} min</b>"),reply_markup=kb)
        except: continue
        async def captcha_job(ctx):
            data=ctx.job.data if ctx.job else {}
            c_id=data.get("chat_id",chat.id); u_id=data.get("user_id",member.id); mid=data.get("msg_id",sent2.message_id)
            try:
                m=await ctx.bot.get_chat_member(c_id,u_id)
                if m.status==ChatMemberStatus.RESTRICTED or (c_id,u_id) in pending_captcha:
                    if (c_id,u_id) in pending_captcha:
                        try: await ctx.bot.ban_chat_member(c_id,u_id); await ctx.bot.unban_chat_member(c_id,u_id); await ctx.bot.send_message(c_id,branded(f"⏰ {esc(member.full_name)} failed verification")); await log_to_support(ctx,f"CAPTCHA FAIL kick {u_id}",c_id,u_id)
                        except: pass
                        try: await ctx.bot.delete_message(c_id,mid)
                        except: pass
                        pending_captcha.pop((c_id,u_id),None)
            except: pass
        if context.job_queue:
            job=context.job_queue.run_once(captcha_job,CAPTCHA_TIMEOUT,data={"chat_id":chat.id,"user_id":member.id,"msg_id":sent2.message_id},name=f"captcha_{chat.id}_{member.id}")
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
    try: await q.edit_message_text(branded(f"✅ Verified — welcome {esc(update.effective_user.full_name)}!"))
    except: await q.answer("Verified!")

async def cmd_setwelcome(update,context):
    txt=" ".join(context.args) if context.args else (update.effective_message.reply_to_message.text if update.effective_message.reply_to_message else "")
    if not txt: await update.effective_message.reply_text(branded("Usage: /setwelcome <text> — {fullname} {mention} {id} {chatname} {count}")); return
    cid=await effective_chat_id(update,context); await db.set_welcome(cid,txt); await update.effective_message.reply_text(branded("✅ Welcome updated")); await log_to_support(context,"SET WELCOME",cid,update.effective_user.id,detail=txt[:80])
async def cmd_resetwelcome(update,context): cid=await effective_chat_id(update,context); await db.set_welcome(cid,"Hello {fullname}! Welcome to {chatname}!"); await update.effective_message.reply_text(branded("✅ Welcome reset"))
async def cmd_setgoodbye(update,context):
    txt=" ".join(context.args) if context.args else ""
    if not txt: await update.effective_message.reply_text(branded("Usage: /setgoodbye <text>")); return
    cid=await effective_chat_id(update,context); await db.set_goodbye(cid,txt); await update.effective_message.reply_text(branded("✅ Goodbye updated"))
async def cmd_resetgoodbye(update,context): cid=await effective_chat_id(update,context); await db.set_goodbye(cid,"Goodbye {fullname}!"); await update.effective_message.reply_text(branded("✅ Goodbye reset"))
async def cmd_welcome(update,context):
    if not context.args: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(branded(f"welcome: {'on' if s['welcome_on'] else 'off'}")); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"welcome_on",val); await update.effective_message.reply_text(branded(f"welcome {'on' if val else 'off'}"))
async def cmd_goodbye(update,context):
    if not context.args: s=await db.get_settings(update.effective_chat.id); await update.effective_message.reply_text(branded(f"goodbye: {'on' if s['goodbye_on'] else 'off'}")); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(update.effective_chat.id,"goodbye_on",val); await update.effective_message.reply_text(branded(f"goodbye {'on' if val else 'off'}"))

# ── Mass Mentions ──
@admin_required
async def cmd_all(update,context):
    custom=" ".join(context.args) if context.args else "Attention!"
    actives=await db.get_active_users(update.effective_chat.id,500)
    if not actives: await update.effective_message.reply_text(branded("No active pool — chat first")); return
    await update.effective_message.reply_text(branded(f"📢 Broadcasting to {len(actives)} in batches of {TAG_BATCH_SIZE}…"))
    for i in range(0,len(actives),TAG_BATCH_SIZE):
        batch=actives[i:i+TAG_BATCH_SIZE]; mentions=" ".join(mention_html(uid,fname) for uid,_,fname in batch)
        try: await context.bot.send_message(update.effective_chat.id,branded(f"📣 <b>{esc(custom)}</b>\n\n{mentions}"),parse_mode=ParseMode.HTML)
        except: await asyncio.sleep(2)
        if i+TAG_BATCH_SIZE < len(actives): await asyncio.sleep(TAG_BATCH_DELAY)
    await log_to_support(context,f"TAG ALL {len(actives)}",update.effective_chat.id,update.effective_user.id)
@admin_required
async def cmd_admins(update,context):
    custom=" ".join(context.args) if context.args else "Admins please attend!"
    try: admins=await context.bot.get_chat_administrators(update.effective_chat.id)
    except Exception as e: await update.effective_message.reply_text(branded(f"Failed {e}")); return
    human=[a for a in admins if not a.user.is_bot]
    if not human: await update.effective_message.reply_text(branded("No human admins")); return
    await update.effective_message.reply_text(branded(f"👮 Tagging {len(human)} admins…"))
    users=[(a.user.id,a.user.username,a.user.full_name) for a in human]
    for i in range(0,len(users),TAG_BATCH_SIZE):
        batch=users[i:i+TAG_BATCH_SIZE]; mentions=" ".join(mention_html(uid,fname) for uid,_,fname in batch)
        try: await context.bot.send_message(update.effective_chat.id,branded(f"👮 <b>{esc(custom)}</b>\n\n{mentions}"),parse_mode=ParseMode.HTML)
        except: pass
        if i+TAG_BATCH_SIZE < len(users): await asyncio.sleep(TAG_BATCH_DELAY)

# ── ShadowReply ──
@admin_required
async def cmd_shadowreply(update,context):
    payload=" ".join(context.args) if context.args else ""
    if not payload and update.effective_message.reply_to_message and (update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption): payload=update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption
    if not payload.strip(): await update.effective_message.reply_text(branded("Usage: /shadowreply <message>")); return
    try: await update.effective_message.delete()
    except:
        try: await context.bot.delete_message(update.effective_chat.id,update.effective_message.message_id)
        except: pass
    try: await context.bot.send_message(update.effective_chat.id,payload,parse_mode=ParseMode.HTML)
    except: await context.bot.send_message(update.effective_chat.id,payload)
    await log_to_support(context,f"SHADOW by {update.effective_user.id}: {payload[:60]}",update.effective_chat.id,update.effective_user.id)

# ── Wordle with Stack Emojify ──
async def cmd_newgame(update,context):
    chat=update.effective_chat
    existing=await db.get_game(chat.id)
    if existing:
        w,l,s,a,gj,bid=existing
        # get starter name
        try:
            starter_member=await context.bot.get_chat_member(chat.id,s)
            sname=starter_member.user.full_name
        except: sname=str(s)
        await update.effective_message.reply_html(branded(f"⚠️ <b>Another game is already in progress in this chat!</b>\nWord length: {l} | Starter: {esc(sname)} | Attempts: {a}\nFinish it or /endgame to start new"))
        return
    raw=update.effective_message.text.split()[0].split("@")[0].lstrip("/").lower() if update.effective_message.text else "new"
    length=5
    if raw in ("new4","word4"): length=4
    elif raw in ("new5","word5"): length=5
    elif raw in ("new6","word6"): length=6
    elif raw in ("new","newword","newgame","word","wordle"): length=5
    if context.args and context.args[0].isdigit() and int(context.args[0]) in (4,5,6): length=int(context.args[0])
    pool=WORD_BANK.get(length,WORD_BANK[5]); word=random.choice(pool)
    await db.start_game(chat.id,word,update.effective_user.id)
    board=Emojify.stack_emojify(word,[],length)
    await update.effective_message.reply_html(branded(f"🎲 <b>Word Jumble Started!</b> — {length} letters\nSend a {length}-letter word to guess. 🎯=perfect 🔀=misplaced 🚫=absent\n\n{board}"))
    await log_to_support(context,f"NEW GAME {length}L by {update.effective_user.id}",chat.id,update.effective_user.id,detail=word)

async def cmd_endgame(update,context):
    if not await is_admin(update,context): await update.effective_message.reply_text("🚫 Only admins can end game"+BRANDED_FOOTER); return
    g=await db.get_game(update.effective_chat.id)
    if not g: await update.effective_message.reply_text(branded("No active game")); return
    w,l,s,a,gj,bid=g
    await db.stop_game(update.effective_chat.id)
    await update.effective_message.reply_html(branded(f"🛑 Game ended. The word was <code>{esc(w.upper())}</code>"))
    await log_to_support(context,f"END GAME word={w}",update.effective_chat.id,update.effective_user.id)

async def cmd_gamestatus(update,context):
    g=await db.get_game(update.effective_chat.id)
    if not g: await update.effective_message.reply_text(branded("No game. /new /new4 /new5 /new6")); return
    w,l,s,a,gj,bid=g
    guesses=json.loads(gj or "[]")
    board=Emojify.stack_emojify(w,guesses,l)
    await update.effective_message.reply_html(branded(board + f"\n\n<b>Starter:</b> <code>{s}</code> | <b>Attempts:</b> {a}"))

async def cmd_top(update,context):
    rows=await db.top_players(10)
    if not rows: await update.effective_message.reply_text(branded("No wins yet. /new")); return
    lines=["🏆 <b>Top Players</b>"]+[f"{i}. {esc(uname or str(uid))} — {wins} win{'s' if wins!=1 else ''}" for i,(uid,uname,wins) in enumerate(rows,1)]
    await update.effective_message.reply_html(branded("\n".join(lines)))

async def handle_wordle(update,context):
    msg=update.effective_message
    if not msg or not msg.text or update.effective_chat.type=="private": return
    txt=msg.text.strip()
    if txt.startswith("/") or txt.startswith("#") or txt.startswith("@"): return
    if " " in txt or not txt.isalpha(): return
    g=await db.get_game(update.effective_chat.id)
    if not g: return
    secret,length,starter,attempts,gj,bid=g
    if len(txt)!=length: return
    guesses=json.loads(gj or "[]")
    # duplicate check
    lower_words=[gg['word'].lower() for gg in guesses]
    if txt.lower() in lower_words:
        orig=next(gg for gg in guesses if gg['word'].lower()==txt.lower())
        await msg.reply_html(branded(f"😅 Sorry, the word <code>{esc(txt.upper())}</code> was already tried first by {mention_html(orig['user_id'], orig['username'])}! Try a new word."))
        return
    scored=Emojify.score_word(secret,txt.lower())
    new_entry={"word":txt.lower(), "user_id":update.effective_user.id, "username":update.effective_user.full_name, "scored":scored}
    guesses.append(new_entry)
    await db.set_guesses(update.effective_chat.id, json.dumps(guesses))
    await db.bump_attempts(update.effective_chat.id)
    board=Emojify.stack_emojify(secret, guesses, length)
    if txt.lower()==secret.lower():
        await db.add_win(update.effective_user.id,update.effective_user.username); await db.stop_game(update.effective_chat.id)
        await msg.reply_html(branded(board + f"\n\n🎉 <b>Correct!</b> {mention_html(update.effective_user.id,update.effective_user.full_name)} guessed <code>{esc(secret.upper())}</code> in {len(guesses)} tries!"))
        await log_to_support(context,f"WORDLE WIN {secret} by {update.effective_user.id}",update.effective_chat.id,update.effective_user.id)
    else:
        await db.add_played(update.effective_user.id,update.effective_user.username)
        # edit or send stacked board
        # if board_msg_id exists, try edit, else send new
        try:
            if bid:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=bid, text=branded(board), parse_mode=ParseMode.HTML)
                # also reply ephemeral?
                # no need to spam, board updated
                return
        except: pass
        sent=await msg.reply_html(branded(board))
        try: await db.set_board_msg(update.effective_chat.id, sent.message_id)
        except: pass
        # check max attempts
        if len(guesses)>=6:
            await db.stop_game(update.effective_chat.id)
            await context.bot.send_message(update.effective_chat.id, branded(f"💀 Game over! The word was <code>{esc(secret.upper())}</code>"), parse_mode=ParseMode.HTML)
            await log_to_support(context,f"WORDLE LOSS word={secret}",update.effective_chat.id,update.effective_user.id)

# ── Connections / Disable / Other ──
async def cmd_connect(update,context):
    if update.effective_chat.type!="private": await update.effective_message.reply_text(branded("Use in private with bot")); return
    if not context.args:
        cid=await db.get_connection(update.effective_user.id)
        if cid: await update.effective_message.reply_text(branded(f"Connected to <code>{cid}</code>. /disconnect to leave"))
        else: await update.effective_message.reply_text(branded("Usage: /connect <chat_id>")); return
    arg=context.args[0]
    try: cid=int(arg)
    except: await update.effective_message.reply_text(branded("Pass chat_id")); return
    try: m=await context.bot.get_chat_member(cid,update.effective_user.id)
    except: await update.effective_message.reply_text(branded("Not in chat or bot not there")); return
    if m.status not in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER) and not await is_owner(update.effective_user.id):
        await update.effective_message.reply_text(branded("Only admins can connect")); return
    await db.set_connection(update.effective_user.id,cid); await update.effective_message.reply_text(branded(f"✅ Connected to <code>{cid}</code>"))
async def cmd_disconnect(update,context): await db._exec("DELETE FROM connections WHERE user_id=?",(update.effective_user.id,)); await update.effective_message.reply_text(branded("🔌 Disconnected"))
@admin_required
async def cmd_disable(update,context):
    if not context.args: items=await db.list_disabled(update.effective_chat.id); await update.effective_message.reply_text(branded("Disabled: "+", ".join(items) if items else "None")); return
    for c in context.args: await db.disable_cmd(update.effective_chat.id,c.lower())
    await update.effective_message.reply_text(branded("Disabled: "+", ".join(context.args)))
@admin_required
async def cmd_enable(update,context):
    if not context.args: await update.effective_message.reply_text(branded("Usage: /enable <command>")); return
    for c in context.args: await db.enable_cmd(update.effective_chat.id,c.lower())
    await update.effective_message.reply_text(branded("Enabled: "+", ".join(context.args)))
async def cmd_setlang(update,context): await update.effective_message.reply_text(branded("✅ Language set"))
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
    await context.bot.send_document(chat.id,bio,caption=branded(f"Exported {', '.join(cats)}"))
async def cmd_import(update,context):
    msg=update.effective_message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text(branded("Reply to JSON file with /import")); return
    try: f=await context.bot.get_file(msg.reply_to_message.document.file_id); bio=io.BytesIO(); await f.download_to_memory(bio); data=json.loads(bio.getvalue().decode())
    except Exception as e: await msg.reply_text(branded(f"Import failed: {e}")); return
    chat=update.effective_chat
    if "notes" in data:
        for n in data["notes"]: await db.set_note(chat.id,n["name"],n["content"],update.effective_user.id)
    if "rules" in data and data["rules"]: await db.set_rules(chat.id,data["rules"])
    await msg.reply_text(branded("✅ Imported"))

# ── Utility ──
async def cmd_start(update,context):
    await update.effective_message.reply_html(branded("✨ <b>Miss Sona — Your Ultimate group Guardian</b> ✨\n\n industrial modules ai backed bot.\n<b>Mod:</b> /ban /tban /mute /warn /promote /adminlist (all via reply/@ID)\n<b>Approve:</b> /approve /unapprove (via reply/@ID)\n<b>Locks:</b> /lock /unlock /locks /allowlist /setflood\n<b>Welcome:</b> /setwelcome {fullname} /welcome on + CAPTCHA ✅\n<b>Vault:</b> /addbword /listbwords (owner)\n<b>Broadcast:</b> /all /admins 5/3s\n<b>Shadow:</b> /shadowreply\n<b>Game:</b> /new /new4 /new5 /new6 /game /top — 📚 Stack Emojify Board\n<b>Support:</b> /support /setlog (logs all actions)\n<b>Utility:</b> /connect (DM), /export /import, /disable"))
async def cmd_help(update,context): await cmd_start(update,context)
async def cmd_id(update,context):
    msg=update.effective_message; chat=update.effective_chat; user=update.effective_user
    lines=[f"👤 You: <code>{user.id}</code> — {esc(user.full_name)}"]
    # resolve target showcase
    tgt=await resolve_target(update,context)
    if tgt: lines.append(f"🎯 Target: <code>{tgt[0]}</code> — {esc(tgt[1])}")
    if chat and chat.type!="private": lines.append(f"💬 Chat: <code>{chat.id}</code> — {esc(chat.title or chat.type)}")
    if msg.reply_to_message and msg.reply_to_message.from_user: u=msg.reply_to_message.from_user; lines.append(f"↩️ Reply: <code>{u.id}</code> — {esc(u.full_name)}")
    lines.append(f"\n<i>Resolve via reply, <code>@username</code> or numeric ID — {esc(BRAND_TAG)}</i>")
    await msg.reply_html(branded("\n".join(lines)))

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
    app.add_handler(CommandHandler(["ban","dban","sban","tban"], cmd_ban), group=G_MAIN)
    app.add_handler(CommandHandler("unban", cmd_unban), group=G_MAIN)
    app.add_handler(CommandHandler(["mute","dmute","smute","tmute"], cmd_mute), group=G_MAIN)
    app.add_handler(CommandHandler("unmute", cmd_unmute), group=G_MAIN)
    app.add_handler(CommandHandler(["kick","dkick","skick"], cmd_kick), group=G_MAIN)
    app.add_handler(CommandHandler("promote", cmd_promote), group=G_MAIN)
    app.add_handler(CommandHandler("demote", cmd_demote), group=G_MAIN)
    app.add_handler(CommandHandler(["approve","unapprove"], lambda u,c: cmd_approve(u,c) if u.effective_message.text.startswith("/approve") else cmd_unapprove(u,c)), group=G_MAIN)
    app.add_handler(CommandHandler("approve", cmd_approve), group=G_MAIN)
    app.add_handler(CommandHandler("unapprove", cmd_unapprove), group=G_MAIN)
    app.add_handler(CommandHandler(["warn","dwarn","swarn"], cmd_warn), group=G_MAIN)
    app.add_handler(CommandHandler(["warns","warnings"], cmd_warns), group=G_MAIN)
    app.add_handler(CommandHandler("rmwarn", cmd_rmwarn), group=G_MAIN)
    app.add_handler(CommandHandler("resetwarn", cmd_resetwarn), group=G_MAIN)
    app.add_handler(CommandHandler("resetallwarns", cmd_resetallwarns), group=G_MAIN)
    app.add_handler(CommandHandler("warnings", cmd_warnings), group=G_MAIN)
    app.add_handler(CommandHandler("warnmode", cmd_warnmode), group=G_MAIN)
    app.add_handler(CommandHandler("warnlimit", cmd_warnlimit), group=G_MAIN)
    app.add_handler(CommandHandler("adminlist", cmd_adminlist), group=G_MAIN)
    app.add_handler(CommandHandler("admincache", cmd_admincache), group=G_MAIN)
    app.add_handler(CommandHandler("anonadmin", cmd_anonadmin), group=G_MAIN)
    app.add_handler(CommandHandler("rules", cmd_rules), group=G_MAIN)
    app.add_handler(CommandHandler("setrules", cmd_setrules), group=G_MAIN)
    app.add_handler(CommandHandler("privaterules", cmd_privaterules), group=G_MAIN)
    app.add_handler(CommandHandler("resetrules", cmd_resetrules), group=G_MAIN)
    app.add_handler(CommandHandler(["setnote","note","savenote","addnote"], cmd_setnote), group=G_MAIN)
    app.add_handler(CommandHandler(["getnote","notes"], cmd_getnote), group=G_MAIN)
    app.add_handler(CommandHandler("delnote", cmd_delnote), group=G_MAIN)
    app.add_handler(CommandHandler("trust", cmd_trust), group=G_MAIN)
    app.add_handler(CommandHandler("untrust", cmd_untrust), group=G_MAIN)
    app.add_handler(CommandHandler(["addbword","addbanword","gbanword"], cmd_addbanword), group=G_MAIN)
    app.add_handler(CommandHandler(["rmbword","delbword","removebanword"], cmd_rmbanword), group=G_MAIN)
    app.add_handler(CommandHandler(["listbwords","globalbans","listbans"], cmd_listbanwords), group=G_MAIN)
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
    app.add_handler(CommandHandler("setwelcome", cmd_setwelcome), group=G_MAIN)
    app.add_handler(CommandHandler("resetwelcome", cmd_resetwelcome), group=G_MAIN)
    app.add_handler(CommandHandler("setgoodbye", cmd_setgoodbye), group=G_MAIN)
    app.add_handler(CommandHandler("resetgoodbye", cmd_resetgoodbye), group=G_MAIN)
    app.add_handler(CommandHandler("welcome", cmd_welcome), group=G_MAIN)
    app.add_handler(CommandHandler("goodbye", cmd_goodbye), group=G_MAIN)
    app.add_handler(CommandHandler(["all","tagall","everyone","mentionall"], cmd_all), group=G_MAIN)
    app.add_handler(CommandHandler(["admins","admin","tagadmins"], cmd_admins), group=G_MAIN)
    app.add_handler(CommandHandler("shadowreply", cmd_shadowreply), group=G_MAIN)
    app.add_handler(CommandHandler(["new","new4","new5","new6","newword","newgame","word","wordle","word4","word5","word6"], cmd_newgame), group=G_MAIN)
    app.add_handler(CommandHandler(["endgame","stopgame"], cmd_endgame), group=G_MAIN)
    app.add_handler(CommandHandler(["game","gamestatus","status"], cmd_gamestatus), group=G_MAIN)
    app.add_handler(CommandHandler(["top","leaderboard","lb"], cmd_top), group=G_MAIN)
    app.add_handler(CommandHandler("connect", cmd_connect), group=G_MAIN)
    app.add_handler(CommandHandler("disconnect", cmd_disconnect), group=G_MAIN)
    app.add_handler(CommandHandler("disable", cmd_disable), group=G_MAIN)
    app.add_handler(CommandHandler("enable", cmd_enable), group=G_MAIN)
    app.add_handler(CommandHandler("setlang", cmd_setlang), group=G_MAIN)
    app.add_handler(CommandHandler("export", cmd_export), group=G_MAIN)
    app.add_handler(CommandHandler("import", cmd_import), group=G_MAIN)
    app.add_handler(CommandHandler("support", cmd_support), group=G_MAIN)
    app.add_handler(CommandHandler("setlog", cmd_setlog), group=G_MAIN)
    app.add_handler(CommandHandler(["start","help"], cmd_start), group=G_MAIN)
    app.add_handler(CommandHandler(["id","chatid","myid"], cmd_id), group=G_MAIN)
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^#\w+"), hashtag_note_handler), group=G_FB)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.ALL, handle_wordle), group=G_FB)
    app.add_error_handler(error_handler)
    return app

async def post_init(app): await db.init(); me=await app.bot.get_me(); log.info("🛡️ Miss Sona @%s id=%s — %s Support:%s WAL %s",me.username,me.id,BRAND_NAME,await get_support_chat_id(),DB_PATH)
async def post_shutdown(app): log.info("Shutting down Miss Sona — %s",BRAND_NAME)
def main():
    app=build_app(); app.post_init=post_init; app.post_shutdown=post_shutdown
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, close_loop=False)
if __name__=="__main__": main()
