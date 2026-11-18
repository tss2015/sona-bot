# 👑 Miss Sona --- The Secret Squad Guardian

### 🛡️ Industrial Telegram Group Management & AI-Powered Protection Bot

```{=html}
<p align="center">
```
`<img src="assets/miss-sona-banner.png" alt="Miss Sona — The Secret Squad Guardian" width="100%">`{=html}
```{=html}
</p>
```
```{=html}
<p align="center">
```
`<b>`{=html}Secure • Smart • Powerful • Always There`</b>`{=html}
```{=html}
</p>
```
```{=html}
<p align="center">
```
Built with ❤️ by `<b>`{=html}The Secret Squad`</b>`{=html}
```{=html}
</p>
```
```{=html}
<p align="center">
```
`<img src="https://img.shields.io/badge/Version-6.0-purple?style=for-the-badge">`{=html}
`<img src="https://img.shields.io/badge/Python-3.8%2B-blue?style=for-the-badge">`{=html}
`<img src="https://img.shields.io/badge/Telegram-Bot-blue?style=for-the-badge&logo=telegram">`{=html}
`<img src="https://img.shields.io/badge/AI-Powered-ff69b4?style=for-the-badge">`{=html}
`<img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge">`{=html}
```{=html}
</p>
```

------------------------------------------------------------------------

## 🌟 Introducing Miss Sona

**Miss Sona** is an advanced, single-file Telegram group management and
protection bot designed to make community administration smarter, safer,
and easier.

Powered by Python, asynchronous processing, SQLite WAL, and optional
open-source AI moderation, Miss Sona brings professional-grade community
management capabilities into one compact application.

From automated moderation and anti-spam protection to OCR-based image
scanning, customizable welcome messages, interactive games, and
multi-group administration, Miss Sona provides an integrated solution
for modern Telegram communities.

**One file. Multiple capabilities. Complete community management.**

### 💜 Why Miss Sona?

-   📦 **Single-file architecture:** Approximately 1,770 lines in
    `miss_sona.py`.
-   🔐 **Strict group isolation:** Database operations are scoped by
    `chat_id`.
-   🤖 **AI-assisted protection:** Optional open-source content
    moderation.
-   🛡️ **Advanced moderation:** Automated restrictions, warnings, and
    security locks.
-   🔗 **Multi-group management:** Control connected communities through
    private messages.
-   ⚡ **Asynchronous architecture:** Built with `asyncio` and
    `python-telegram-bot`.
-   💾 **Reliable storage:** SQLite with Write-Ahead Logging.
-   🎨 **Clean branding:** No unnecessary promotional messages in normal
    group interactions.

------------------------------------------------------------------------

# 🚀 Core Features

## 1. 🛡️ Advanced Moderation System

Take complete control of your Telegram community with a comprehensive
moderation toolkit.

**Member management**

-   `/ban` --- Permanently ban a member.
-   `/tban` --- Temporarily ban a member.
-   `/dban` --- Delete the replied-to message and ban its sender.
-   `/sban` --- Ban silently.
-   `/unban` --- Remove a ban.
-   `/mute` --- Restrict a member.
-   `/tmute` --- Temporarily mute a member.
-   `/dmute` --- Delete the message and mute its sender.
-   `/smute` --- Mute silently.
-   `/unmute` --- Restore messaging permissions.
-   `/kick` --- Remove a member.
-   `/dkick` --- Delete the message and kick its sender.
-   `/skick` --- Kick silently.
-   `/promote` --- Promote a member.
-   `/demote` --- Remove administrator privileges.

**Warning management**

-   `/warn` --- Issue a warning.
-   `/dwarn` --- Delete the message and issue a warning.
-   `/swarn` --- Issue a silent warning.
-   `/warns` --- View a member's warnings.
-   `/rmwarn` --- Remove a warning.
-   `/resetwarn` --- Reset a member's warnings.
-   `/resetallwarns` --- Reset all warnings in the group.
-   `/warnmode` --- Configure automatic punishment.
-   `/warnlimit` --- Set the warning threshold.

**Additional administration**

-   `/adminlist` --- Display group administrators.
-   `/admincache` --- Refresh administrator information.
-   `/approve` --- Approve a trusted member.
-   `/unapprove` --- Remove approval.
-   `/trust` --- Add a trusted member.
-   `/untrust` --- Remove trusted status.

**Target identification**

Miss Sona supports:

-   Reply-based member targeting.
-   Username targeting (`@username`).
-   Numeric Telegram user IDs.
-   Active-user lookup where available.

Reply-based targeting receives the highest priority.

------------------------------------------------------------------------

## 2. 🤖 Telegram Terms of Service Safety Protection

An AI-assisted moderation layer designed to identify potentially harmful
content and help administrators maintain safer communities.

### Supported moderation categories

  Category                Protection
  ----------------------- -----------------------------------------------
  🔞 Adult content        Sexual and NSFW material
  🚨 Child safety         Child sexual abuse material (CSAM) indicators
  ⚔️ Violence             Violent threats and harmful content
  🚫 Hate                 Hate speech and harassment
  💊 Illegal activities   Illegal drugs and weapons-related content
  🆘 Self-harm            Self-harm-related content
  ⚠️ Fraud                Spam, scams, and fraudulent messages

### How it works

Miss Sona uses a layered detection pipeline:

1.  **Keyword and Regex Detection:** Fast preliminary content screening.
2.  **Optional AI Moderation:** Open-source models for additional
    content classification.
3.  **Confidence Threshold:** Configurable moderation threshold, default
    `0.75`.
4.  **Automated Action:** Depending on the configured policy, flagged
    content can be deleted and logged.

**Example moderation notification:**

``` text
⚠️ Message was deleted as it violates
Telegram TOS — {reason}.

Please follow Telegram Terms of Service.
```

### Available configuration

``` text
/tos on
/tos off
```

Protection can be configured independently for each group.

**AI models and libraries:**

-   Detoxify
-   Hugging Face Transformers
-   KoalaAI/Text-Moderation

> Note: Automated moderation is probabilistic, not a guarantee of legal
> or Telegram policy compliance. False positives and false negatives are
> possible. Administrators should review moderation decisions and
> maintain an appropriate appeal process. AI protection is optional.

------------------------------------------------------------------------

## 3. 🔒 Smart Security Locks & Anti-Spam

Protect your community from unwanted content, suspicious links, and spam
activities.

### Available lock categories

  Lock         Restricted content
  ------------ --------------------------
  `url`        Website URLs
  `forward`    Forwarded messages
  `inline`     Inline queries
  `invite`     Group invitations
  `sticker`    Stickers
  `photo`      Photos
  `video`      Videos
  `gif`        GIF animations
  `audio`      Audio files
  `voice`      Voice messages
  `document`   Documents
  `contact`    Contact sharing
  `location`   Location sharing
  `game`       Telegram games
  `rtl`        Right-to-left text
  `button`     Buttons
  `command`    Commands
  `email`      Email addresses
  `phone`      Phone numbers
  `bot`        Bot-related restrictions

### Security commands

``` text
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

Configure restrictions according to the requirements of each community.

------------------------------------------------------------------------

## 4. 🧠 AI-OCR Safety Pipeline

Miss Sona extends content screening beyond ordinary text messages.

Its OCR pipeline can inspect supported images and documents to extract
readable text for moderation.

### Processing pipeline

``` text
       Incoming Media
             |
             v
       Media Detection
             |
             v
       Image Extraction
             |
             v
      OCR Text Extraction
             |
             v
       Owner Vault Check
             |
             v
       OCR Blacklist Check
             |
             v
       TOS AI Moderation
             |
             v
       Moderation Decision
             |
       +-----+------+
       |            |
       v            v
     Allow        Delete
                    |
                    v
               Log Event
```

### Technologies

-   Pillow --- Image processing.
-   Tesseract OCR --- Text extraction.
-   Optional AI moderation --- Additional content analysis.

**Additional capabilities:**

-   Image and supported document scanning.
-   OCR blacklist matching.
-   Owner-defined protected-word vault.
-   Moderation event logging.
-   Automatic deletion when configured detection rules are triggered.
-   Files larger than 20 MB are skipped.

------------------------------------------------------------------------

## 5. 👋 Welcome & Goodbye System

Give every new member a personalized introduction.

### Features

-   Custom welcome messages.
-   Custom goodbye messages.
-   Photo and video welcome media.
-   Per-group configuration.
-   Automatic member-count information.
-   Optional CAPTCHA verification.

### Supported placeholders

  Placeholder    Description
  -------------- --------------------------
  `{fullname}`   Member's full name
  `{username}`   Telegram username
  `{mention}`    Clickable member mention
  `{id}`         Telegram user ID
  `{chatname}`   Group name
  `{count}`      Member count

### Commands

``` text
/setwelcome
/welcome on
/welcome off
```

Reply to supported media with `/setwelcome` to configure media-based
welcomes.

### CAPTCHA Verification

Miss Sona includes a human-verification workflow:

-   Verification button: `✅ Verify You Are Human`
-   Verification duration: 120 seconds.
-   Helps reduce automated bot entries.

------------------------------------------------------------------------

## 6. 🎯 Filters & Notes Management

Create custom automated responses for frequently used words, phrases,
and commands.

### Filters

``` text
/filter <trigger> <reply>
```

You can also reply to supported media using:

``` text
/filter <trigger>
```

**Available commands**

``` text
/filters
/stop <trigger>
/stopall
```

Each group's filters are stored independently.

### Notes

Save useful information and retrieve it whenever needed.

``` text
/setnote <name> <text>
/getnote <name>
/getnote #hashtag
/delnote <name>
```

Useful for:

-   Group rules.
-   Frequently asked questions.
-   Important announcements.
-   Community resources.
-   Saved instructions.

------------------------------------------------------------------------

## 7. 🧩 Word Jumble Game --- Stack Emojify

Bring some entertainment into your Telegram community with an
interactive word-guessing game.

### Game feedback system

  Emoji   Meaning
  ------- -----------------------------------
  🎯      Correct character and position
  🔀      Character exists but is misplaced
  🚫      Character is absent

### Game commands

``` text
/new
/new4
/new5
/new6
/word
/game
/top
/endgame
```

### Validated word dictionary

Miss Sona includes an embedded dictionary containing 1,831 words.

  Word length                    Available words
  ---------------------------- -----------------
  4 letters                                  546
  5 letters                                  648
  6 letters                                  639
  **Total of listed counts**           **1,833**

> Version note: The individual counts add up to 1,833, whereas the
> stated total is 1,831. Verify the embedded dictionary before
> publishing the final count.

Additional validation can use:

-   `wordfreq`
-   `enchant`

The validation system is designed to reject invalid or nonsensical
guesses such as `bkilsd` and `zzzz`.

------------------------------------------------------------------------

## 8. 🔗 Multi-Group Connection System

Manage multiple Telegram communities without repeatedly switching
between group chats.

### How it works

**Step 1 --- Connect your group**

In the target group:

``` text
/connect
```

The bot performs administrator verification.

**Step 2 --- Open private chat**

Start a conversation with Miss Sona.

**Step 3 --- Manage connections**

``` text
/connect
/connections
/disconnect
```

### Management capabilities

-   List connected groups.
-   Switch between connected communities.
-   Manage group settings through private chat.
-   Disconnect groups when necessary.
-   Resolve the effective group ID for private management operations.

This feature is especially useful for administrators managing multiple
communities.

------------------------------------------------------------------------

## 9. ⚙️ Additional Utilities

Miss Sona also includes several tools for everyday group administration.

  Command           Function
  ----------------- -----------------------------
  `/all`            Mention group members
  `/admins`         Mention administrators
  `/shadowreply`    Shadow-reply functionality
  `/rules`          Display group rules
  `/setrules`       Configure group rules
  `/privaterules`   Private rules workflow
  `/export`         Export supported bot data
  `/import`         Import supported bot data
  `/disable`        Disable supported commands
  `/id`             Display Telegram IDs
  `/support`        Display support information
  `/setlog`         Configure logging

### Mention throttling

To reduce excessive notifications:

-   Maximum 5 mentions.
-   3-second throttling interval.

------------------------------------------------------------------------

# 🏗️ Technical Architecture

Miss Sona is built using a lightweight, asynchronous Python
architecture.

### Technology stack

  Technology                 Purpose
  -------------------------- -------------------------------------
  Python                     Main programming language
  python-telegram-bot v20+   Telegram Bot API integration
  asyncio                    Asynchronous execution
  SQLite                     Persistent storage
  SQLite WAL                 Improved concurrent database access
  Pillow                     Image processing
  Tesseract                  OCR processing
  Transformers               Optional AI inference
  Detoxify                   Optional toxicity detection
  HTML ParseMode             Telegram message formatting

### Handler architecture

The application uses dedicated handler groups:

``` python
GROUP_TRACK = -1
G_SEC = 0
G_MAIN = 1
G_FB = 2
```

### Database architecture

Miss Sona uses SQLite with:

``` sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=10000;
```

Database design emphasizes:

-   Group-specific records.
-   Composite primary keys.
-   Chat-scoped queries.
-   Reduced cross-group data exposure.
-   Improved handling of concurrent database operations.

### Single-file structure

``` text
Miss-Sona/
│
├── miss_sona.py
├── requirements.txt
├── .env
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── LICENSE
├── README.md
│
├── assets/
│   └── miss-sona-banner.png
│
└── miss_sona.db
```

The main bot implementation is contained in:

``` text
miss_sona.py
```

------------------------------------------------------------------------

# 📦 Installation & Deployment

## Requirements

-   Python 3.8 or newer.
-   Telegram Bot Token.
-   Internet connection.
-   Telegram group administrator permissions.
-   Optional Tesseract installation for OCR.
-   Optional AI dependencies for advanced moderation.

### Step 1 --- Clone the repository

``` bash
git clone <YOUR_REPOSITORY_URL>
cd Miss-Sona
```

### Step 2 --- Install dependencies

``` bash
pip install -r requirements.txt
```

### Step 3 --- Install OCR support

On Ubuntu/Debian:

``` bash
sudo apt update
sudo apt install tesseract-ocr
```

Install Python OCR dependencies if required:

``` bash
pip install Pillow pytesseract
```

### Step 4 --- Install optional AI dependencies

``` bash
pip install transformers torch detoxify
```

The AI dependencies are optional and may require significant RAM, disk
space, and processing resources.

### Step 5 --- Configure environment variables

Create a `.env` file or export the following environment variables.

``` ini
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN

OWNER_ID=YOUR_TELEGRAM_USER_ID

DB_PATH=miss_sona.db

SUPPORT_CHAT_ID=-1001234567890

SUPPORT_LINK=https://t.me/SecretSquadSupport

TOS_AI_MODEL=KoalaAI/Text-Moderation

TOS_AI_THRESHOLD=0.75
```

### Configuration reference

  Variable             Description
  -------------------- ---------------------------------
  `BOT_TOKEN`          Telegram bot token (required)
  `OWNER_ID`           Bot owner's Telegram ID
  `DB_PATH`            SQLite database location
  `SUPPORT_CHAT_ID`    Optional moderation log channel
  `SUPPORT_LINK`       Support community URL
  `TOS_AI_MODEL`       Optional AI moderation model
  `TOS_AI_THRESHOLD`   AI moderation threshold

### Step 6 --- Run Miss Sona

``` bash
python miss_sona.py
```

🎉 Your Miss Sona bot is now running!

------------------------------------------------------------------------

# 🐳 Docker Deployment

Miss Sona can also be deployed using Docker.

### Start the application

``` bash
docker compose up -d
```

### View logs

``` bash
docker compose logs -f
```

### Stop the application

``` bash
docker compose down
```

Ensure your Docker configuration preserves the SQLite database and
environment variables between restarts.

------------------------------------------------------------------------

# 📋 Command Reference

A quick reference to the major commands available in Miss Sona.

  Category         Commands
  ---------------- ----------------------------------------------------------
  Moderation       `/ban`, `/tban`, `/mute`, `/tmute`, `/kick`, `/unban`
  Warnings         `/warn`, `/warns`, `/rmwarn`, `/resetwarn`
  Administration   `/promote`, `/demote`, `/adminlist`
  Approval         `/approve`, `/unapprove`, `/trust`, `/untrust`
  Security         `/lock`, `/unlock`, `/locks`, `/allowlist`, `/blocklist`
  Welcome          `/setwelcome`, `/welcome`
  Filters          `/filter`, `/filters`, `/stop`, `/stopall`
  Notes            `/setnote`, `/getnote`, `/delnote`
  AI Protection    `/tos on`, `/tos off`
  Word Game        `/new`, `/new4`, `/new5`, `/new6`, `/game`, `/top`
  Connections      `/connect`, `/connections`, `/disconnect`
  Utilities        `/rules`, `/setrules`, `/export`, `/import`, `/id`
  Support          `/support`, `/setlog`

For the complete command list and usage instructions:

``` text
/help
```

or

``` text
/start
```

------------------------------------------------------------------------

# 🔐 Privacy, Security & Group Isolation

Security and data isolation are fundamental design priorities.

### Group isolation

Miss Sona follows a chat-scoped database architecture.

-   Every group-specific table includes `chat_id`.
-   Group-specific database queries filter by `chat_id`.
-   Filters, notes, and moderation records are isolated between
    communities.
-   Private management operations resolve the appropriate effective
    group ID.

### Database reliability

-   SQLite WAL mode.
-   Configurable busy timeout.
-   Composite database keys.
-   Persistent storage.

### Message formatting security

The bot uses Telegram HTML ParseMode and `html.escape()` to reduce HTML
injection and formatting problems when handling user-provided text.

### Logging policy

Moderation logs are sent to the configured `SUPPORT_CHAT_ID`.

Log messages use the prefix:

``` text
🛡️ Miss Sona Log | The Secret Squad
```

No logging channel is required if logging is not configured.

### Owner controls

Certain privileged operations, including protected vault management and
bypass functionality, are restricted to the configured owner.

> Administrators should protect their bot token, restrict access to
> database backups, configure appropriate Telegram permissions, and
> follow applicable privacy and data-retention requirements.

------------------------------------------------------------------------

# 🎨 Branding & Identity

```{=html}
<p align="center">
```
`<b>`{=html}👑 MISS SONA`</b>`{=html} `<br>`{=html} `<i>`{=html}The
Secret Squad Guardian`</i>`{=html} `<br>`{=html}`<br>`{=html}
`<b>`{=html}Secure • Smart • Powerful • Always There`</b>`{=html}
```{=html}
</p>
```
Miss Sona represents a modern approach to Telegram community management,
combining automated safety, practical administration, and community
engagement.

### Our philosophy

-   🛡️ Protect communities.
-   🤖 Automate repetitive administration.
-   🔐 Respect group privacy.
-   ⚡ Keep management efficient.
-   💜 Make Telegram communities more enjoyable.

------------------------------------------------------------------------

# 📞 Support & Community

```{=html}
<p align="center">
```
`<b>`{=html}Built with ❤️ by The Secret Squad`</b>`{=html}
```{=html}
</p>
```
  --------------------------------------------------------------------------------------------
  Platform                            Link
  ----------------------------------- --------------------------------------------------------
  Telegram Support                    [@SecretSquadSupport](https://t.me/SecretSquadSupport)

  GitHub                              Open an issue in the project repository

  License                             MIT
  --------------------------------------------------------------------------------------------

For technical assistance, bug reports, feature suggestions, and
improvements, contact the support community.

------------------------------------------------------------------------

# 🧾 Project Information

  Property           Details
  ------------------ ---------------------------
  Project            Miss Sona
  Identity           The Secret Squad Guardian
  Version            v6.0 TOS-AI
  Release date       18 November 2026
  Main file          `miss_sona.py`
  Architecture       Single-file application
  Approximate size   1,770 lines
  Language           Python
  Framework          python-telegram-bot v20+
  Database           SQLite WAL
  Processing         asyncio
  AI moderation      Optional
  OCR                Optional
  License            MIT

------------------------------------------------------------------------

# 📜 License

This project is distributed under the **MIT License**.

You are permitted to use, modify, distribute, and adapt the software in
accordance with the license terms.

See the `LICENSE` file for complete details.

------------------------------------------------------------------------

```{=html}
<p align="center">
```
👑 `<b>`{=html}THE SECRET SQUAD`</b>`{=html} `<br>`{=html}
`<i>`{=html}BUILD • MANAGE • PROTECT`</i>`{=html}
`<br>`{=html}`<br>`{=html} `<b>`{=html}Miss Sona --- Your Community, Our
Responsibility.`</b>`{=html} `<br>`{=html}`<br>`{=html} Made with ❤️ for
Telegram communities.
```{=html}
</p>
```
```{=html}
<p align="center">
```
`<sub>`{=html}© 2026 The Secret Squad. All rights reserved under the
applicable MIT License terms.`</sub>`{=html}
```{=html}
</p>
```
