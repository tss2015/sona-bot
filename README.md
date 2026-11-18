# 👑 Miss Sona — The Secret Squad Guardian

### 🛡️ Industrial Telegram Group Management & AI-Powered Protection Bot

![Miss Sona — The Secret Squad Guardian](assets/miss-sona-banner.png)

**Secure • Smart • Powerful • Always There**

Built with ❤️ by **The Secret Squad**

[![Version](https://img.shields.io/badge/Version-6.0-purple?style=for-the-badge)](#project-information)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?style=for-the-badge&logo=python)](#technology-stack)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-blue?style=for-the-badge&logo=telegram)](https://core.telegram.org/bots)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

---

## 🌟 Introducing Miss Sona

**Miss Sona** is a single-file Telegram group-management and protection bot designed to make community administration smarter, safer, and easier.

Built with Python, asynchronous processing, SQLite WAL, and optional open-source AI moderation, Miss Sona brings moderation, anti-spam controls, OCR-assisted screening, welcome messages, filters, notes, games, and multi-group administration together in one application.

**One file. Multiple capabilities. Complete community management.**

### Why Miss Sona?

- 📦 **Single-file application:** The main implementation is `miss_sona.py` (approximately 1,770 lines, according to the project description).
- 🔐 **Group-scoped data:** Group-specific database operations use `chat_id`.
- 🤖 **Optional AI moderation:** Integrates with open-source moderation models.
- 🛡️ **Flexible moderation:** Bans, mutes, kicks, warnings, approvals, and promotions.
- 🔗 **Multi-group management:** Connect and manage groups from private chat.
- ⚡ **Asynchronous processing:** Uses `asyncio` and `python-telegram-bot` v20+.
- 💾 **Persistent storage:** SQLite configured for Write-Ahead Logging (WAL).
- 🎨 **Clean presentation:** Branding is reserved for `/start` and `/help`, according to the project specification.

---

# 🚀 Features

## 1. 🛡️ Moderation

Manage members and enforce group rules with moderation commands.

### Member management

| Command | Description |
|---|---|
| `/ban` | Ban a member |
| `/tban` | Temporarily ban a member, e.g. `/tban 4m`, `/tban 3h`, `/tban 6d` |
| `/dban` | Delete the replied-to message and ban its sender |
| `/sban` | Perform a silent ban |
| `/unban` | Remove a member's ban |
| `/mute` | Restrict a member from messaging |
| `/tmute` | Temporarily mute a member |
| `/dmute` | Delete the replied-to message and mute its sender |
| `/smute` | Perform a silent mute |
| `/unmute` | Restore a member's messaging permissions |
| `/kick` | Remove a member from the group |
| `/dkick` | Delete the replied-to message and kick its sender |
| `/skick` | Perform a silent kick |
| `/promote` | Promote a member to administrator |
| `/demote` | Remove a member's administrator privileges |
| `/adminlist` | Display the administrator list |
| `/admincache` | Refresh administrator information |

### Warning system

| Command | Description |
|---|---|
| `/warn` | Issue a warning |
| `/dwarn` | Delete the replied-to message and issue a warning |
| `/swarn` | Issue a silent warning |
| `/warns` | View a member's warnings |
| `/rmwarn` | Remove a warning |
| `/resetwarn` | Reset a member's warnings |
| `/resetallwarns` | Reset all warnings in the group |
| `/warnmode` | Configure the action taken at the warning limit |
| `/warnlimit` | Set the warning threshold |

Supported warning modes are `ban`, `kick`, and `mute`.

### Approval and trust

- `/approve` — Approve a member.
- `/unapprove` — Remove approval.
- `/trust` — Add a trusted member.
- `/untrust` — Remove trusted status.

### Member targeting

The bot description specifies support for:

1. Reply-based targeting (highest priority).
2. Username targeting, such as `@username`.
3. Numeric Telegram user IDs.
4. Active-user lookup where available.

---

## 2. 🤖 AI-Assisted Content Moderation

Miss Sona includes a layered content-screening design intended to help administrators identify potentially harmful messages.

### Moderation categories

- Adult, sexual, and NSFW content.
- Child-safety violations and CSAM indicators.
- Violence and threats.
- Hate speech and harassment.
- Illegal drugs and weapons-related content.
- Self-harm-related content.
- Spam, scams, and fraud.

### Detection workflow

1. **Keyword and regular-expression checks:** Fast preliminary screening.
2. **Optional AI moderation:** An open-source model can provide additional classification.
3. **Threshold evaluation:** The configured default threshold is `0.75`.
4. **Configured moderation action:** Flagged content may be deleted and logged.

Example notification:

```text
⚠️ Message was deleted as it violates
Telegram TOS — {reason}.

Please follow Telegram Terms of Service.
```

### Configuration commands

```text
/tos on
/tos off
```

The protection setting is configurable per group.

### Optional AI dependencies

- Hugging Face Transformers
- Detoxify
- KoalaAI/Text-Moderation

> **Important:** AI classification is not perfect. It can produce false positives and false negatives, and it does not certify that a group or bot is compliant with Telegram's Terms of Service or applicable law. Administrators should review enforcement settings and provide a way to handle moderation appeals. AI moderation is optional.

---

## 3. 🔒 Security Locks and Anti-Spam

Configure restrictions to reduce unwanted content and disruptive activity.

### Supported lock types

| Lock type | Content or activity |
|---|---|
| `url` | Website URLs |
| `forward` | Forwarded messages |
| `inline` | Inline queries |
| `invite` | Invite links |
| `sticker` | Stickers |
| `photo` | Photos |
| `video` | Videos |
| `gif` | GIF animations |
| `audio` | Audio files |
| `voice` | Voice messages |
| `document` | Documents |
| `contact` | Contact sharing |
| `location` | Location sharing |
| `game` | Telegram games |
| `rtl` | Right-to-left text |
| `button` | Buttons |
| `command` | Commands |
| `email` | Email addresses |
| `phone` | Phone numbers |
| `bot` | Bot-related restrictions |

### Security commands

```text
/lock
/unlock
/locks
/locktypes
/allowlist
/blocklist
/setflood
/setfloodmode
/cleanwelcome
/cleanservice
```

The available restrictions and their exact behavior depend on the implementation and Telegram permissions granted to the bot.

---

## 4. 🧠 OCR-Assisted Media Safety

The OCR pipeline can extract text from supported images and documents so that extracted text can be checked by the configured safety rules.

### Processing overview

```text
Incoming image or supported document
                 |
                 v
           Media detection
                 |
                 v
          Image extraction
                 |
                 v
           OCR extraction
                 |
                 v
       Owner vault / blacklist
                 |
                 v
       Optional AI moderation
                 |
                 v
       Apply configured action
                 |
                 v
             Log event
```

### Technologies

- **Pillow:** Image processing.
- **Tesseract OCR:** Text extraction.
- **Optional AI model:** Additional content classification.

### Capabilities described for this project

- Scanning supported images and documents.
- Extracting visible text using OCR.
- Checking extracted text against an owner-managed vault and OCR blacklist.
- Passing extracted text to the configured moderation pipeline.
- Deleting content when configured rules identify a violation.
- Skipping files larger than 20 MB.

OCR and AI results should be treated as automated signals, not definitive legal determinations.

---

## 5. 👋 Welcome and Goodbye Messages

Welcome new members with customized text or supported media, and optionally send goodbye messages when members leave.

### Features

- Custom welcome messages.
- Custom goodbye messages.
- Photo and video welcome media.
- Per-group configuration.
- Member-count placeholders.
- Optional CAPTCHA verification.

### Message placeholders

| Placeholder | Description |
|---|---|
| `{fullname}` | Member's full name |
| `{username}` | Member's username |
| `{mention}` | Member mention |
| `{id}` | Telegram user ID |
| `{chatname}` | Group name |
| `{count}` | Member count |

### Commands

```text
/setwelcome
/welcome on
/welcome off
```

To configure supported media, reply to the media with `/setwelcome`.

### CAPTCHA verification

The described verification flow includes:

- A `✅ Verify You Are Human` button.
- A 120-second verification period.
- A check intended to help reduce automated bot entries.

---

## 6. 🎯 Filters and Notes

Create automatic replies for common words or phrases, and store reusable information for your group.

### Filters

Create a text filter:

```text
/filter <trigger> <reply>
```

Create a media filter by replying to supported media:

```text
/filter <trigger>
```

Manage filters:

```text
/filters
/stop <trigger>
/stopall
```

Filters are stored separately for each group.

### Notes

Save and retrieve group information:

```text
/setnote <name> <text>
/getnote <name>
/getnote #hashtag
/delnote <name>
```

Example uses include group rules, FAQs, announcements, resources, and frequently shared instructions.

---

## 7. 🧩 Word Jumble — Stack Emojify

Miss Sona includes an interactive word-guessing game for community engagement.

### Feedback symbols

| Symbol | Meaning |
|---|---|
| 🎯 | Correct character in the correct position |
| 🔀 | Character exists but is in the wrong position |
| 🚫 | Character is absent from the target word |

### Game commands

```text
/new
/new4
/new5
/new6
/word
/game
/top
/endgame
```

### Dictionary validation

The project description lists the following embedded dictionary counts:

| Word length | Listed word count |
|---|---:|
| 4 letters | 546 |
| 5 letters | 648 |
| 6 letters | 639 |
| **Sum of listed counts** | **1,833** |

The original project summary also states a total of 1,831 words. These figures do not match, so verify the dictionary in the source file before publishing a definitive total.

Optional word-validation libraries:

- `wordfreq`
- `enchant`

The validation system is intended to reject invalid guesses, including examples such as `bkilsd` and `zzzz`.

---

## 8. 🔗 Multi-Group Connection

Connect multiple Telegram groups and manage them through the bot's private chat interface.

### Connection workflow

**Step 1 — Start in the group**

```text
/connect
```

The bot performs an administrator check.

**Step 2 — Open the bot privately**

Start a private conversation with Miss Sona.

**Step 3 — Manage connected groups**

```text
/connect
/connections
/disconnect
```

### Described capabilities

- List connected groups.
- Switch between connected groups using inline controls.
- Manage supported group settings from private chat.
- Disconnect a group.
- Resolve the effective group ID for private management operations.

---

## 9. ⚙️ Additional Utilities

| Command | Description |
|---|---|
| `/all` | Mention group members |
| `/admins` | Mention group administrators |
| `/shadowreply` | Use the shadow-reply feature |
| `/rules` | Display group rules |
| `/setrules` | Configure group rules |
| `/privaterules` | Use the private-rules workflow |
| `/export` | Export supported bot data |
| `/import` | Import supported bot data |
| `/disable` | Disable supported commands |
| `/id` | Display Telegram IDs |
| `/support` | Display support information |
| `/setlog` | Configure the logging destination |

### Mention throttling

The project description specifies a throttle of **5 mentions per 3 seconds** to help limit excessive notifications.

---

# 🏗️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Main programming language |
| `python-telegram-bot` v20+ | Telegram Bot API framework |
| `asyncio` | Asynchronous execution |
| SQLite | Persistent database |
| SQLite WAL | Write-Ahead Logging |
| Pillow | Image processing |
| Tesseract OCR | Text extraction from images |
| Transformers | Optional AI inference |
| Detoxify | Optional toxicity classification |
| Telegram HTML ParseMode | Message formatting |

## Handler groups

The described application uses these handler-group constants:

```python
GROUP_TRACK = -1
G_SEC = 0
G_MAIN = 1
G_FB = 2
```

## SQLite configuration

The project description specifies these database settings:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=10000;
```

The database design is described as using:

- `chat_id` scoping for group-specific records.
- Composite primary keys.
- Group-filtered database queries.
- SQLite WAL for improved concurrent access.

## Project layout

A suggested repository layout is:

```text
Miss-Sona/
├── miss_sona.py
├── requirements.txt
├── .env
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── LICENSE
├── README.md
├── assets/
│   └── miss-sona-banner.png
└── miss_sona.db
```

The main bot implementation is:

```text
miss_sona.py
```

The Docker files, environment file, assets, and database should be included only if they are part of your actual repository. Do not commit bot tokens or other secrets.

---

# 📦 Installation

## Requirements

- Python 3.8 or newer, subject to compatibility with the selected dependency versions.
- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- Internet access.
- Administrator permissions in groups where the bot will moderate.
- Tesseract OCR for OCR functionality.
- Optional AI dependencies for AI moderation.

## 1. Clone the repository

Replace the placeholder with your actual repository URL:

```bash
git clone <YOUR_REPOSITORY_URL>
cd Miss-Sona
```

## 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

## 3. Install Tesseract OCR

On Ubuntu or Debian:

```bash
sudo apt update
sudo apt install tesseract-ocr
```

Install the Python OCR packages if they are not already included in `requirements.txt`:

```bash
pip install Pillow pytesseract
```

## 4. Install optional AI dependencies

```bash
pip install transformers torch detoxify
```

AI packages and models can require substantial memory, storage, and processing capacity. Install them only if you intend to enable the corresponding functionality.

## 5. Configure environment variables

Create a `.env` file or configure the variables in your deployment environment:

```ini
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
OWNER_ID=YOUR_TELEGRAM_USER_ID
DB_PATH=miss_sona.db

SUPPORT_CHAT_ID=-1001234567890
SUPPORT_LINK=https://t.me/SecretSquadSupport

TOS_AI_MODEL=KoalaAI/Text-Moderation
TOS_AI_THRESHOLD=0.75
```

### Environment variable reference

| Variable | Required? | Purpose |
|---|---|---|
| `BOT_TOKEN` | Yes | Telegram bot token |
| `OWNER_ID` | Optional | Bot owner's Telegram user ID |
| `DB_PATH` | Optional | SQLite database path |
| `SUPPORT_CHAT_ID` | Optional | Moderation log chat or channel |
| `SUPPORT_LINK` | Optional | Support community link |
| `TOS_AI_MODEL` | Optional | AI moderation model |
| `TOS_AI_THRESHOLD` | Optional | AI moderation threshold |

The `.env` file is not automatically loaded by Python unless the application or its startup environment loads it. If the project uses `python-dotenv`, ensure that dependency is installed and `load_dotenv()` is called.

## 6. Start the bot

```bash
python miss_sona.py
```

If configuration and dependencies are correct, the bot should start polling Telegram for updates.

---

# 🐳 Docker Deployment

If the repository includes a working `Dockerfile` and `docker-compose.yml`, you can use Docker Compose.

### Start

```bash
docker compose up -d
```

### Follow logs

```bash
docker compose logs -f
```

### Stop

```bash
docker compose down
```

Make sure the Docker configuration passes the required environment variables and mounts persistent storage for the SQLite database. The OCR system package must also be installed in the image if OCR is enabled.

---

# 📋 Quick Command Reference

| Category | Commands |
|---|---|
| Moderation | `/ban`, `/tban`, `/dban`, `/sban`, `/unban`, `/mute`, `/tmute`, `/unmute`, `/kick` |
| Warnings | `/warn`, `/dwarn`, `/swarn`, `/warns`, `/rmwarn`, `/resetwarn`, `/resetallwarns` |
| Administration | `/promote`, `/demote`, `/adminlist`, `/admincache` |
| Approval and trust | `/approve`, `/unapprove`, `/trust`, `/untrust` |
| Security | `/lock`, `/unlock`, `/locks`, `/locktypes`, `/allowlist`, `/blocklist` |
| Flood control | `/setflood`, `/setfloodmode` |
| Welcome | `/setwelcome`, `/welcome on`, `/welcome off` |
| Filters | `/filter`, `/filters`, `/stop`, `/stopall` |
| Notes | `/setnote`, `/getnote`, `/delnote` |
| AI moderation | `/tos on`, `/tos off` |
| Word game | `/new`, `/new4`, `/new5`, `/new6`, `/word`, `/game`, `/top`, `/endgame` |
| Multi-group | `/connect`, `/connections`, `/disconnect` |
| Utilities | `/all`, `/admins`, `/rules`, `/setrules`, `/privaterules`, `/export`, `/import`, `/disable`, `/id` |
| Support | `/support`, `/setlog` |

For the full command list and usage instructions, use:

```text
/help
```

or:

```text
/start
```

---

# 🔐 Privacy and Group Isolation

Group isolation is a core design objective of Miss Sona.

### Database isolation

According to the project design:

- Group-specific tables include a `chat_id` field.
- Group-specific queries filter records by `chat_id`.
- Filters, notes, and moderation data are scoped to their respective groups.
- Private management commands resolve the effective group ID before performing group operations.

### Database reliability

The database configuration includes:

- SQLite WAL mode.
- A 10-second busy timeout.
- Normal synchronous mode.
- Composite keys for scoped records.

### Safe message formatting

The project description specifies Telegram HTML ParseMode and `html.escape()` for user-provided text. Ensure every untrusted value inserted into HTML-formatted Telegram messages is escaped appropriately.

### Logging

When `SUPPORT_CHAT_ID` is configured, moderation logs are directed to that chat or channel.

The described log prefix is:

```text
🛡️ Miss Sona Log | The Secret Squad
```

### Owner controls

Owner-only functionality is described for protected vault management and bypass controls. Keep the owner ID private and verify authorization checks in the implementation.

### Deployment security checklist

- Never publish your bot token.
- Keep `.env` out of version control.
- Restrict access to the database and backups.
- Give the bot only the Telegram permissions it needs.
- Review automated moderation actions.
- Set an appropriate data-retention policy.
- Protect log channels and administrative commands.

---

# 🎨 Branding and Identity

## 👑 Miss Sona — The Secret Squad Guardian

**Secure • Smart • Powerful • Always There**

Miss Sona brings together community protection, automated administration, and interactive features in one Telegram bot.

### Our philosophy

- 🛡️ Protect communities.
- 🤖 Automate repetitive administration.
- 🔐 Respect group privacy.
- ⚡ Keep management efficient.
- 💜 Make Telegram communities more enjoyable.

---

# 📞 Support and Community

**Built with ❤️ by The Secret Squad**

- **Telegram Support:** [@SecretSquadSupport](https://t.me/SecretSquadSupport)
- **GitHub Issues:** Open an issue in the project repository.
- **License:** MIT

For technical assistance, bug reports, feature requests, and suggestions, contact the support community.

---

# 🧾 Project Information

| Property | Details |
|---|---|
| Project | Miss Sona |
| Identity | The Secret Squad Guardian |
| Version | v6.0 TOS-AI |
| Release date | 18 November 2026 |
| Main file | `miss_sona.py` |
| Architecture | Single-file application |
| Approximate source size | 1,770 lines |
| Language | Python |
| Framework | `python-telegram-bot` v20+ |
| Database | SQLite WAL |
| Async processing | `asyncio` |
| AI moderation | Optional |
| OCR | Optional |
| License | MIT |

---

# 📜 License

Miss Sona is distributed under the **MIT License**.

You may use, copy, modify, merge, publish, distribute, sublicense, and sell copies of the software in accordance with the terms of the license.

See the [`LICENSE`](LICENSE) file for the complete license text.

---

<div align="center">

## 👑 THE SECRET SQUAD

**BUILD • MANAGE • PROTECT**

### Miss Sona — Your Community, Our Responsibility.

Made with ❤️ for Telegram communities.

*© 2026 The Secret Squad. Released under the MIT License.*

</div>
