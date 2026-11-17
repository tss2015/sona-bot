# Miss Sona — Telegram Group Management Bot

> **Industrial-grade, single-file, production-ready group guardian** merging Miss Rose-inspired elegance with hardcore moderation — built on `python-telegram-bot v20+` + `asyncio` + SQLite WAL.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![PTB](https://img.shields.io/badge/python--telegram--bot-v20%2B-0A9ED9)
![License](https://img.shields.io/badge/License-MIT-green)
![Single File](https://img.shields.io/badge/Build-single--file-gold)

---

## ✨ Features

### 1. Core Moderation & SQLite Persistence
| Command | Description | Role |
|---------|-------------|------|
| `/ban [reply/@user] [reason]` | Ban user from chat | Admin |
| `/unban` | Unban user | Admin |
| `/mute [duration 30m/2h/1d]` | Restrict send permission | Admin |
| `/unmute` | Restore permissions | Admin |
| `/warn [reason]` | Warn (3 strikes = auto-ban) | Admin |
| `/warns` | Check warning count (self or reply) | All |
| `/rules` | Show group rules | All |
| `/setrules <text>` | Set rules (HTML allowed) | Admin |
| `/setnote <name> \| <text>` | Save reference note (also `/note`) | Admin |
| `/note <name>` | Retrieve specific note | All |
| `/notes` | List all saved notes | All |
| `/delnote <name>` | Delete note | Admin |
| `/trust /untrust` | Exempt user from all automated filters | Admin |

> SQLite uses **WAL journal mode**, composite primary keys, and `asyncio.to_thread` for thread-safe, non-blocking I/O.

### 2. Advanced Security, Locks & AI-OCR Protection
- **Granular Protection Panel** — Link policies and content filters
- **Global Banned-Word Vault** (Owner-only, instant sync across *all* groups):
  - `/bannedwords` — View global vault list
  - `/addword <word1, word2>` — Add terms globally
  - `/delword <word>` — Remove terms globally
  - `/setwords <words>` — Replace entire vault atomically
  - `/clearbannedwords` — Empty the shared vault
- **AI-OCR & File Safety Pipeline** (`Pillow` + `Tesseract OCR`):
  - Scans incoming photos, images, and documents via `pytesseract.image_to_string`
  - Grayscale conversion and thumbnail scaling, threaded off the event loop
  - On content policy violation, prohibited keywords, or illegal file detection → **instant delete** + temporary notice:  
    > `I found something illegal so I deleted it`
  - Automatically handles repeat offenders with progressive strikes leading to an auto-ban

### 3. Interactive CAPTCHA & Security Gate
- New joiners auto-restricted (`can_send_messages=False`) upon entry
- Inline button `✅ Verify You Are Human` — **strict**: only the designated joiner can press it
- Timeout configured via `JobQueue` (with an `asyncio` fallback task) — kicks unverified users if ignored
- Activity tracking on every message to maintain an active user pool for announcements

### 4. Throttled Mass Mentions (Flood-Safe Broadcasts)
- `@all <text>` / `@admin <text>` — triggers mass notifications for active members or administrators
- **Paced delivery:** Sends mentions in strict batches (default 5 users) with a **3-second interval** between batches to entirely eliminate `FloodWait` risks

### 5. Shadow-Reply (Anonymous Admin Sending)
- `/shadowreply <message>` — instantly deletes the admin's command message and republishes the clean payload directly through the bot's own identity

### 6. Multiplayer Word Jumble / Wordle Game
- Commands `/new`, `/new4`, `/new5`, and `/new6` to start matching letter-length word guessing games
- Real-time duplicate-letter-aware emoji scoring for plain text guesses: `🎯` correct spot, `🔀` wrong spot, `🚫` absent
- `/game` — checks current game status
- `/top` — displays career win leaderboards per chat

---

## 📁 Project Structure

```text
miss-sona/
├── bot.py             # Main bot script — single-file, production-ready
├── requirements.txt   # Python dependencies
├── .env.example       # Env template (copy to .env)
├── .env               # Your secrets (gitignored)
├── .gitignore
├── README.md
└── bot_data.db        # Auto-created SQLite (WAL) — gitignored
```

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+
- A bot token from [@BotFather](https://t.me/BotFather)
- (Optional, for OCR) Tesseract binary:

```bash
# Debian/Ubuntu
sudo apt update && sudo apt install -y tesseract-ocr libtesseract-dev

# macOS
brew install tesseract

# Verify
tesseract --version
```

### 2. Install

```bash
git clone <your-repo> && cd miss-sona

# venv recommended
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
nano .env
```

```ini
BOT_TOKEN=1234567890:AAH_YourBotTokenHere
OWNER_IDS=123456789   # your Telegram user ID — @userinfobot
DB_PATH=bot_data.db
```

### 4. Run

```bash
python bot.py
```

#### Add to group
1. Add bot to your Telegram group
2. Promote to **Admin** with rights: `Delete Messages`, `Ban Users`, `Restrict Members`
3. Ensure privacy mode is disabled via `@BotFather` so the bot receives group messages

---

## ⚙️ Configuration Reference

| Env Var | Required | Default | Description |
|---------|----------|---------|-------------|
| `BOT_TOKEN` | ✅ | — | BotFather API token |
| `OWNER_IDS` | ❌ | `""` | Comma-separated owner IDs for global vault access |
| `DB_PATH` | ❌ | `bot_data.db` | SQLite file path |
| `WARN_LIMIT` | ❌ | `3` | Warnings before auto-ban |
| `CONTENT_BAN_THRESHOLD` | ❌ | `2` | Content/OCR strikes before auto-ban |
| `CAPTCHA_TIMEOUT` | ❌ | `120` | Seconds to solve captcha |
| `BROADCAST_COOLDOWN` | ❌ | `300` | Seconds between broadcasts |
| `MENTION_DELAY` | ❌ | `3.0` | Seconds between mention batches |
| `MENTION_BATCH_SIZE` | ❌ | `5` | Users per mention batch |

---

## 🎮 Usage Examples

```text
# Moderation
/ban @spammer Scam link
/mute @noisy 2h
/warn @rude Bad language  # 3rd strike = auto-ban
/setrules No spam or unauthorized links.
/rules

# Notes & References
/setnote faq \| Our FAQ is pinned in the channel.
/note faq

# Trust System
/trust @helper   # exempts user from link/OCR filters
/untrust @helper

# Owner Vault
/bannedwords     # view global vault (DM recommended)
/addword crypto-scam, free-nitro

# Broadcasts
@all Meeting starting in 5 minutes!
@admin Urgent review required in chat.

# Anonymous Admin Post
/shadowreply Important notice regarding group rules.

# Games
/new5            # start 5-letter Wordle game
apple            # guess the word
/game            # check active game
/top             # view chat leaderboard
```

---

## 🏗️ Architecture

- **Handler Groups** are explicitly separated (`group=-10` for captcha gates, `group=0` for commands, `group=2` for broadcasts, `group=3` for game guesses, and `group=4` for final safeguards) to prevent race conditions.
- **Error Handler** catches exceptions gracefully, filtering network disconnects and logging traces without crashing polling.
- **HTML Escaping** ensures safe message formatting using `html.escape`.
- **Database** relies on thread-safe synchronous SQLite helpers executed via `asyncio.to_thread` with WAL mode enabled.

---

## 🐳 Docker Deployment

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y tesseract-ocr libtesseract-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py ./
CMD ["python", "bot.py"]
```

```bash
docker build -t miss-sona .
docker run --env-file .env miss-sona
```

---

## 📜 License

MIT — free for personal and commercial use.
