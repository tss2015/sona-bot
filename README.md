# Miss Sona

**An all-in-one Telegram group management and gaming bot.**

Miss Sona handles moderation, persistent rules and notes, anti-link and content safeguards with OCR, captcha verification, throttled mass mentions, proxy announcements, and a Wordle-style word game — all in one async, production-ready Python file.

Built with [python-telegram-bot](https://docs.python-telegram-bot.org/) (v20+/v21.x), SQLite, Pillow and PyTesseract. No external APIs or hosted services required.

[Features](#features) · [Quick start](#quick-start) · [Configuration](#configuration) · [Commands](#command-reference) · [Architecture](#architecture) · [Deployment](#deployment) · [Troubleshooting](#troubleshooting)

---

## Repository layout

```
.
├── index.html   # Interactive blueprint: full bot.py source, command explorer,
│                # .env generator, schema, deployment guide and downloads
└── README.md    # This file
```

The runnable Python application is embedded verbatim inside `index.html` in
`<script id="py-source" type="text/plain">`. Open the page and use **Download bot.py**,
or extract it from the command line:

```bash
python - <<'PY'
import re
from pathlib import Path

page = Path("index.html").read_text(encoding="utf-8")
match = re.search(r'<script id="py-source" type="text/plain">(.*?)</script>', page, re.S)
if not match:
    raise SystemExit("Embedded bot.py not found")
Path("bot.py").write_text(match.group(1).lstrip("\n").rstrip() + "\n", encoding="utf-8")
print("Wrote bot.py")
PY
```

The page also provides copy/download tabs for `requirements.txt`, `.env.example` and
`miss-sona.service`. There is no build step for the website; serving `index.html` is enough.

---

## Features

| Module | What Miss Sona does |
| --- | --- |
| **Moderation** | `/ban`, `/unban`, `/mute`, `/unmute`, `/warn`, `/warns`. Every warning is persisted per chat; reaching `WARN_LIMIT` (default **3**) automatically bans the user. Optional mute durations (`30m`, `2h`, `7d`); permanent by default. Admins and owners are protected from bot moderation. |
| **Persistence** | Custom group rules (`/setrules`, `/rules`) and named reference notes (`/setnote`, `/note`, `/notes`, `/delnote`) stored permanently in SQLite with upserts. Notes list/lookup, 8,000-character rules limit, safe HTML output. |
| **Anti-link** | Deletes unapproved `http(s)://`, `www.`, `t.me`, `telegram.me`, `tg://` links and Telegram URL/text-link entities. Posts a **temporary auto-deleting warning**. Domain allowlist, per-user trust (`/trust`, `/untrust`), and `LINK_WHITELIST` IDs. |
| **Content safeguard** | Keyword filtering with NFKC normalisation and confusable folding (`p0rn` → `porn`). Images are downloaded and OCR'd **off the event loop** with PyTesseract; matching captions or image text are deleted and strikes recorded. Repeat offenders are banned at `CONTENT_BAN_THRESHOLD` (default **2**). |
| **Broadcasts** | Admin-only `@all [message]` and `@admin [message]`. Mentions are sent **one by one with `asyncio.sleep(3)`** (configurable), with per-chat cooldown, a concurrency lock, mention cap, batching, and `RetryAfter` back-off. |
| **Captcha gate** | New joiners are immediately restricted and receive one inline button. Verification restores the chat's default permissions. A 2-minute JobQueue timeout kicks unverified users; pending timers are re-armed automatically after a restart. |
| **Shadow reply** | `/shadowreply <text>` deletes the admin's command and republishes the payload **from the bot's own identity**, preserving Telegram HTML formatting. Restricted to admins/owners with a short per-user rate limit and audit logging. |
| **Word game** | `/new`, `/new4`, `/new5`, `/new6` start persistent games. Up to **30 guesses** (`MAX_GUESSES`). Two-pass duplicate-aware scoring: 🎯 exact, 🔀 misplaced, 🚫 absent. First correct guess wins; leaderboard via `/top`. |
| **Utilities** | `/start`, `/help`, `/ping` (health + DB stats), `/id`, `/game`, `/stopgame`, `/top`. |

### Why it is production-oriented

- Fully asynchronous handlers; blocking SQLite and OCR work runs via `asyncio.to_thread`.
- Single SQLite connection guarded by a thread lock, WAL journal, configurable timeouts.
- Targeted error handling for `BadRequest`, `Forbidden`, `RetryAfter`, `NetworkError`, plus a global error handler.
- Cached admin checks to avoid `getChatMember` on every message.
- Explicit PTB handler groups so guesses and broadcasts are never re-read by the text filter.
- Graceful degradation: missing Tesseract/Pillow disables OCR with a warning instead of crashing.
- Restart-safe captcha timer reconstruction from the database.
- Environment-driven configuration with validation and safe clamping.

---

## Quick start

### Prerequisites

- **Python 3.10+**
- **Tesseract OCR 4+** with language data under `tessdata` (optional but needed for image scanning)
- A bot token from [@BotFather](https://t.me/BotFather)
- A **supergroup** where you can promote the bot
- Your numeric Telegram user ID (e.g. from [@userinfobot](https://t.me/userinfobot)) for `OWNER_IDS`

### 1. System dependencies

```bash
# Debian / Ubuntu
sudo apt update
sudo apt install -y python3-venv tesseract-ocr tesseract-ocr-eng libtesseract-dev

# macOS (Homebrew)
brew install python tesseract
```

Windows: install Tesseract and set `TESSERACT_CMD` to the full path of `tesseract.exe`.

### 2. Bot files

Download `bot.py` from the blueprint's **Source** section (or extract it with the snippet above),
then place `requirements.txt` and `.env.example` beside it.

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

### 3. Configure

Edit `.env` and set at minimum:

```dotenv
BOT_TOKEN=123456789:AA...your-token...
OWNER_IDS=123456789
```

`OWNER_IDS` is a comma-separated list. Owners can moderate, broadcast, and use
`/shadowreply` even without a Telegram admin badge.

### 4. Telegram setup

1. In @BotFather: `/setname` → **Miss Sona** (display name). The username itself
   cannot contain spaces, e.g. `@MissSonaBot`.
2. `/setprivacy` → **Disable** so the bot receives ordinary group messages
   (required for activity tracking, link filtering, OCR and bare-word guesses).
3. Add the bot to your group and promote it with **Delete messages**,
   **Ban users**, and **Restrict members** rights.
4. Use a **supergroup**. Basic groups do not support the restriction APIs reliably.

### 5. Run

```bash
python bot.py
```

The bot uses long polling with `allowed_updates=Update.ALL_TYPES` (required for
`chat_member` join events). Leave it running under a process supervisor for production.

---

## Configuration

All settings are environment variables. Defaults are shown below.

### Core

| Variable | Default | Description |
| --- | --- | --- |
| `BOT_TOKEN` | **required** | Bot token from @BotFather. Never commit it. |
| `OWNER_IDS` | empty | Comma-separated user IDs with owner privileges. |
| `DB_PATH` | `bot_data.db` | SQLite database path; the directory is created if needed. |
| `LOG_LEVEL` | `INFO` | Python logging level. |
| `ERROR_CHAT_ID` | empty | Optional chat/user ID for unhandled exception alerts. |

### Moderation and content

| Variable | Default | Description |
| --- | --- | --- |
| `WARN_LIMIT` | `3` | Warnings before an automatic ban. |
| `CONTENT_BAN_THRESHOLD` | `2` | Content strikes before a ban; reset on ban. |
| `BANNED_WORDS` | built-in list | Comma-separated restricted terms. Normalised, word-boundary matched. |
| `ALLOWED_DOMAINS` | `github.com,stackoverflow.com` | Domains permitted for everyone; subdomains included. |
| `LINK_WHITELIST` | empty | Comma-separated user IDs exempt from link filtering. |

### Captcha and broadcasts

| Variable | Default | Description |
| --- | --- | --- |
| `CAPTCHA_TIMEOUT` | `120` | Seconds (2 minutes) before an unverified joiner is kicked. Clamped 30–900. |
| `BROADCAST_COOLDOWN` | `300` | Minimum seconds between mass mentions in a chat. |
| `MENTION_DELAY` | `3` | Pacing between mention messages in seconds. **Keep at 3 or higher.** |
| `MENTION_BATCH_SIZE` | `1` | Members mentioned per message. Raise carefully for very large groups. |
| `MAX_MENTIONS` | `40` | Maximum `@all` targets, most recently active first. |
| `ACTIVE_WINDOW_DAYS` | `30` | Activity lookback for `@all`. |

### Game

| Variable | Default | Description |
| --- | --- | --- |
| `MAX_GUESSES` | `30` | Guesses per game (5–60). |
| `WORDS_FILE` | empty | Optional JSON word bank override. |

### OCR

| Variable | Default | Description |
| --- | --- | --- |
| `OCR_LANG` | `eng` | Tesseract language code(s), e.g. `eng+deu`. |
| `OCR_MAX_BYTES` | `8388608` | Skip image files larger than this (8 MB). |
| `TESSERACT_CMD` | empty | Explicit Tesseract binary path if not on `PATH`. |

### Custom word bank format

`WORDS_FILE` accepts a mapping of length to words, or a flat list (lengths inferred):

```json
{
  "5": ["amber", "crane", "pixel"],
  "6": ["rocket", "velvet"]
}
```

Words must contain letters only; they are lower-cased on load. A mapping replaces that
length's built-in words; a flat list extends the bank.

---

## Command reference

### Members

| Command | Purpose |
| --- | --- |
| `/start` | Brief introduction and capability summary. |
| `/help` | Full command list. |
| `/ping` | Health check plus database counters. |
| `/id` | Current chat ID and your user ID. |
| `/rules` | Show saved group rules. |
| `/note <name>` | Show a saved note (no name lists all notes). |
| `/notes` | List note names. |
| `/new`, `/new4`, `/new5`, `/new6` | Start a game (default 5 letters). |
| `<word>` | A bare word of the correct length is accepted as a guess. |
| `/game` | Current game progress and hint legend. |
| `/top` | Top five guessers in the chat. |

### Administrators (or owners)

| Command | Purpose |
| --- | --- |
| `/ban [reason]` | Ban a replied-to user or a numeric ID. |
| `/unban <user_id>` | Lift a ban. |
| `/mute [duration]` | Restrict posting; `30m`, `2h`, `1d`; permanent without a duration. |
| `/unmute` | Restore the chat's default permissions. |
| `/warn [reason]` | Add a warning; reaching the limit bans automatically. |
| `/warns` | Show a user's warning count. |
| `/trust` / `/untrust` | Grant/revoke a user's link-posting exemption. |
| `/setrules <text>` | Save group rules permanently. |
| `/setnote <name> \| <text>` | Create or update a named note. |
| `/delnote <name>` | Delete a note. |
| `/stopgame` | Stop the active game and reveal the word. |
| `/shadowreply <text>` | Delete the command and repost the payload as **Miss Sona**. |
| `@all [message]` | Mention recently active members, paced at 3 seconds. |
| `@admin [message]` | Mention all non-bot group administrators. |

### Usage examples

```text
/setrules 1. Be kind.  2. No spam or unsolicited links.  3. English in general chat.
/setnote faq | Read the FAQ at example.com before asking.
/rules
/shadowreply Maintenance starts at <b>20:00 UTC</b>. Please finish your games.
@all Server maintenance in 10 minutes — save your guesses!
/mute 123456789 30m
```

For `/mute`, reply to the target and pass a duration, or pass the user ID first:
`/mute 123456789 2h`.

### Game hints

| Icon | Meaning |
| --- | --- |
| 🎯 | Correct letter in the correct position. |
| 🔀 | Letter exists in the word but in another position. |
| 🚫 | Letter is not present (considering duplicate counts). |

Games are persisted in SQLite, survive restarts, are shared per chat, and end when a
guess is correct or the guess limit is reached. Guesses are not dictionary-checked;
any alphabetic string of the exact length is accepted.

---

## Architecture

Update flow across PTB handler groups:

```
group -10   activity tracking + unverified-message gate
group   0   commands, captcha callback, join events
group   2   @all / @admin broadcasts            → ApplicationHandlerStop
group   3   active-game guesses                 → ApplicationHandlerStop
group   4   link policy → keyword filter → OCR → strikes/bans
JobQueue    captcha timeouts (2 minutes, re-armed after restart)
```

Key modules in `bot.py`:

| Component | Responsibility |
| --- | --- |
| `Config` | Parses, validates and clamps environment values. |
| `Database` | Thread-safe SQLite facade; called exclusively through `asyncio.to_thread`. |
| `KeywordMatcher` | Normalised, word-boundary banned-term matching. |
| `enforce_link_policy` | Detects raw URLs and Telegram entities, deletes violations, warns temporarily. |
| `enforce_content_policy` | Text/caption/OCR matches, content strikes, repeat-offender bans. |
| `on_broadcast` | Admin-gated, cooldown/lock-serialised paced mentions. |
| `on_chat_member` / `on_captcha_button` | Restrict, verify, restore permissions. |
| `captcha_timeout` | Kick and clean up unverified users. |
| `cmd_shadowreply` | Entity-preserving proxy send from the bot. |
| `score_guess` | Two-pass Wordle scoring with duplicate handling. |
| `post_init` | Creates schema, restores pending captcha timers, logs bot identity. |
| `register_handlers` | Declares all handler groups explicitly. |

### Data model

| Table | Contents |
| --- | --- |
| `rules` | One rules document per chat (`chat_id` PK). |
| `notes` | Named notes per chat (`chat_id`, `name` PK). |
| `warnings` | Moderation warnings per chat/user. |
| `content_strikes` | Content-safeguard strikes per chat/user. |
| `link_trust` | Users allowed to post links, with granting admin. |
| `activity` | Display names, usernames, last seen; powers `@all`. |
| `captcha` | Pending verifications: token, message ID, creation time. |
| `games` | Word, length, JSON guesses, starter, status. |
| `wins` | Per-chat win leaderboard. |

The schema is created idempotently at startup with `PRAGMA journal_mode=WAL`,
`busy_timeout=5000` and a thread lock around writes.

---

## Deployment

### systemd (Linux, per-user)

The blueprint's `miss-sona.service` tab assumes `/opt/miss-sona`:

```bash
sudo mkdir -p /opt/miss-sona
sudo cp bot.py requirements.txt .env /opt/miss-sona/
sudo python3 -m venv /opt/miss-sona/.venv
sudo /opt/miss-sona/.venv/bin/pip install -r /opt/miss-sona/requirements.txt
sudo cp miss-sona.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now miss-sona
systemctl --user status miss-sona
journalctl --user -u miss-sona -f
```

Restrict permissions on `.env` and the database:

```bash
sudo chmod 600 /opt/miss-sona/.env
sudo chown -R "$USER" /opt/miss-sona
```

### Backups

SQLite runs in WAL mode, so copy `bot_data.db`, `bot_data.db-wal` and
`bot_data.db-shm` together, or use a consistent online backup:

```bash
sqlite3 bot_data.db ".backup 'bot_data.backup.db'"
```

Stop the bot before manual file copies to avoid inconsistent snapshots.

### Running the blueprint website

```bash
python -m http.server 8000
# open http://localhost:8000
```

The page uses the Tailwind browser CDN and Google Fonts; no build tooling is required.

---

## Operational notes and limitations

- **`@all` can only mention tracked users.** Telegram's Bot API cannot enumerate all
  group members. Miss Sona stores members who chat in the `activity` table; mention
  reach grows as participation grows. `@admin` reads the live administrator list.
- **Flood limits are real.** Keep `MENTION_DELAY` at 3s, keep `MAX_MENTIONS` modest,
  and raise `MENTION_BATCH_SIZE` instead of lowering the delay for large groups.
  `RetryAfter` is honoured automatically, but the safest strategy is pacing.
- **OCR is text detection, not image classification.** Restricted terms inside photos,
  screenshots, or image documents are detected after Tesseract extracts text. Visual
  NSFW content without matching text is not classified.
- **Heuristic filters can produce false positives.** Review bans, keep an appeal path,
  and consider tuning `BANNED_WORDS` and the strike threshold for your community.
- **Admins and owners bypass safeguards.** Owner/admins (and link-trusted users for
  links) can post links and restricted terms. Keep `OWNER_IDS` accurate and minimal.
- **Captcha kick means ban + unban.** The user can rejoin with a fresh invite; repeated
  failures can be escalated manually. A pending user's messages are deleted defensively.
- **Supergroup required.** Restriction, deletion and join events need appropriate admin
  rights; without them the bot warns in chat and logs the failure.
- **No message-content retention by design.** Activity names/usernames and game guesses
  are stored; filtered messages are deleted rather than archived. Validate this against
  your own privacy obligations and disclose automated scanning to members.
- **`/shadowreply` is powerful.** Announcements look authored by the bot. Limit owner
  access, review the logs (`shadow reply chat=… by=…`), and never let it be used to
  impersonate community members.

---

## Security

- Keep `BOT_TOKEN` in `.env`, never in `bot.py` or version control.
- Add `.env`, `*.db`, `*.db-wal`, `*.db-shm`, `.venv/` and `__pycache__/` to `.gitignore`.
- Restrict `.env`/database file permissions and back up only to trusted storage.
- Grant the bot only the admin rights it needs: delete messages, ban users, restrict
  members. Avoid adding it as a full administrator with unrelated powers.
- Rotate the token in @BotFather immediately if it is exposed (`/revoke`).
- `OWNER_IDS` is a privileged list and bypasses safety filters. Verify IDs carefully.
- Set `ERROR_CHAT_ID` to a private admin chat; error reports may contain message details.
- Audit `/shadowreply`, `/trust` and broadcast activity through service logs.
- Comply with Telegram's terms, local law, and your group's privacy expectations.

---

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `BOT_TOKEN is missing` | `.env` not found or token malformed. Run from the directory containing `.env`. |
| `ModuleNotFoundError: telegram` | Activate the virtualenv and `pip install -r requirements.txt`. |
| JobQueue warning / no captcha timeouts | Install the extra: `pip install "python-telegram-bot[job-queue]>=21,<22"`. A plain asyncio fallback also runs. |
| OCR unavailable / image scanning silent | Install Pillow and pytesseract, install the Tesseract binary and language data, or set `TESSERACT_CMD`. |
| No captcha on joins | Bot is not a group admin, lacks **Restrict members**, group is not a supergroup, or privacy/update settings are wrong. Check `chat_member` updates are delivered. |
| Bot doesn't react to normal messages | Enable privacy mode off via `/setprivacy` and re-add the bot to the group. |
| `@all` mentions nobody | No tracked activity yet, or everyone is outside `ACTIVE_WINDOW_DAYS`. Wait for members to chat. |
| Broadcast rejected | Sender is not admin/owner (group admin cache lasts ~45s), a broadcast is already running, or the cooldown has not elapsed. |
| Flood-wait errors | Increase `MENTION_DELAY`, lower `MAX_MENTIONS`, or increase `MENTION_BATCH_SIZE` for large communities. |
| `Conflict: terminated by other getUpdates` | Another instance is polling with the same token. Run exactly one process. |
| Captcha never resolves | Timeout job missing after a crash is re-armed at startup; check logs and grant Restrict members. |
| Ban/mute fails | Missing admin rights, user is an administrator, or the target ID is invalid. The bot replies with the reason. |
| Shadow reply formatting error | Malformed HTML is retried as plain text; the original command is still removed. Check bot HTML entities. |
| Word game doesn't accept guesses | No active game, wrong word length, non-alphabetic guess, or guess limit reached. Use `/game`. |
| Database is locked | Only one bot process should run; check `DB_PATH` permissions and WAL files on shared storage. |

Enable `LOG_LEVEL=DEBUG` for detailed filter/OCR diagnostics.

---

## Development

- The canonical Python source is embedded in `index.html`. Edit it there, download
  `bot.py`, and keep the blueprint and deployment copies in sync.
- Avoid the literal `</script>` sequence inside embedded Python comments or strings, as
  it terminates the HTML script block early.
- The website is a static document: no bundler, no package manager, no server code.
- The Python app is a single file by design. Keep it that way unless you introduce
  explicit migrations and tests.
- Before changing scoring, moderation thresholds, or handler groups, test in a private
  supergroup with a second account and a backup of `bot_data.db`.

---

## Acknowledgements

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) and [PyTesseract](https://github.com/madmaze/pytesseract)
- [Pillow](https://python-pillow.org/)
- [Tailwind CSS](https://tailwindcss.com/)

---

## License and disclaimer

No license is included in this repository. Add a `LICENSE` file (for example, MIT) if
you intend to redistribute the code. Review the licenses of all dependencies.

This software performs automated moderation and content scanning. It is provided as-is,
without warranty; you are responsible for configuring it correctly, moderating its
decisions, and complying with applicable law, Telegram's terms, and your community's
privacy expectations. Never deploy it without a tested backup and an appeal process.
