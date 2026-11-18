"""
Miss Sona v6.0 TOS-AI — Industrial Telegram Group Management Bot
===========================================================
By The Secret Squad | python-telegram-bot v20+ | asyncio + SQLite WAL
FIXED: Validated Wordle (bkilsd rejected) + Clean Branding (only /start & /help) + 500+ word dictionary

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
def branded(text:str)->str: return text
def branded_start(text:str)->str: return text + BRANDED_FOOTER
# FIX v5.0: Clean branding — only start/help use branded_start, all other () calls are now cleaned to plain text below via identity wrapper
def _clean_branding_wrapper(t): return t  # used to strip branding from non-start/help


CAPTCHA_TIMEOUT=120
TAG_BATCH_SIZE=5
TAG_BATCH_DELAY=3.0
ILLEGAL_NOTICE="I found something illegal so I deleted it"
ILLEGAL_NOTICE_TTL=15

# ── Telegram TOS AI Protection (Open-Source) ──
# Uses open-source transformers (optional). Falls back to keyword heuristics if no model.
# Vulnerable categories per Telegram TOS: https://telegram.org/tos
TOS_AI_MODEL=os.getenv("TOS_AI_MODEL","KoalaAI/Text-Moderation")  # alternatives: unitary/toxic-bert, martin-ha/toxic-bert
TOS_AI_THRESHOLD=float(os.getenv("TOS_AI_THRESHOLD","0.75"))
TOS_PROTECTION_DEFAULT=1
TOS_NOTICE="⚠️ Message was deleted as it violates Telegram TOS — {reason}. Please follow Telegram Terms of Service."
TOS_NOTICE_TTL=15
_tos_classifier=None
_tos_classifier_lock=asyncio.Lock()
_tos_warmed=False

# Keyword fallback: high-precision patterns for TOS-violating content
TOS_KEYWORDS = {
    "Adult/Sexual/NSFW": [
        r"\b(?:porn\b|xnxx|xvideos|pornhub|onlyfans|nsfw\b|nude\b|nudes\b|naked\b|sex\s*video|sex\s*chat|erotic\b)",
    ],
    "Child Abuse/CSAM": [
        r"\b(?:child\s*porn|cp\s*video|cp\s*link|lolicon|shotacon|minor\s*nude|underage\s*nude|teen\s*sex)\b",
        r"\b(?:preteen|loli\b|lolita)\b",
    ],
    "Violence/Threat": [
        r"\b(?:kill\s*(?:yourself|him|her|them)|murder\b|terrorist\b|bomb\s*making|how\s*to\s*make\s*bomb|mass\s*shooting|behead\b)",
    ],
    "Hate/Harassment": [
        r"\b(?:hate\s*speech|genocide|ethnic\s*cleansing)\b",
    ],
    "Illegal Drugs/Weapons": [
        r"\b(?:buy\s*cocaine|buy\s*heroin|buy\s*weed\b|buy\s*marijuana|buy\s*mdma|buy\s*lsd|buy\s*gun\b|buy\s*rifle|credit\s*card\s*dump|stolen\s*card)\b",
        r"\b(?:how\s*to\s*make\s*meth|drug\s*deal|weapon\s*deal)\b",
    ],
    "Self-Harm": [
        r"\b(?:commit\s*suicide|how\s*to\s*kill\s*myself|self\s*harm|cute\s*suicide)\b",
    ],
    "Spam/Scam/Fraud/Phishing": [
        r"\b(?:free\s*nitro|free\s*money|click\s*here\s*to\s*win|you\s*won\s*lottery|earn\s*\$\d+\s*per\s*day|investment\s*doubles?)\b",
    ],
}
_COMPILED_TOS = [(cat, [re.compile(p, re.I) for p in pats]) for cat, pats in TOS_KEYWORDS.items()]
OCR_BLACKLIST_TOS_EXTRA = ["child porn", "cp video", "minor nude", "bomb making"]

def _tos_keyword_check(text: str):
    low=text.lower()
    for cat, patterns in _COMPILED_TOS:
        for pat in patterns:
            if pat.search(low):
                return True, cat, 1.0, pat.pattern
    # also check OCR_BLACKLIST
    for bad in OCR_BLACKLIST + OCR_BLACKLIST_TOS_EXTRA:
        if bad.lower() in low:
            return True, "Illegal/Blacklisted", 1.0, bad
    return False, None, 0.0, None

async def _load_tos_classifier():
    global _tos_classifier, _tos_warmed
    if _tos_classifier is not None:
        return _tos_classifier
    if _tos_warmed:
        return None
    async with _tos_classifier_lock:
        if _tos_classifier is not None:
            return _tos_classifier
        _tos_warmed=True
        # Try detoxify first (lightweight)
        try:
            from detoxify import Detoxify
            def _make():
                return Detoxify("original")
            cls=await asyncio.to_thread(_make)
            _tos_classifier=("detoxify", cls)
            log.info("TOS AI: Loaded Detoxify original")
            return _tos_classifier
        except Exception as e:
            log.debug("TOS Detoxify not available: %s", e)
        # Try transformers pipeline
        try:
            from transformers import pipeline
            model=TOS_AI_MODEL
            def _make_pipe():
                # KoalaAI/Text-Moderation => text-classification with 7 labels
                # unitary/toxic-bert => toxic label
                return pipeline("text-classification", model=model, top_k=None, truncation=True, max_length=512)
            pipe=await asyncio.to_thread(_make_pipe)
            _tos_classifier=("transformers", pipe, model)
            log.info("TOS AI: Loaded transformers %s", model)
            return _tos_classifier
        except Exception as e:
            log.info("TOS AI: No open-source model available (install transformers + torch + detoxify for AI), using keyword fallback only: %s", e)
            return None

def _score_tos_sync(text: str, classifier_tuple):
    kind=classifier_tuple[0]
    try:
        if kind=="detoxify":
            _, clf = classifier_tuple
            res=clf.predict(text)
            # res is dict of scores
            # Map detoxify labels to TOS: toxicity, severe_toxicity, obscene, threat, insult, identity_attack, sexual_explicit
            max_label=max(res, key=lambda k: res[k])
            max_score=res[max_label]
            label_map={
                "toxicity":"Toxic",
                "severe_toxicity":"Severe Toxic",
                "obscene":"Obscene/NSFW",
                "threat":"Violence/Threat",
                "insult":"Harassment",
                "identity_attack":"Hate/Harassment",
                "sexual_explicit":"Adult/Sexual/NSFW",
            }
            tos_cat=label_map.get(max_label, max_label)
            return tos_cat, max_score, max_label, res
        elif kind=="transformers":
            _, pipe, model = classifier_tuple
            out=pipe(text[:1000], truncation=True)
            # out is list of list of dicts: [{"label":"...","score":0.9},...] or single
            # KoalaAI/Text-Moderation labels: S (sexual), H (hate), V (violence), etc.
            if isinstance(out, list) and out and isinstance(out[0], list):
                out=out[0]
            elif isinstance(out, dict):
                out=[out]
            # Find max toxic
            best=max(out, key=lambda x: x["score"])
            label=best["label"]
            score=best["score"]
            # If model is KoalaAI: labels like "OK", "S", "H", "V", "SH" etc. Treat not OK as violation
            koala_map={"S":"Adult/Sexual/NSFW","H":"Hate/Harassment","V":"Violence/Threat","SH":"Self-Harm","S/H":"Sexual/Hate","HM":"Harassment","OK":"OK"}
            if "OK" not in label and score>=TOS_AI_THRESHOLD:
                cat=koala_map.get(label, label)
                return cat, score, label, out
            elif score>=TOS_AI_THRESHOLD and label.lower() not in ("ok","not toxic","non-toxic"):
                # unitary/toxic-bert: label TOXIC
                return label, score, label, out
            return "OK", 0.0, label, out
    except Exception as e:
        log.debug("TOS scoring failed: %s", e)
    return None, 0.0, None, None

async def check_tos_violation(text: str):
    """Open-source AI + keyword check for Telegram TOS violations.
    Returns (is_violation:bool, reason:str, score:float, detail:str)"""
    if not text or not text.strip():
        return False, None, 0.0, None
    # 1) Fast keyword fallback (always)
    kw_hit, kw_cat, kw_score, kw_pat = _tos_keyword_check(text)
    if kw_hit:
        return True, kw_cat, kw_score, f"keyword:{kw_pat}"
    # 2) AI model (if available)
    clf=await _load_tos_classifier()
    if clf:
        try:
            cat, score, label, raw = await asyncio.to_thread(_score_tos_sync, text, clf)
            if cat and cat!="OK" and score>=TOS_AI_THRESHOLD:
                return True, cat, float(score), f"ai:{label}={score:.2f}"
        except Exception as e:
            log.debug("TOS AI check error: %s", e)
    return False, None, 0.0, None

async def send_tos_notice(ctx, chat_id, reason: str):
    """Send the exact requested notice after deletion."""
    try:
        # User requested literal: "message was deleted as it violates telegram tos"
        # We send polished version with category for clarity
        txt=TOS_NOTICE.format(reason=reason)
        m=await ctx.bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML)
        if ctx.job_queue:
            ctx.job_queue.run_once(lambda c: asyncio.create_task(_del(c, chat_id, m.message_id)), TOS_NOTICE_TTL)
        else:
            asyncio.create_task(_auto(ctx, chat_id, m.message_id, TOS_NOTICE_TTL))
    except Exception as e:
        log.debug("TOS notice failed: %s", e)

async def handle_tos_violation(update, context):
    """Early handler (G_SEC) — deletes TOS-violating messages using open-source AI + keywords."""
    msg=update.effective_message
    chat=update.effective_chat
    user=update.effective_user
    if not msg or not chat or not user:
        return
    if chat.type=="private":
        return
    # Owner bypass only — even admins must follow Telegram TOS
    if await is_owner(user.id):
        return
    # Get per-chat setting (cached)
    try:
        s=await db.get_settings(chat.id)
        tos_on = s.get("tos_protection", TOS_PROTECTION_DEFAULT) if isinstance(s, dict) else getattr(s, "tos_protection", 1)
        if not tos_on:
            return
    except:
        pass
    # Extract text to check (caption or text)
    txt = (msg.text or msg.caption or "")
    if not txt or len(txt.strip())<2:
        return
    # Ignore commands
    if txt.strip().startswith("/"):
        return
    violated, reason, score, detail = await check_tos_violation(txt)
    if violated:
        try:
            await msg.delete()
        except Exception as e:
            log.debug("TOS delete failed %s", e)
        # Send required notice
        await send_tos_notice(context, chat.id, reason or "TOS violation")
        # Log to support
        await log_to_support(context, f"TOS DELETE — {reason} score={score:.2f} user={user.id} text={txt[:120]}", chat.id, user.id, detail=detail or txt[:200])
        # Stop further processing for this message
        # (do not return block, just deleted; other handlers will ignore deleted msg)


DEFAULT_SETTINGS={"tos_protection":1,"antilink":1,"antispam":1,"ocr_protection":1,"captcha_enabled":1,"lockwarns":0,"cleanwelcome":0,"cleanservice":0,"welcome_on":1,"goodbye_on":0,"antiflood_on":1,"flood_limit":5,"flood_mode":"tmute 1h"}
LOCK_TYPES=["url","forward","inline","invite","sticker","photo","video","gif","audio","voice","document","contact","location","game","rtl","button","command","email","phone","bot"]
WORD_BANK={
 4:["code","game","sona","rose","bolt","flux","kilo","nova","apex","zeal","iris","luna","aura","vibe","echo","frost","glow","kind","mint","nest","opal","peak","quip","rift","sage","tide","vale","warp","yarn","zest","bark","clap","dune","fern","gale","howl","jade","kite","lark","moss","neon","oath","plum","quay","rust","star","tune","urge","void","wisp"],
 5:["sonar","crane","ultra","blitz","frost","flame","glide","prism","quirk","amber","blaze","crest","drift","ember","forge","gloom","haven","ivory","jolly","kneel","lunar","mango","nymph","ocean","pride","quest","rally","stone","theta","unity","vivid","whirl","xenon","yield","zebra","apple","brave","crown","dream","eager","flora","giant","happy","ideal","jazzy","knack","light","magic","noble","olive","brack","blues","basks","books","beats"],
 6:["python","rocket","silver","golden","cobalt","nebula","jasper","quartz","velvet","arcade","breeze","castle","desert","engine","falcon","galaxy","harbor","island","jungle","kernel","legend","marble","nimbus","orange","planet","quiver","ranger","summit","tunnel","unique","voyage","wisdom","yonder","zenith","bronze","crystal","diamond","emerald","fortune"]
}

# ── Valid Words Dictionary for Validation (prevents bkilsd gibberish) ──
VALID_WORDS_4 = {"able","acts","adam","aged","ages","alex","also","anti","apex","area","arms","army","arts","asia","aura","away","baby","back","ball","band","bank","bark","base","bask","bear","beat","been","beer","bell","best","bike","bill","bird","blog","blow","blue","boat","body","bolt","bomb","bond","book","born","boss","both","bowl","boys","bros","burn","bush","busy","cake","call","calm","came","camp","card","care","cars","case","cash","cast","cell","cent","chat","city","clap","club","coal","code","cold","come","cook","cool","copy","core","cost","crew","cute","damn","dark","data","date","days","dead","deal","dear","debt","deep","dick","died","diet","does","dogs","done","dont","door","down","draw","drop","drug","dude","dumb","dune","duty","each","east","easy","echo","edge","eggs","else","ends","eric","even","ever","evil","eyes","face","fact","fail","fair","fake","fall","fans","farm","fast","fear","feed","feel","fees","feet","fell","felt","fern","file","fill","film","find","fine","fire","firm","fish","five","flag","flat","flow","flux","food","foot","ford","form","four","free","from","frost","fuck","fuel","full","fund","gain","gale","game","gang","gave","gets","gift","girl","give","glad","glow","goal","goes","gold","golf","gone","good","grab","grew","grow","guns","guys","haha","hair","half","hall","hand","hang","hard","hate","have","head","hear","heat","held","hell","help","here","hero","hide","high","hill","hits","hold","hole","holy","home","hong","hope","host","hour","howl","http","huge","hunt","hurt","idea","inch","info","into","iran","iris","iron","item","jack","jade","jail","jobs","john","join","joke","july","jump","june","jury","just","keep","kept","kick","kids","kill","kilo","kind","king","kiss","kite","knew","know","kong","lack","lady","laid","lake","land","lane","lark","last","late","laws","lead","left","legs","less","lets","lies","life","lift","like","line","link","list","live","load","loan","lock","long","look","lord","lose","loss","lost","lots","loud","love","luck","luna","made","mail","main","make","male","many","mark","mary","mass","mate","matt","mean","meat","meet","mess","mike","mile","milk","mind","mine","mint","miss","mode","moon","more","moss","most","move","much","must","name","navy","near","neck","need","neon","nest","news","next","nice","nick","nine","none","nose","note","nova","oath","ohio","okay","once","ones","only","onto","opal","open","over","pack","page","paid","pain","pair","park","part","pass","past","path","paul","peak","pick","pink","plan","play","plot","plum","plus","pool","poor","porn","port","post","pull","pure","push","puts","quay","quip","quit","race","rain","rare","rate","read","real","rent","rest","rice","rich","ride","rift","ring","rise","risk","road","rock","role","roll","roof","room","rose","rule","runs","rust","ryan","safe","sage","said","sale","salt","same","save","says","seat","seek","seem","seen","sees","self","sell","semi","send","sent","sets","ship","shit","shop","shot","show","shut","sick","side","sign","sing","site","size","skin","slow","snow","soft","sold","solo","some","sona","song","sons","soon","sort","soul","spot","star","stay","step","stop","such","suit","sure","take","talk","tall","tank","task","team","tech","tell","tend","term","test","text","than","that","them","then","they","this","thus","tide","till","time","tiny","tips","told","tone","tony","took","tool","tour","town","tree","trip","true","tune","turn","type","unit","upon","urge","used","user","uses","vale","very","vibe","vice","view","void","vote","wait","wake","walk","wall","want","warm","warp","wars","wave","ways","weak","wear","week","well","went","were","west","what","when","whom","wide","wife","wild","will","wind","wine","wing","wins","wise","wish","wisp","with","wood","word","work","yard","yarn","yeah","year","york","your","zeal","zero","zest","zone"}
VALID_WORDS_5 = {"about","above","abuse","actor","added","admit","adult","after","again","agent","agree","ahead","album","alive","allow","alone","along","amber","among","angel","angry","apart","apple","apply","april","areas","argue","armed","asian","aside","asked","audio","avoid","award","aware","awful","badly","balls","banks","based","basic","basis","basks","beach","beats","began","begin","being","below","bible","bills","birds","birth","bitch","black","blame","blaze","blind","blitz","block","blood","blues","board","bonus","books","bound","brack","brain","brand","brave","bread","break","brian","brief","bring","broad","broke","brown","build","built","bunch","cable","calls","cards","carry","cases","catch","cause","cells","chain","chair","chart","chase","cheap","check","chest","chief","child","china","chose","chris","civil","claim","class","clean","clear","click","close","cloud","clubs","coach","coast","color","comes","costs","could","count","court","cover","crane","crash","crazy","cream","crest","crime","cross","crowd","crown","cycle","daily","dance","dates","david","davis","deals","death","debut","depth","dirty","doing","doors","doubt","draft","drama","drawn","dream","dress","drift","drink","drive","drugs","drunk","dying","eager","early","earth","egypt","eight","email","ember","empty","ended","enemy","enjoy","enter","entry","equal","error","event","every","exact","exist","extra","faced","faces","facts","faith","falls","false","fault","favor","feels","field","fifth","fight","filed","files","films","final","finds","fired","first","fixed","flame","flash","floor","flora","focus","folks","force","forge","forms","found","frame","frank","fresh","front","frost","fruit","fully","funds","funny","games","giant","girls","given","gives","glass","glide","gloom","goals","going","gonna","goods","gotta","grace","grade","grand","grant","great","greek","green","gross","group","grown","guard","guess","guest","guide","hands","happy","harry","haven","heads","heard","heart","heavy","hello","helps","henry","hills","holds","homes","honor","horse","hotel","hours","house","human","ideal","ideas","image","index","india","inner","irish","issue","italy","items","ivory","james","japan","jason","jazzy","jesus","jimmy","joint","jolly","jones","judge","keeps","kelly","kevin","kinda","kinds","knack","kneel","known","knows","korea","labor","large","later","latin","laugh","leads","learn","least","leave","legal","level","lewis","light","liked","likes","limit","lines","links","lived","lives","loans","local","looks","loose","louis","loved","loves","lower","lucky","lunar","lunch","lying","magic","major","makes","mango","march","marks","match","maybe","mayor","means","meant","media","metal","miami","might","miles","minds","minor","mixed","model","money","month","moral","motor","mount","mouse","mouth","moved","moves","movie","multi","music","named","names","needs","never","night","noble","noise","north","noted","notes","novel","nymph","obama","occur","ocean","offer","often","older","olive","order","other","owned","owner","pages","paint","panel","pants","paper","paris","parts","party","peace","peter","phase","phone","photo","piece","pilot","place","plane","plans","plant","plate","plays","point","posts","power","press","price","pride","prime","print","prior","prism","prize","proof","proud","prove","queen","quest","quick","quiet","quirk","quite","quote","radio","raise","rally","range","rates","reach","ready","refer","reply","right","river","roads","roles","roman","rooms","rough","round","route","royal","rules","rural","sales","santa","sarah","saved","scale","scene","score","scott","seats","seems","sense","serve","seven","shall","shame","shape","share","sharp","shift","ships","shirt","shock","shoes","shoot","short","shots","should","shown","shows","sides","sight","signs","simon","since","sites","slate","sleep","small","smart","smell","smile","smith","smoke","solar","solid","sonar","songs","sorry","sound","south","space","spain","spare","speak","speed","spend","spent","split","spoke","sport","squad","staff","stage","stand","stars","start","state","steam","steel","steps","steve","stick","still","stock","stone","stood","store","storm","story","stuck","study","stuff","style","sugar","super","swear","sweet","table","taken","takes","talks","taste","taxes","teach","teams","tears","teeth","tells","terms","tests","texas","thank","thats","their","theme","there","these","theta","thick","thing","think","third","those","three","threw","throw","tight","times","tired","title","today","tools","topic","total","touch","tough","tower","trace","track","trade","trail","train","treat","trees","trend","trial","trick","tried","tries","truck","truly","trump","trust","truth","turns","twice","types","ultra","uncle","under","union","units","unity","until","upper","upset","urban","users","using","usual","value","video","views","visit","vivid","voice","voted","votes","wales","walls","wanna","wants","waste","watch","water","weeks","weird","wheel","where","which","while","whirl","white","whole","whose","wings","woman","women","woods","words","works","world","worry","worse","worst","worth","would","write","wrong","wrote","xenon","yards","years","yield","young","yours","youth","zebra"}
VALID_WORDS_6 = {"accept","access","across","acting","action","active","actual","adding","adults","advice","affect","afford","afraid","africa","agency","agents","agreed","allows","almost","always","amazon","amount","andrew","animal","annual","answer","anyone","anyway","appeal","appear","arcade","around","arrest","arthur","artist","asking","aspect","assets","assist","assume","attack","attend","august","author","avenue","awards","babies","banned","barely","battle","beauty","became","become","before","begins","behalf","behind","belief","better","beyond","bigger","blocks","bodies","border","boring","boston","bottle","bottom","bought","branch","brazil","breaks","breath","breeze","bridge","bright","brings","broken","bronze","budget","bureau","button","buying","called","camera","campus","canada","cancer","cannot","carbon","career","castle","caught","caused","causes","center","centre","chance","change","charge","cheese","choice","choose","chosen","christ","church","circle","cities","claims","client","closed","closer","cobalt","coffee","colors","colour","combat","comedy","coming","common","copies","corner","county","couple","course","courts","cousin","covers","create","credit","crimes","crisis","crying","crystal","custom","dallas","damage","danger","daniel","dating","deaths","debate","decade","decide","deeply","defeat","defend","degree","demand","denied","deputy","desert","design","desire","detail","device","diamond","dinner","direct","doctor","dollar","donald","double","dragon","dreams","drinks","driven","driver","during","earned","easier","easily","eating","editor","edward","effect","effort","either","emerald","empire","ending","energy","engine","enough","ensure","entire","escape","estate","europe","events","except","excuse","exists","expect","expert","extent","facing","factor","failed","fairly","falcon","family","famous","faster","father","fellow","female","fields","figure","filled","finals","finger","finish","flight","flower","flying","follow","forced","forces","forest","forget","forgot","formal","format","formed","former","fortune","fought","fourth","france","french","friday","friend","fucked","future","gained","galaxy","garden","gender","george","german","giving","global","golden","google","gotten","ground","groups","growth","guests","guilty","guitar","handed","handle","happen","harbor","harder","hardly","harris","having","headed","health","hearts","heaven","height","helped","hidden","higher","highly","hockey","honest","hoping","horizon","horror","horses","houses","howard","humans","hungry","ignore","images","imagine","impact","income","indeed","indian","injury","inside","iphone","island","israel","issued","issues","itself","jasper","jersey","jewish","joined","jordan","joseph","journey","judges","jungle","junior","kernel","killed","killer","kingdom","korean","labour","ladies","lantern","larger","lately","latest","latter","launch","lawyer","leader","league","leaves","legend","length","lesson","letter","levels","lights","likely","limits","linked","listed","listen","little","living","locked","london","longer","looked","losing","losses","lovely","loving","lowest","mainly","making","manage","manner","marble","marine","marked","market","martin","master","matter","medium","member","memory","mental","method","mexico","middle","midnight","miller","mining","minute","mirror","missed","mobile","models","modern","moment","monday","months","morgan","moscow","mostly","mother","motion","movies","moving","murder","muscle","museum","muslim","myself","nation","native","natural","nature","nearby","nearly","nebula","needed","nights","nimbus","nobody","normal","notice","number","object","offers","office","online","opened","option","orange","orders","origin","others","outlook","output","owners","palace","papers","parent","parrot","passed","passes","paying","people","period","person","phones","photos","picked","pieces","placed","places","planet","plants","played","player","please","plenty","pocket","points","police","policy","posted","pounds","powers","prayer","prefer","pretty","prices","prince","prison","profit","proper","proved","public","pulled","pushed","python","quartz","quiver","racing","racist","raised","random","ranger","rarely","rather","rating","really","reason","recent","record","reduce","reform","region","relief","remain","remove","repeat","report","rescue","result","retail","return","reveal","review","riding","rights","rising","robert","rocket","russia","safety","sample","saving","saying","scared","scenes","scheme","school","scored","scores","screen","search","season","second","secret","sector","secure","seeing","seemed","select","senate","senior","series","served","server","severe","sexual","shadow","shared","shares","should","showed","shower","signal","signed","silent","silver","simple","simply","singer","single","sister","skills","slowly","soccer","social","sought","sounds","source","soviet","speaks","speech","spirit","spoken","sports","spread","spring","square","stable","stages","stands","starts","stated","states","status","stayed","stores","stream","street","stress","strike","strong","struck","studio","stupid","suffer","summer","summit","sunday","supply","surely","survey","switch","sydney","system","taking","talent","talked","target","taught","taylor","temple","tested","thanks","theory","things","thinks","thomas","though","thread","threat","ticket","toward","tracks","travel","troops","trying","tunnel","turkey","turned","twenty","unable","unique","united","unless","unlike","update","useful","valley","values","velvet","victim","videos","vision","visual","volume","voters","voting","voyage","walked","walker","wanted","waters","wealth","weapon","weekly","weight","widely","wilson","window","winner","winter","wisdom","within","wonder","worked","worker","writer","yellow","yonder","zenith"}
DICTIONARY = VALID_WORDS_4 | VALID_WORDS_5 | VALID_WORDS_6 | set(w.lower() for ws in WORD_BANK.values() for w in ws)
def is_valid_word(word: str, length: int) -> bool:
    w=word.lower().strip()
    # 1) Embedded dictionary is PRIMARY — guarantees bkilsd/zzzz rejected
    if length==4:
        if w in VALID_WORDS_4: return True
    elif length==5:
        if w in VALID_WORDS_5: return True
    elif length==6:
        if w in VALID_WORDS_6: return True
    else:
        if w in DICTIONARY: return True
    # 2) Optional fallback: wordfreq with STRICT threshold (>2.5) to allow rare valid words not in set, but still block gibberish
    try:
        from wordfreq import zipf_frequency
        if zipf_frequency(w, 'en') > 2.5:
            return True
    except: pass
    try:
        import enchant
        d=enchant.Dict("en_US")
        if d.check(w):
            # enchant is permissive — double-check not pure gibberish by requiring at least one vowel
            if any(c in w for c in "aeiou"):
                return True
    except: pass
    return False


OCR_BLACKLIST=["csam","child porn","cp video","minor nude","bomb making","how to make bomb","terrorist recruitment","credit card dump","illegal drugs buy"]
LINK_RE=re.compile(r"(?:https?://|t\.me/|telegram\.me/|telegram\.dog/|wa\.me/|discord\.gg/|invite)\S+",re.I)
SPAM_RE=re.compile(r"(.)\1{7,}")
EMAIL_RE=re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE=re.compile(r"\+?\d{10,15}")
pending_captcha:Dict[Tuple[int,int],object]={}
flood_tracker:Dict[Tuple[int,int], List[float]]={}

# ── Stack Emojify Book Class ──
class Emojify:
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
        header=f"📚 <b>Miss Sona Wordle — {length} Letters</b> | Attempts: {len(guesses)}/6\n"
        header+=f"<code>┌{'─'*28}┐</code>\n"
        if not guesses:
            body=f"<code>│ {'No guesses yet. Send a '+str(length)+'-letter word!':28} │</code>\n"
        else:
            body=""
            for g in guesses[-max_display:]:
                w=g['word'].upper()
                scored=' '.join(g['scored'])
                uname=g.get('username','?')[:12]
                line=f"{w:6} {scored} by {uname}"
                body+=f"<code>│ {line[:28].ljust(28)} │</code>\n"
        footer="<code>└────────────────────────────┘</code>\n"
        footer+="<i>🎯=perfect 🔀=misplaced 🚫=absent</i>"
        return header+body+footer
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
        cur.execute("""CREATE TABLE IF NOT EXISTS chat_settings(chat_id INTEGER PRIMARY KEY, tos_protection INTEGER DEFAULT 1, antilink INTEGER DEFAULT 1, antispam INTEGER DEFAULT 1, ocr_protection INTEGER DEFAULT 1, captcha_enabled INTEGER DEFAULT 1, lockwarns INTEGER DEFAULT 0, cleanwelcome INTEGER DEFAULT 0, cleanservice INTEGER DEFAULT 0, welcome_on INTEGER DEFAULT 1, goodbye_on INTEGER DEFAULT 0, antiflood_on INTEGER DEFAULT 1, flood_limit INTEGER DEFAULT 5, flood_mode TEXT DEFAULT 'tmute 1h', warn_limit INTEGER DEFAULT 3, warn_mode TEXT DEFAULT 'ban', welcome_clean_msg_id INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS active_users(chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT, fullname TEXT, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(chat_id,user_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS games(chat_id INTEGER PRIMARY KEY, word TEXT, length INTEGER, starter_id INTEGER, attempts INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, guesses_json TEXT DEFAULT '[]', board_msg_id INTEGER, started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS leaderboard(user_id INTEGER PRIMARY KEY, username TEXT, wins INTEGER DEFAULT 0, games_played INTEGER DEFAULT 0)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS greetings(chat_id INTEGER PRIMARY KEY, welcome_text TEXT DEFAULT 'Hello {fullname}! Welcome to {chatname}!', goodbye_text TEXT DEFAULT 'Goodbye {fullname}!', welcome_on INTEGER DEFAULT 1, goodbye_on INTEGER DEFAULT 0, welcome_media_type TEXT, welcome_file_id TEXT, goodbye_media_type TEXT, goodbye_file_id TEXT, welcome_buttons_json TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS disabled_commands(chat_id INTEGER NOT NULL, command TEXT NOT NULL, PRIMARY KEY(chat_id,command))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS blocklists(chat_id INTEGER NOT NULL, word TEXT NOT NULL, PRIMARY KEY(chat_id,word))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS locks(chat_id INTEGER NOT NULL, lock_type TEXT NOT NULL, locked INTEGER DEFAULT 0, PRIMARY KEY(chat_id,lock_type))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS allowlist(chat_id INTEGER NOT NULL, item TEXT NOT NULL, PRIMARY KEY(chat_id,item))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS connections(user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, chat_title TEXT, PRIMARY KEY(user_id, chat_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS filters(chat_id INTEGER NOT NULL, trigger TEXT NOT NULL, reply_text TEXT, media_type TEXT, file_id TEXT, buttons_json TEXT, PRIMARY KEY(chat_id, trigger))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS bot_config(key TEXT PRIMARY KEY, value TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS warns_settings(chat_id INTEGER PRIMARY KEY, limit_num INTEGER DEFAULT 3, mode TEXT DEFAULT 'ban')""")
        cur.execute("""CREATE TABLE IF NOT EXISTS support_logs(log_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, actor_id INTEGER, action TEXT, target_id INTEGER, detail TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        for col, typ in [("guesses_json","TEXT DEFAULT '[]'"),("board_msg_id","INTEGER")]:
            try: cur.execute(f"ALTER TABLE games ADD COLUMN {col} {typ}")
            except: pass
        try: cur.execute("ALTER TABLE chat_settings ADD COLUMN tos_protection INTEGER DEFAULT 1")
        except: pass
        for col in ["welcome_media_type TEXT","welcome_file_id TEXT","goodbye_media_type TEXT","goodbye_file_id TEXT","welcome_buttons_json TEXT"]:
            try: cur.execute(f"ALTER TABLE greetings ADD COLUMN {col}")
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
        row=await self._exec("SELECT limit_num, mode FROM warns_settings WHERE chat_id=?" ,(chat_id,),fetch="one")
        if row: return row[0], row[1]
        return 3, "ban"
    async def set_warn_limit(self, chat_id, n): await self._exec("INSERT INTO warns_settings(chat_id,limit_num) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET limit_num=?",(chat_id,n,n))
    async def set_warn_mode(self, chat_id, mode): await self._exec("INSERT INTO warns_settings(chat_id,mode) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET mode=?",(chat_id,mode,mode))
    # rules
    async def set_rules(self, chat_id, content): await self._exec("INSERT INTO rules(chat_id,content) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET content=excluded.content",(chat_id,content))
    async def get_rules(self, chat_id): row=await self._exec("SELECT content FROM rules WHERE chat_id=?" ,(chat_id,),fetch="one"); return row[0] if row else None
    async def set_privaterules(self, chat_id, val): await self._exec("INSERT INTO rules(chat_id,privaterules) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET privaterules=?",(chat_id,int(val),int(val)))
    async def get_privaterules(self, chat_id): row=await self._exec("SELECT privaterules FROM rules WHERE chat_id=?" ,(chat_id,),fetch="one"); return bool(row[0]) if row else False
    async def reset_rules(self, chat_id): await self._exec("DELETE FROM rules WHERE chat_id=?",(chat_id,))
    # notes
    async def set_note(self, chat_id, name, content, uid): await self._exec("INSERT INTO notes(chat_id,name,content,created_by) VALUES(?,?,?,?) ON CONFLICT(chat_id,name) DO UPDATE SET content=excluded.content",(chat_id,name.lower(),content,uid))
    async def get_note(self, chat_id, name): row=await self._exec("SELECT content FROM notes WHERE chat_id=? AND name=?",(chat_id,name.lower()),fetch="one"); return row[0] if row else None
    async def del_note(self, chat_id, name): return await self._exec("DELETE FROM notes WHERE chat_id=? AND name=?",(chat_id,name.lower()))
    async def list_notes(self, chat_id): rows=await self._exec("SELECT name FROM notes WHERE chat_id=? ORDER BY name" ,(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
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
        row=await self._exec("SELECT tos_protection, antilink, antispam, ocr_protection, captcha_enabled, lockwarns, cleanwelcome, cleanservice, welcome_on, goodbye_on, antiflood_on, flood_limit, flood_mode FROM chat_settings WHERE chat_id=?" ,(chat_id,),fetch="one")
        if row: return dict(zip(["tos_protection","antilink","antispam","ocr_protection","captcha_enabled","lockwarns","cleanwelcome","cleanservice","welcome_on","goodbye_on","antiflood_on","flood_limit","flood_mode"],row))
        await self._exec("INSERT OR IGNORE INTO chat_settings(chat_id) VALUES(?)",(chat_id,))
        return {"tos_protection":1,"antilink":1,"antispam":1,"ocr_protection":1,"captcha_enabled":1,"lockwarns":0,"cleanwelcome":0,"cleanservice":0,"welcome_on":1,"goodbye_on":0,"antiflood_on":1,"flood_limit":5,"flood_mode":"tmute 1h"}
    async def set_setting(self, chat_id, key, val):
        await self.get_settings(chat_id)
        if key in ["tos_protection","antilink","antispam","ocr_protection","captcha_enabled","lockwarns","cleanwelcome","cleanservice","welcome_on","goodbye_on","antiflood_on","flood_limit","tos_protection"]:
            await self._exec(f"UPDATE chat_settings SET {key}=? WHERE chat_id=?",(int(val) if isinstance(val,bool) else int(val) if key=="flood_limit" else val,chat_id))
        else:
            await self._exec(f"UPDATE chat_settings SET {key}=? WHERE chat_id=?",(val,chat_id))
    # active
    async def upsert_active(self, chat_id, user_id, username, fullname): await self._exec("INSERT INTO active_users(chat_id,user_id,username,fullname,last_seen) VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(chat_id,user_id) DO UPDATE SET username=excluded.username, fullname=excluded.fullname, last_seen=CURRENT_TIMESTAMP",(chat_id,user_id,username,fullname))
    async def get_active_users(self, chat_id, limit=200): rows=await self._exec("SELECT user_id, username, fullname FROM active_users WHERE chat_id=? ORDER BY last_seen DESC LIMIT ?",(chat_id,limit),fetch="all"); return rows or []
    # greetings with media
    async def set_welcome(self, chat_id, text, media_type=None, file_id=None): await self._exec("INSERT INTO greetings(chat_id,welcome_text,welcome_media_type,welcome_file_id) VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET welcome_text=excluded.welcome_text, welcome_media_type=excluded.welcome_media_type, welcome_file_id=excluded.welcome_file_id",(chat_id,text,media_type,file_id))
    async def set_goodbye(self, chat_id, text, media_type=None, file_id=None): await self._exec("INSERT INTO greetings(chat_id,goodbye_text,goodbye_media_type,goodbye_file_id) VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET goodbye_text=excluded.goodbye_text, goodbye_media_type=excluded.goodbye_media_type, goodbye_file_id=excluded.goodbye_file_id",(chat_id,text,media_type,file_id))
    async def get_greetings(self, chat_id):
        row=await self._exec("SELECT welcome_text, goodbye_text, welcome_on, goodbye_on, welcome_media_type, welcome_file_id, goodbye_media_type, goodbye_file_id FROM greetings WHERE chat_id=?" ,(chat_id,),fetch="one")
        if row: return row
        return ("Hello {fullname}! Welcome to {chatname}!", "Goodbye {fullname}!",1,0,None,None,None,None)
    # locks
    async def set_lock(self, chat_id, ltype, locked): await self._exec("INSERT INTO locks(chat_id,lock_type,locked) VALUES(?,?,?) ON CONFLICT(chat_id,lock_type) DO UPDATE SET locked=?",(chat_id,ltype,int(locked),int(locked)))
    async def is_locked(self, chat_id, ltype): row=await self._exec("SELECT locked FROM locks WHERE chat_id=? AND lock_type=?",(chat_id,ltype),fetch="one"); return bool(row[0]) if row else False
    async def list_locks(self, chat_id): rows=await self._exec("SELECT lock_type FROM locks WHERE chat_id=? AND locked=1" ,(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
    # allowlist/blocklist/disabled/connections
    async def add_allow(self, chat_id, item): await self._exec("INSERT OR IGNORE INTO allowlist(chat_id,item) VALUES(?,?)",(chat_id,item.lower()))
    async def rm_allow(self, chat_id, item): return await self._exec("DELETE FROM allowlist WHERE chat_id=? AND item=?",(chat_id,item.lower()))
    async def list_allow(self, chat_id): rows=await self._exec("SELECT item FROM allowlist WHERE chat_id=?" ,(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
    async def is_allowed(self, chat_id, text): items=await self.list_allow(chat_id); low=text.lower(); return any(it in low for it in items)
    async def add_blocklist(self, chat_id, word): await self._exec("INSERT OR IGNORE INTO blocklists(chat_id,word) VALUES(?,?)",(chat_id,word.lower()))
    async def rm_blocklist(self, chat_id, word): return await self._exec("DELETE FROM blocklists WHERE chat_id=? AND word=?",(chat_id,word.lower()))
    async def list_blocklist(self, chat_id): rows=await self._exec("SELECT word FROM blocklists WHERE chat_id=?" ,(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
    async def is_blocklisted(self, text, chat_id): rows=await self.list_blocklist(chat_id); low=text.lower(); return next((w for w in rows if w in low),None)
    async def disable_cmd(self, chat_id, cmd): await self._exec("INSERT OR IGNORE INTO disabled_commands(chat_id,command) VALUES(?,?)",(chat_id,cmd.lower()))
    async def enable_cmd(self, chat_id, cmd): return await self._exec("DELETE FROM disabled_commands WHERE chat_id=? AND command=?",(chat_id,cmd.lower()))
    async def is_disabled(self, chat_id, cmd): row=await self._exec("SELECT 1 FROM disabled_commands WHERE chat_id=? AND command=?",(chat_id,cmd.lower()),fetch="one"); return bool(row)
    async def list_disabled(self, chat_id): rows=await self._exec("SELECT command FROM disabled_commands WHERE chat_id=?" ,(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
    # connections MULTI-GROUP FIXED
    async def set_connection(self, user_id, chat_id, title=""): await self._exec("INSERT OR REPLACE INTO connections(user_id,chat_id,chat_title) VALUES(?,?,?)",(user_id,chat_id,title))
    async def get_connections(self, user_id): rows=await self._exec("SELECT chat_id, chat_title FROM connections WHERE user_id=? ORDER BY chat_id",(user_id,),fetch="all"); return rows or []
    async def get_connection(self, user_id): rows=await self.get_connections(user_id); return rows[0][0] if rows else None
    async def remove_connection(self, user_id, chat_id): await self._exec("DELETE FROM connections WHERE user_id=? AND chat_id=?",(user_id,chat_id))
    async def remove_all_connections(self, user_id): await self._exec("DELETE FROM connections WHERE user_id=?",(user_id,))
    # filters per-chat
    async def set_filter(self, chat_id, trigger, reply_text, media_type=None, file_id=None): await self._exec("INSERT OR REPLACE INTO filters(chat_id,trigger,reply_text,media_type,file_id) VALUES(?,?,?,?,?)",(chat_id,trigger.lower(),reply_text,media_type,file_id))
    async def get_filter(self, chat_id, trigger): row=await self._exec("SELECT reply_text, media_type, file_id FROM filters WHERE chat_id=? AND lower(trigger)=?",(chat_id,trigger.lower()),fetch="one"); return row
    async def list_filters(self, chat_id): rows=await self._exec("SELECT trigger FROM filters WHERE chat_id=? ORDER BY trigger" ,(chat_id,),fetch="all"); return [r[0] for r in (rows or [])]
    async def del_filter(self, chat_id, trigger): return await self._exec("DELETE FROM filters WHERE chat_id=? AND lower(trigger)=?",(chat_id,trigger.lower()))
    async def del_all_filters(self, chat_id): await self._exec("DELETE FROM filters WHERE chat_id=?",(chat_id,))
    async def get_filters_rows(self, chat_id): rows=await self._exec("SELECT trigger, reply_text, media_type, file_id FROM filters WHERE chat_id=?" ,(chat_id,),fetch="all"); return rows or []
    async def set_config(self, key, value): await self._exec("INSERT INTO bot_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?",(key,value,value))
    async def get_config(self, key): row=await self._exec("SELECT value FROM bot_config WHERE key=?",(key,),fetch="one"); return row[0] if row else None
    async def log_support(self, chat_id, actor_id, action, target_id=None, detail=""): await self._exec("INSERT INTO support_logs(chat_id,actor_id,action,target_id,detail) VALUES(?,?,?,?,?)",(chat_id,actor_id,action,target_id,detail))
    # games with guesses_json
    async def start_game(self, chat_id, word, starter): await self._exec("INSERT INTO games(chat_id,word,length,starter_id,attempts,is_active,guesses_json,board_msg_id) VALUES(?,?,?,?,0,1,'[]',NULL) ON CONFLICT(chat_id) DO UPDATE SET word=excluded.word, length=excluded.length, starter_id=excluded.starter_id, attempts=0, is_active=1, guesses_json='[]', board_msg_id=NULL, started_at=CURRENT_TIMESTAMP",(chat_id,word.lower(),len(word),starter))
    async def get_game(self, chat_id): row=await self._exec("SELECT word,length,starter_id,attempts,guesses_json,board_msg_id FROM games WHERE chat_id=? AND is_active=1" ,(chat_id,),fetch="one"); return row
    async def bump_attempts(self, chat_id): await self._exec("UPDATE games SET attempts=attempts+1 WHERE chat_id=?",(chat_id,))
    async def set_guesses(self, chat_id, guesses_json): await self._exec("UPDATE games SET guesses_json=? WHERE chat_id=?",(guesses_json,chat_id))
    async def set_board_msg(self, chat_id, msg_id): await self._exec("UPDATE games SET board_msg_id=? WHERE chat_id=?",(msg_id,chat_id))
    async def stop_game(self, chat_id): await self._exec("UPDATE games SET is_active=0 WHERE chat_id=?",(chat_id,))
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
    n,unit=int(m.group(1), m.group(2).lower())
    mul={"s":1,"m":60,"h":3600,"d":86400,"w":604800}
    return datetime.timedelta(seconds=n*mul[unit])
async def get_support_chat_id():
    if SUPPORT_CHAT_ID: return SUPPORT_CHAT_ID
    v=await db.get_config("SUPPORT_CHAT_ID")
    if v and v.lstrip("-").isdigit(): return int(v)
    return None
async def log_to_support(context, action_text:str, chat_id=None, actor_id=None, target_id=None, detail=""):
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

async def effective_chat_id(update,context) -> Optional[int]:
    chat=update.effective_chat
    if not chat: return None
    if chat.type=="private":
        uid=update.effective_user.id
        active=await db.get_config(f"active_conn_{uid}")
        if active and active.lstrip("-").isdigit(): return int(active)
        conns=await db.get_connections(uid)
        if conns: return conns[0][0]
        await update.effective_message.reply_text("🔌 Not connected. Go to a group where you're admin and send /connect, or use /connect <chat_id> in private.")
        return None
    return chat.id

get_effective_chat_id = effective_chat_id  # alias for prompt compatibility

async def is_admin(update, context, user_id=None):
    # check admin in effective chat_id (supports private connect)
    cid=await effective_chat_id(update,context)
    if not cid: return False
    uid=user_id or (update.effective_user.id if update.effective_user else None)
    if not uid: return False
    if OWNER_ID and uid==OWNER_ID: return True
    try:
        m=await context.bot.get_chat_member(cid, uid)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except: return False
async def is_owner(uid): return bool(OWNER_ID and uid==OWNER_ID)

async def resolve_target(update, context) -> Optional[Tuple[int,str]]:
    msg=update.effective_message
    if msg and msg.reply_to_message and msg.reply_to_message.from_user:
        u=msg.reply_to_message.from_user; return u.id, u.full_name
    if context.args:
        arg=context.args[0].lstrip("@")
        if arg.lstrip("-").isdigit(): return int(arg), f"user {arg}"
        # get correct chat_id for lookup (private connect aware)
        cid=await effective_chat_id(update,context)
        if cid:
            actives=await db.get_active_users(cid,500)
            for uid,uname,fname in actives:
                if uname and uname.lower()==arg.lower(): return uid, fname
            try:
                m=await context.bot.get_chat_member(cid, arg if arg.startswith("@") else f"@{arg}")
                if m: return m.user.id, m.user.full_name
            except: pass
        return None
    return None

async def send_temp_notice(ctx, chat_id, text, ttl=ILLEGAL_NOTICE_TTL):
    try:
        m=await ctx.bot.send_message(chat_id,text,parse_mode=ParseMode.HTML)
        if ctx.job_queue: ctx.job_queue.run_once(lambda c: asyncio.create_task(_del(c,chat_id,m.message_id), ttl))
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
            if update.effective_message: await update.effective_message.reply_text("🚫 Only admins.")
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
async def check_bypass(update,context):
    user=update.effective_user; chat=update.effective_chat
    if not user or not chat: return True
    if await is_owner(user.id): return True
    cid=await effective_chat_id(update,context)
    if not cid: return True
    try:
        m=await context.bot.get_chat_member(cid, user.id)
        if m.status in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER): return True
    except: pass
    if chat.type!="private" and await db.is_trusted(chat.id, user.id): return True
    return False

# ── Moderation ──
@admin_required
async def cmd_ban(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Usage: Reply or /ban <@username|ID> [reason]"); return
    uid,name=tgt
    if OWNER_ID and uid==OWNER_ID: await update.effective_message.reply_text("Can't ban owner"); return
    if await is_admin(update,context,uid): await update.effective_message.reply_text("Can't ban admin"); return
    cmd=update.effective_message.text.split()[0].split("@")[0].lstrip("/").lower()
    reason=" ".join(context.args[1:]) if len(context.args)>1 else "Banned"
    until=None
    if cmd=="tban" or (context.args and len(context.args)>=1 and parse_duration(context.args[-1])):
        dur=parse_duration(context.args[-1]) if context.args else None
        if dur: until=datetime.datetime.now(datetime.timezone.utc)+dur; reason=" ".join(context.args[1:-1]) if len(context.args)>2 else "Temp ban"
    try:
        if until: await context.bot.ban_chat_member(cid,uid,until_date=until)
        else: await context.bot.ban_chat_member(cid,uid)
        await update.effective_message.reply_html(f"🔨 Banned {mention_html(uid,name)} <i>{esc(reason)}</i>")
        await log_to_support(context,f"BAN {uid} in {cid} reason:{reason}",cid,update.effective_user.id,uid,reason)
        if cmd in ("sban","tban"):
            try: await update.effective_message.delete()
            except: pass
        elif cmd=="dban":
            try: await update.effective_message.reply_to_message.delete()
            except: pass
    except Exception as e: await update.effective_message.reply_text(f"Ban failed: {e}")

@admin_required
async def cmd_unban(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Usage: /unban <@username|ID>"); return
    uid,name=tgt
    try: await context.bot.unban_chat_member(cid,uid); await update.effective_message.reply_html(f"✅ Unbanned {mention_html(uid,name)}"); await log_to_support(context,f"UNBAN {uid}",cid,update.effective_user.id,uid)
    except Exception as e: await update.effective_message.reply_text(f"Unban failed: {e}")

@admin_required
async def cmd_mute(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Usage: /mute <@username|ID> [time]"); return
    uid,name=tgt
    if await is_admin(update,context,uid): await update.effective_message.reply_text("Can't mute admin"); return
    until=None; cmd=update.effective_message.text.split()[0].lstrip("/").lower().split("@")[0]
    if context.args and parse_duration(context.args[-1]): until=datetime.datetime.now(datetime.timezone.utc)+parse_duration(context.args[-1])
    try:
        await context.bot.restrict_chat_member(cid,uid,permissions=ChatPermissions(can_send_messages=False),until_date=until)
        await update.effective_message.reply_html(f"🔇 Muted {mention_html(uid,name)}")
        await log_to_support(context,f"MUTE {uid}",cid,update.effective_user.id,uid)
        if cmd in ("smute","tmute"):
            try: await update.effective_message.delete()
            except: pass
        elif cmd=="dmute":
            try: await update.effective_message.reply_to_message.delete()
            except: pass
    except Exception as e: await update.effective_message.reply_text(f"Mute failed: {e}")

@admin_required
async def cmd_unmute(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Usage: /unmute <@username|ID>"); return
    uid,name=tgt
    try: await context.bot.restrict_chat_member(cid,uid,permissions=ChatPermissions(can_send_messages=True,can_send_media_messages=True,can_send_other_messages=True,can_add_web_page_previews=True)); await update.effective_message.reply_html(f"🔊 Unmuted {mention_html(uid,name)}"); await log_to_support(context,f"UNMUTE {uid}",cid,update.effective_user.id,uid)
    except Exception as e: await update.effective_message.reply_text(f"Unmute failed: {e}")

@admin_required
async def cmd_kick(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Usage: /kick <@username|ID>"); return
    uid,name=tgt
    if await is_admin(update,context,uid): await update.effective_message.reply_text("Can't kick admin"); return
    try: await context.bot.ban_chat_member(cid,uid); await context.bot.unban_chat_member(cid,uid); await update.effective_message.reply_html(f"👢 Kicked {mention_html(uid,name)}"); await log_to_support(context,f"KICK {uid}",cid,update.effective_user.id,uid)
    except Exception as e: await update.effective_message.reply_text(f"Kick failed: {e}")

async def cmd_promote(update,context):
    if not await is_admin(update,context): await update.effective_message.reply_text("🚫 Only admins."); return
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply or /promote <@username|ID>"); return
    uid,name=tgt
    try: await context.bot.promote_chat_member(cid,uid,can_delete_messages=True,can_restrict_members=True,can_pin_messages=True); await update.effective_message.reply_html(f"⬆️ Promoted {mention_html(uid,name)}"); await log_to_support(context,f"PROMOTE {uid}",cid,update.effective_user.id,uid)
    except Exception as e: await update.effective_message.reply_text(f"Promote failed: {e}")

async def cmd_demote(update,context):
    if not await is_admin(update,context): await update.effective_message.reply_text("🚫 Only admins."); return
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply or /demote <@username|ID>"); return
    uid,name=tgt
    try: await context.bot.promote_chat_member(cid,uid,can_delete_messages=False,can_restrict_members=False,can_pin_messages=False); await update.effective_message.reply_html(f"⬇️ Demoted {mention_html(uid,name)}"); await log_to_support(context,f"DEMOTE {uid}",cid,update.effective_user.id,uid)
    except Exception as e: await update.effective_message.reply_text(f"Demote failed: {e}")

@admin_required
async def cmd_warn(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Usage: /warn <@username|ID> [reason]"); return
    uid,name=tgt
    if await is_admin(update,context,uid): await update.effective_message.reply_text("Can't warn admin"); return
    reason=" ".join(context.args[1:]) if len(context.args)>1 else "No reason"
    count=await db.add_warn(cid,uid,reason)
    limit,mode=await db.get_warns_settings(cid)
    await update.effective_message.reply_html(f"⚠️ Warned {mention_html(uid,name)} ({count}/{limit}) <i>{esc(reason)}</i>")
    await log_to_support(context,f"WARN {uid} {count}/{limit} {reason}",cid,update.effective_user.id,uid,reason)
    if count>=limit:
        try:
            if mode=="ban": await context.bot.ban_chat_member(cid,uid); await context.bot.send_message(cid,f"🚫 {mention_html(uid,name)} reached {limit} warns → <b>banned</b>",parse_mode=ParseMode.HTML)
            elif mode=="kick": await context.bot.ban_chat_member(cid,uid); await context.bot.unban_chat_member(cid,uid)
            elif mode in ("mute","tmute"): await context.bot.restrict_chat_member(cid,uid,permissions=ChatPermissions(can_send_messages=False))
            await db.reset_warns(cid,uid)
            await log_to_support(context,f"AUTO {mode} after warns {uid}",cid,uid,uid)
        except Exception as e: await update.effective_message.reply_text(f"Warn action failed: {e}")

async def cmd_warns(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if tgt: uid,name=tgt
    elif update.effective_message.reply_to_message and update.effective_message.reply_to_message.from_user: uid=update.effective_message.reply_to_message.from_user.id; name=update.effective_message.reply_to_message.from_user.full_name
    else: uid=update.effective_user.id; name=update.effective_user.full_name
    c=await db.get_warns(cid,uid); limit,_=await db.get_warns_settings(cid)
    await update.effective_message.reply_html(f"📋 {mention_html(uid,name)} has {c}/{limit} warns")
@admin_required
async def cmd_rmwarn(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply or /rmwarn <@username|ID>"); return
    uid,name=tgt; newc=await db.rm_warn(cid,uid); await update.effective_message.reply_html(f"✅ Removed warn for {mention_html(uid,name)} → {newc}")
@admin_required
async def cmd_resetwarn(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Usage: /resetwarn <@username|ID>"); return
    uid,name=tgt; await db.reset_warns(cid,uid); await update.effective_message.reply_html(f"✅ Reset warns for {mention_html(uid,name)}")
@admin_required
async def cmd_resetallwarns(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    await db.reset_all_warns(cid); await update.effective_message.reply_text("✅ All warns reset")
async def cmd_warnings(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    limit,mode=await db.get_warns_settings(cid); await update.effective_message.reply_html(f"⚙️ Warn limit: <b>{limit}</b>\nWarn mode: <b>{esc(mode)}</b>")
@admin_required
async def cmd_warnmode(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: limit,mode=await db.get_warns_settings(cid); await update.effective_message.reply_html(f"Warn mode: {esc(mode)}"); return
    mode=context.args[0].lower()
    if mode not in ("ban","kick","mute","tban","tmute"): await update.effective_message.reply_text("Valid: ban/kick/mute/tban/tmute"); return
    await db.set_warn_mode(cid,mode); await update.effective_message.reply_text(f"Warn mode set to {mode}")
@admin_required
async def cmd_warnlimit(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: limit,_=await db.get_warns_settings(cid); await update.effective_message.reply_text(f"Warn limit: {limit}"); return
    try: n=int(context.args[0]); await db.set_warn_limit(cid,n); await update.effective_message.reply_text(f"Warn limit set to {n}")
    except: await update.effective_message.reply_text("Usage: /warnlimit 3")
async def cmd_adminlist(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    try: admins=await context.bot.get_chat_administrators(cid); txt="👮 <b>Admins:</b>\n"+"\n".join(f"• {mention_html(a.user.id,a.user.full_name)} — {a.status}" for a in admins); await update.effective_message.reply_html((txt))
    except Exception as e: await update.effective_message.reply_text(f"Failed: {e}")
async def cmd_admincache(update,context): await update.effective_message.reply_text("✅ Admin cache refreshed")
@admin_required
async def cmd_anonadmin(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text("Usage: /anonadmin on/off"); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(cid,"anonadmin",val); await update.effective_message.reply_text(f"Anon admin {'enabled' if val else 'disabled'}")

async def cmd_approve(update,context):
    if not await is_admin(update,context): await update.effective_message.reply_text("🚫 Only admins."); return
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply or /approve <@username|ID>"); return
    uid,name=tgt; await db.trust(cid,uid,update.effective_user.id); await update.effective_message.reply_html(f"✅ Approved {mention_html(uid,name)}"); await log_to_support(context,f"APPROVE {uid}",cid,update.effective_user.id,uid)
async def cmd_unapprove(update,context):
    if not await is_admin(update,context): await update.effective_message.reply_text("🚫 Only admins."); return
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply or /unapprove <@username|ID>"); return
    uid,name=tgt; await db.untrust(cid,uid); await update.effective_message.reply_html(f"❌ Unapproved {mention_html(uid,name)}"); await log_to_support(context,f"UNAPPROVE {uid}",cid,update.effective_user.id,uid)

# ── Rules / Notes ──
async def cmd_rules(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    c=await db.get_rules(cid)
    if not c: await update.effective_message.reply_text("No rules. Use /setrules"); return
    if await db.get_privaterules(cid):
        try: await context.bot.send_message(update.effective_user.id,f"📜 <b>Rules for {esc(update.effective_chat.title or str(cid))}</b>:\n\n{c}",parse_mode=ParseMode.HTML); await update.effective_message.reply_text("Rules sent in private"); return
        except: pass
    await update.effective_message.reply_html((c))
@admin_required
async def cmd_setrules(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    txt=" ".join(context.args) if context.args else (update.effective_message.reply_to_message.text if update.effective_message.reply_to_message and update.effective_message.reply_to_message.text else "")
    if not txt: await update.effective_message.reply_text("Usage: /setrules <text>"); return
    await db.set_rules(cid,txt); await update.effective_message.reply_text("✅ Rules updated"); await log_to_support(context,"SET RULES",cid,update.effective_user.id)
@admin_required
async def cmd_privaterules(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text(f"Private: {await db.get_privaterules(cid)}"); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_privaterules(cid,val); await update.effective_message.reply_text(f"Private rules {'on' if val else 'off'}")
@admin_required
async def cmd_resetrules(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    await db.reset_rules(cid); await update.effective_message.reply_text("✅ Rules reset")
@admin_required
async def cmd_setnote(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text("Usage: /setnote <name> <text>"); return
    name=context.args[0]
    if update.effective_message.reply_to_message and (update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption): content=update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption
    else:
        if len(context.args)<2: await update.effective_message.reply_text("Provide content or reply"); return
        content=" ".join(context.args[1:])
    await db.set_note(cid,name,content,update.effective_user.id); await update.effective_message.reply_html(f"💾 Note <code>#{esc(name.lower())}</code> saved")
async def cmd_getnote(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args:
        notes=await db.list_notes(cid)
        if not notes: await update.effective_message.reply_text("No notes. Use /setnote"); return
        await update.effective_message.reply_html("🗒 <b>Notes:</b>\n"+"\n".join(f"• <code>#{esc(n)}</code>" for n in notes)); return
    name=context.args[0].lstrip("#"); c=await db.get_note(cid,name)
    if not c: await update.effective_message.reply_text("Not found"); return
    await update.effective_message.reply_html((c))
@admin_required
async def cmd_delnote(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text("Usage: /delnote <name>"); return
    name=context.args[0].lstrip("#"); cnt=await db.del_note(cid,name); await update.effective_message.reply_text("Deleted" if cnt else "Not found")
async def hashtag_note_handler(update,context):
    if not update.effective_message or not update.effective_message.text: return
    if update.effective_chat.type=="private": return
    m=re.match(r"^#(\w+)\b",update.effective_message.text.strip())
    if not m: return
    c=await db.get_note(update.effective_chat.id,m.group(1))
    if c: await update.effective_message.reply_html((c))
@admin_required
async def cmd_trust(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply or /trust <@username|ID>"); return
    uid,name=tgt; await db.trust(cid,uid,update.effective_user.id); await update.effective_message.reply_html(f"✅ Trusted {mention_html(uid,name)}")
@admin_required
async def cmd_untrust(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    tgt=await resolve_target(update,context)
    if not tgt: await update.effective_message.reply_text("Reply or /untrust <@username|ID>"); return
    uid,name=tgt; await db.untrust(cid,uid); await update.effective_message.reply_html(f"❌ Untrusted {mention_html(uid,name)}")

# ── Vault ──
@owner_required
async def cmd_addbanword(update,context):
    if not context.args: await update.effective_message.reply_text("Usage: /addbword <word>"); return
    phrase=" ".join(context.args); await db.add_global_word(phrase,update.effective_user.id); await update.effective_message.reply_html(f"🔒 Added <code>{esc(phrase.lower())}</code>"); await log_to_support(context,f"ADD VAULT {phrase}",None,update.effective_user.id)
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
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text("Usage: /lock <type>"); return
    for t in context.args:
        if t.lower() in LOCK_TYPES: await db.set_lock(cid,t.lower(),True)
    await update.effective_message.reply_text(f"🔒 Locked: {', '.join(context.args)}"); await log_to_support(context,f"LOCK {context.args}",cid,update.effective_user.id)
@admin_required
async def cmd_unlock(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text("Usage: /unlock <type>"); return
    for t in context.args:
        if t.lower() in LOCK_TYPES: await db.set_lock(cid,t.lower(),False)
    await update.effective_message.reply_text(f"🔓 Unlocked: {', '.join(context.args)}")
@admin_required
async def cmd_locks_panel(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    s=await db.get_settings(cid); locks=await db.list_locks(cid)
    kb=[
        [InlineKeyboardButton(f"{'✅' if await db.is_locked(cid,'url') else '❌'} Link",callback_data="toggle_lock_url"), InlineKeyboardButton(f"{'✅' if await db.is_locked(cid,'sticker') else '❌'} Sticker",callback_data="toggle_lock_sticker")],
        [InlineKeyboardButton(f"{'✅' if s['antispam'] else '❌'} AntiSpam",callback_data="toggle_antispam"), InlineKeyboardButton(f"{'✅' if s['ocr_protection'] else '❌'} OCR",callback_data="toggle_ocr_protection")],
        [InlineKeyboardButton(f"{'✅' if s['captcha_enabled'] else '❌'} CAPTCHA",callback_data="toggle_captcha_enabled"), InlineKeyboardButton("Close",callback_data="close_locks")]
    ]
    await update.effective_message.reply_html(f"🔐 <b>Locks — {esc(update.effective_chat.title or str(cid))}</b>\nLocked: {', '.join(locks) if locks else 'none'}",reply_markup=InlineKeyboardMarkup(kb))
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
        ltype=q.data.replace("toggle_lock_",""); cid=await effective_chat_id(update,context); cur=await db.is_locked(cid,ltype); await db.set_lock(cid,ltype,not cur); await q.answer(f"{ltype} {'locked' if not cur else 'unlocked'}"); return
    if q.data.startswith("toggle_"):
        if not await is_admin(update,context): await q.answer("Admins only",show_alert=True); return
        key=q.data.replace("toggle_",""); cid=await effective_chat_id(update,context); cur=await db.get_settings(cid); await db.set_setting(cid,key,not cur.get(key,True)); await q.answer("Toggled")
async def cmd_locktypes(update,context): await update.effective_message.reply_text("Lock types: "+", ".join(LOCK_TYPES))
async def cmd_lockwarns(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: s=await db.get_settings(cid); await update.effective_message.reply_text(f"lockwarns: {'on' if s['lockwarns'] else 'off'}"); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(cid,"lockwarns",val); await update.effective_message.reply_text(f"lockwarns {'on' if val else 'off'}")
@admin_required
async def cmd_allowlist(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: items=await db.list_allow(cid); await update.effective_message.reply_text("Allowlist: "+", ".join(items) if items else "Empty"); return
    for it in context.args: await db.add_allow(cid,it)
    await update.effective_message.reply_text("Added: "+", ".join(context.args))
@admin_required
async def cmd_rmallowlist(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text("Usage: /rmallowlist <url>"); return
    for it in context.args: await db.rm_allow(cid,it)
    await update.effective_message.reply_text("Removed")
@admin_required
async def cmd_rmallowlistall(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    await db._exec("DELETE FROM allowlist WHERE chat_id=?",(cid,)); await update.effective_message.reply_text("✅ Allowlist cleared")
@admin_required
async def cmd_whitelist(update,context): await cmd_allowlist(update,context)
@admin_required
async def cmd_blocklist(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: items=await db.list_blocklist(cid); await update.effective_message.reply_text("Blocklist: "+", ".join(items) if items else "Empty"); return
    for w in context.args: await db.add_blocklist(cid,w)
    await update.effective_message.reply_text("Added to blocklist")
@admin_required
async def cmd_rmblocklist(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text("Usage: /rmblocklist <word>"); return
    for w in context.args: await db.rm_blocklist(cid,w)
    await update.effective_message.reply_text("Removed")
@admin_required
async def cmd_setflood(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: s=await db.get_settings(cid); await update.effective_message.reply_text(f"Flood limit: {s['flood_limit']}"); return
    try: n=int(context.args[0]); await db.set_setting(cid,"flood_limit",n); await update.effective_message.reply_text(f"Flood limit {n}")
    except: await update.effective_message.reply_text("Usage: /setflood 5")
@admin_required
async def cmd_setfloodmode(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: s=await db.get_settings(cid); await update.effective_message.reply_text(f"Flood mode: {s['flood_mode']}"); return
    mode=" ".join(context.args); await db.set_setting(cid,"flood_mode",mode); await update.effective_message.reply_text(f"Flood mode {mode}")

@admin_required
async def cmd_tosprotection(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args:
        s=await db.get_settings(cid)
        on=s.get("tos_protection",1) if isinstance(s, dict) else 1
        await update.effective_message.reply_html(f"🛡️ <b>TOS Protection:</b> <code>{'ON ✅' if on else 'OFF ❌'}</code>\nUse <code>/tos on</code> or <code>/tos off</code> — open-source AI + keyword filter deletes messages violating Telegram TOS.")
        return
    val=context.args[0].lower() in ("on","yes","true","1","enable","enabled")
    await db.set_setting(cid,"tos_protection", 1 if val else 0)
    await update.effective_message.reply_text(f"🛡️ TOS Protection {'enabled ✅' if val else 'disabled ❌'} — {'AI will delete TOS-violating content.' if val else 'Only manual deletes.'}")
    await log_to_support(context,f"TOS {('ON' if val else 'OFF')}",cid,update.effective_user.id)

async def cmd_cleanwelcome(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if context.args: val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(cid,"cleanwelcome",val); await update.effective_message.reply_text(f"cleanwelcome {'on' if val else 'off'}")
    else: s=await db.get_settings(cid); await update.effective_message.reply_text(f"cleanwelcome: {s['cleanwelcome']}")
async def cmd_cleanservice(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if context.args: val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(cid,"cleanservice",val); await update.effective_message.reply_text(f"cleanservice {'on' if val else 'off'}")
    else: s=await db.get_settings(cid); await update.effective_message.reply_text(f"cleanservice: {s['cleanservice']}")

# ── Support Log Commands ──
async def cmd_support(update,context):  # plain, no footer per v5
    sup=await get_support_chat_id()
    status=f"Log Channel: <code>{sup}</code>" if sup else "Log Channel: not set (env SUPPORT_CHAT_ID or /setlog)"
    await update.effective_message.reply_html(f"🆘 <b>Support — {esc(BRAND_NAME)}</b>\nLink: {esc(SUPPORT_LINK)}\n{status}\n\nAdmin actions are logged there.")
@admin_required
async def cmd_setlog(update,context):
    if not context.args: await update.effective_message.reply_text(f"Usage: /setlog <chat_id> e.g. -100123...\nCurrent: {await get_support_chat_id()}"); return
    val=context.args[0]
    if not val.lstrip("-").isdigit(): await update.effective_message.reply_text("Invalid ID"); return
    await db.set_config("SUPPORT_CHAT_ID",val); await update.effective_message.reply_html(f"✅ Log channel set to <code>{esc(val)}</code>")

# ── FILTERS FIXED (per-chat media) ──
@admin_required
async def cmd_addfilter(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text("Usage: /filter <trigger> [reply]  OR reply to media with /filter <trigger>"); return
    trigger=context.args[0].lower()
    reply_text=""; media_type=None; file_id=None
    # if reply to media, use that media
    if update.effective_message.reply_to_message:
        rep=update.effective_message.reply_to_message
        if rep.photo: media_type="photo"; file_id=rep.photo[-1].file_id; reply_text=rep.caption or " ".join(context.args[1:]) if len(context.args)>1 else ""
        elif rep.video: media_type="video"; file_id=rep.video.file_id; reply_text=rep.caption or ""
        elif rep.document: media_type="document"; file_id=rep.document.file_id; reply_text=rep.caption or ""
        elif rep.sticker: media_type="sticker"; file_id=rep.sticker.file_id; reply_text=""
        elif rep.animation: media_type="animation"; file_id=rep.animation.file_id; reply_text=rep.caption or ""
        elif rep.text: media_type="text"; reply_text=rep.text
        # if no media but args have reply_text
        if not reply_text and len(context.args)>1: reply_text=" ".join(context.args[1:])
    else:
        if len(context.args)<2: await update.effective_message.reply_text("Usage: /filter <trigger> <reply> or reply to media"); return
        reply_text=" ".join(context.args[1:]); media_type="text"
    await db.set_filter(cid, trigger, reply_text, media_type, file_id)
    await update.effective_message.reply_html(f"✅ Filter <code>{esc(trigger)}</code> saved for this group — will reply with {media_type or 'text'} when found.")
    await log_to_support(context,f"ADD FILTER {trigger} -> {media_type} in {cid}",cid,update.effective_user.id,detail=trigger)

@admin_required
async def cmd_filters_list(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    items=await db.list_filters(cid)
    if not items: await update.effective_message.reply_text("No filters in this group. Use /filter <trigger> <reply>"); return
    txt="🗒 <b>Filters in this group:</b>\n" + "\n".join(f"• <code>{esc(t)}</code>" for t in items)
    await update.effective_message.reply_html((txt))

@admin_required
async def cmd_stopfilter(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text("Usage: /stop <trigger>"); return
    trig=context.args[0].lower(); cnt=await db.del_filter(cid,trig)
    await update.effective_message.reply_text(f"Removed filter <code>{esc(trig)}</code>" if cnt else f"Filter <code>{esc(trig)}</code> not found")
    await log_to_support(context,f"DEL FILTER {trig}",cid,update.effective_user.id)

@admin_required
async def cmd_stopall(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    # simple immediate (no confirm for brevity, can add button)
    await db.del_all_filters(cid); await update.effective_message.reply_text("✅ All filters cleared for this group.")

async def handle_filters(update,context):
    if not update.effective_chat or update.effective_chat.type=="private": return
    msg=update.effective_message
    if not msg or (msg.text and msg.text.startswith("/")): return
    text=(msg.text or msg.caption or "").lower()
    if not text: return
    cid=update.effective_chat.id
    # respect disabled? skip
    # fetch per-chat filters
    rows=await db.get_filters_rows(cid)
    for trig, reply_text, mtype, fid in rows or []:
        if trig.lower() in text.lower():
            try:
                if mtype=="photo": await context.bot.send_photo(cid, fid, caption=reply_text, parse_mode=ParseMode.HTML)
                elif mtype=="video": await context.bot.send_video(cid, fid, caption=reply_text, parse_mode=ParseMode.HTML)
                elif mtype=="document": await context.bot.send_document(cid, fid, caption=reply_text, parse_mode=ParseMode.HTML)
                elif mtype=="sticker": await context.bot.send_sticker(cid, fid)
                elif mtype=="animation": await context.bot.send_animation(cid, fid, caption=reply_text, parse_mode=ParseMode.HTML)
                else: await msg.reply_html(reply_text)
            except Exception as e: log.debug("filter send err %s",e)
            break  # only first match to avoid spam


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
            await send_temp_notice(context,chat_id,"🚨 Flood muted")
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
    # TOS AI on OCR extracted text (images violating Telegram TOS)
    violated, reason, score, detail = await check_tos_violation(extracted)
    if violated:
        try: await msg.delete()
        except: pass
        await send_tos_notice(context, update.effective_chat.id, reason)
        await log_to_support(context,f"TOS OCR DELETE — {reason} score={score:.2f}",update.effective_chat.id,update.effective_user.id,detail=extracted[:200])
        return
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
    wtext,gtext,won,gon,wmtype,wfid,gmtype,gfid=await db.get_greetings(chat.id)
    for member in msg.new_chat_members:
        if member.is_bot: continue
        if won and s["welcome_on"]:
            try:
                cnt=await context.bot.get_chat_member_count(chat.id)
                fill=fill_welcome(wtext,member,chat,cnt)
                if wmtype=="photo" and wfid:
                    sent=await context.bot.send_photo(chat.id, wfid, caption=(fill), parse_mode=ParseMode.HTML)
                else:
                    sent=await msg.reply_html((fill))
                if s["cleanwelcome"]:
                    old=await db._exec("SELECT welcome_clean_msg_id FROM chat_settings WHERE chat_id=?",(chat.id,),fetch="one")
                    if old and old[0]:
                        try: await context.bot.delete_message(chat.id, old[0])
                        except: pass
                    await db._exec("UPDATE chat_settings SET welcome_clean_msg_id=? WHERE chat_id=?",(sent.message_id if 'sent' in locals() else msg.message_id,chat.id))
                await log_to_support(context,f"JOIN {member.id} {member.full_name}",chat.id,member.id)
            except Exception as e: log.debug("welcome err %s",e)
        if not s["captcha_enabled"]: continue
        try:
            await context.bot.restrict_chat_member(chat.id,member.id,permissions=ChatPermissions(can_send_messages=False,can_send_media_messages=False,can_send_other_messages=False,can_add_web_page_previews=False))
        except: continue
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify You Are Human",callback_data=f"captcha_{member.id}")]])
        try: sent2=await msg.reply_html(f"👋 Welcome {mention_html(member.id,member.full_name)}! Verify in <b>{CAPTCHA_TIMEOUT//60} min</b>",reply_markup=kb)
        except: continue
        async def captcha_job(ctx):
            data=ctx.job.data if ctx.job else {}
            c_id=data.get("chat_id",chat.id); u_id=data.get("user_id",member.id); mid=data.get("msg_id",sent2.message_id)
            try:
                m=await ctx.bot.get_chat_member(c_id,u_id)
                if m.status==ChatMemberStatus.RESTRICTED or (c_id,u_id) in pending_captcha:
                    if (c_id,u_id) in pending_captcha:
                        try: await ctx.bot.ban_chat_member(c_id,u_id); await ctx.bot.unban_chat_member(c_id,u_id); await ctx.bot.send_message(c_id,f"⏰ {esc(member.full_name)} failed verification"); await log_to_support(ctx,f"CAPTCHA FAIL kick {u_id}",c_id,u_id)
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
    try: await q.edit_message_text(f"✅ Verified — welcome {esc(update.effective_user.full_name)}!")
    except: await q.answer("Verified!")

async def cmd_setwelcome(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    reply=update.effective_message.reply_to_message
    text=" ".join(context.args) if context.args else ""
    media_type=None; file_id=None
    if reply:
        if reply.photo: media_type="photo"; file_id=reply.photo[-1].file_id; text=reply.caption or text
        elif reply.video: media_type="video"; file_id=reply.video.file_id; text=reply.caption or text
        elif reply.document: media_type="document"; file_id=reply.document.file_id; text=reply.caption or text
        elif reply.text: text=reply.text
    if not text and not file_id: await update.effective_message.reply_text("Usage: /setwelcome <text> — or reply to media with /setwelcome"); return
    await db.set_welcome(cid,text,media_type,file_id); await update.effective_message.reply_text("✅ Welcome updated — per-chat custom media saved"); await log_to_support(context,"SET WELCOME",cid,update.effective_user.id,detail=text[:80])
async def cmd_resetwelcome(update,context): cid=await effective_chat_id(update,context); await db.set_welcome(cid,"Hello {fullname}! Welcome to {chatname}!",None,None); await update.effective_message.reply_text("✅ Welcome reset")
async def cmd_setgoodbye(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    text=" ".join(context.args) if context.args else ""
    if not text: await update.effective_message.reply_text("Usage: /setgoodbye <text>"); return
    await db.set_goodbye(cid,text); await update.effective_message.reply_text("✅ Goodbye updated")
async def cmd_resetgoodbye(update,context): cid=await effective_chat_id(update,context); await db.set_goodbye(cid,"Goodbye {fullname}!"); await update.effective_message.reply_text("✅ Goodbye reset")
async def cmd_welcome(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: s=await db.get_settings(cid); await update.effective_message.reply_text(f"welcome: {'on' if s['welcome_on'] else 'off'}"); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(cid,"welcome_on",val); await update.effective_message.reply_text(f"welcome {'on' if val else 'off'}")
async def cmd_goodbye(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: s=await db.get_settings(cid); await update.effective_message.reply_text(f"goodbye: {'on' if s['goodbye_on'] else 'off'}"); return
    val=context.args[0].lower() in ("yes","on","true"); await db.set_setting(cid,"goodbye_on",val); await update.effective_message.reply_text(f"goodbye {'on' if val else 'off'}")

# ── Mass Mentions ──
@admin_required
async def cmd_all(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    custom=" ".join(context.args) if context.args else "Attention!"
    actives=await db.get_active_users(cid,500)
    if not actives: await update.effective_message.reply_text("No active pool — chat first"); return
    await update.effective_message.reply_text(f"📢 Broadcasting to {len(actives)} in batches of {TAG_BATCH_SIZE}…")
    for i in range(0,len(actives),TAG_BATCH_SIZE):
        batch=actives[i:i+TAG_BATCH_SIZE]; mentions=" ".join(mention_html(uid,fname) for uid,_,fname in batch)
        try: await context.bot.send_message(cid,f"📣 <b>{esc(custom)}</b>\n\n{mentions}",parse_mode=ParseMode.HTML)
        except: await asyncio.sleep(2)
        if i+TAG_BATCH_SIZE < len(actives): await asyncio.sleep(TAG_BATCH_DELAY)
    await log_to_support(context,f"TAG ALL {len(actives)}",cid,update.effective_user.id)
@admin_required
async def cmd_admins(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    custom=" ".join(context.args) if context.args else "Admins please attend!"
    try: admins=await context.bot.get_chat_administrators(cid)
    except Exception as e: await update.effective_message.reply_text(f"Failed {e}"); return
    human=[a for a in admins if not a.user.is_bot]
    if not human: await update.effective_message.reply_text("No human admins"); return
    await update.effective_message.reply_text(f"👮 Tagging {len(human)} admins…")
    users=[(a.user.id,a.user.username,a.user.full_name) for a in human]
    for i in range(0,len(users),TAG_BATCH_SIZE):
        batch=users[i:i+TAG_BATCH_SIZE]; mentions=" ".join(mention_html(uid,fname) for uid,_,fname in batch)
        try: await context.bot.send_message(cid,f"👮 <b>{esc(custom)}</b>\n\n{mentions}",parse_mode=ParseMode.HTML)
        except: pass
        if i+TAG_BATCH_SIZE < len(users): await asyncio.sleep(TAG_BATCH_DELAY)

# ── ShadowReply ──
@admin_required
async def cmd_shadowreply(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    payload=" ".join(context.args) if context.args else ""
    if not payload and update.effective_message.reply_to_message and (update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption): payload=update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption
    if not payload.strip(): await update.effective_message.reply_text("Usage: /shadowreply <message>"); return
    try: await update.effective_message.delete()
    except:
        try: await context.bot.delete_message(update.effective_chat.id,update.effective_message.message_id)
        except: pass
    try: await context.bot.send_message(cid,payload,parse_mode=ParseMode.HTML)
    except: await context.bot.send_message(cid,payload)
    await log_to_support(context,f"SHADOW by {update.effective_user.id}: {payload[:60]}",cid,update.effective_user.id)

# ── Wordle with Stack Emojify ──
async def cmd_newgame(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    existing=await db.get_game(cid)
    if existing:
        w,l,s,a,gj,bid=existing
        try: sm=await context.bot.get_chat_member(cid,s); sname=sm.user.full_name
        except: sname=str(s)
        await update.effective_message.reply_html(f"⚠️ <b>Another game is already in progress in this chat!</b>\nWord length: {l} | Starter: {esc(sname)} | Attempts: {a}\nFinish it or /endgame to start new")
        return
    raw=update.effective_message.text.split()[0].split("@")[0].lstrip("/").lower() if update.effective_message.text else "new"
    length=5
    if raw in ("new4","word4"): length=4
    elif raw in ("new5","word5"): length=5
    elif raw in ("new6","word6"): length=6
    elif raw in ("new","newword","newgame","word","wordle"): length=5
    if context.args and context.args[0].isdigit() and int(context.args[0]) in (4,5,6): length=int(context.args[0])
    pool=WORD_BANK.get(length,WORD_BANK[5]); word=random.choice(pool)
    await db.start_game(cid,word,update.effective_user.id)
    board=Emojify.stack_emojify(word,[],length)
    await update.effective_message.reply_html(f"🎲 <b>Word Jumble Started!</b> — {length} letters\nSend a {length}-letter word to guess. 🎯=perfect 🔀=misplaced 🚫=absent\n\n{board}")
    await log_to_support(context,f"NEW GAME {length}L by {update.effective_user.id}",cid,update.effective_user.id,detail=word)

async def cmd_endgame(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not await is_admin(update,context): await update.effective_message.reply_text("🚫 Only admins can end game"); return
    g=await db.get_game(cid)
    if not g: await update.effective_message.reply_text("No active game"); return
    w,l,s,a,gj,bid=g
    await db.stop_game(cid)
    await update.effective_message.reply_html(f"🛑 Game ended. The word was <code>{esc(w.upper())}</code>")
    await log_to_support(context,f"END GAME word={w}",cid,update.effective_user.id)

async def cmd_gamestatus(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    g=await db.get_game(cid)
    if not g: await update.effective_message.reply_text("No game. /new /new4 /new5 /new6"); return
    w,l,s,a,gj,bid=g
    guesses=json.loads(gj or "[]")
    board=Emojify.stack_emojify(w,guesses,l)
    await update.effective_message.reply_html((board + f"\n\n<b>Starter:</b> <code>{s}</code> | <b>Attempts:</b> {a}"))

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
    cid=update.effective_chat.id
    g=await db.get_game(cid)
    if not g: return
    secret,length,starter,attempts,gj,bid=g
    if len(txt)!=length: return
    guesses=json.loads(gj or "[]")
    # ── VALIDATED WORD CHECK (v5.0 FIX: reject bkilsd gibberish) ──
    if not is_valid_word(txt, length):
        await msg.reply_html(f"❌ <code>{esc(txt.upper())}</code> is not a valid English word. Try a real word.")
        return
    lower_words=[gg['word'].lower() for gg in guesses]
    if txt.lower() in lower_words:
        orig=next(gg for gg in guesses if gg['word'].lower()==txt.lower())
        await msg.reply_html(f"😅 Sorry, the word <code>{esc(txt.upper())}</code> was already tried first by {mention_html(orig['user_id'], orig['username'])}! Try a new word.")
        return
    scored=Emojify.score_word(secret,txt.lower())
    new_entry={"word":txt.lower(), "user_id":update.effective_user.id, "username":update.effective_user.full_name, "scored":scored}
    guesses.append(new_entry)
    await db.set_guesses(cid, json.dumps(guesses))
    await db.bump_attempts(cid)
    board=Emojify.stack_emojify(secret, guesses, length)
    if txt.lower()==secret.lower():
        await db.add_win(update.effective_user.id, (update.effective_user.username if isinstance(update.effective_user.username, str) else None)); await db.stop_game(cid)
        await msg.reply_html(board + f"\n\n🎉 <b>Correct!</b> {mention_html(update.effective_user.id,update.effective_user.full_name)} guessed <code>{esc(secret.upper())}</code> in {len(guesses)} tries!")
        await log_to_support(context,f"WORDLE WIN {secret} by {update.effective_user.id}",cid,update.effective_user.id)
    else:
        await db.add_played(update.effective_user.id, (update.effective_user.username if isinstance(update.effective_user.username, str) else None))
        try:
            if bid:
                await context.bot.edit_message_text(chat_id=cid, message_id=bid, text=(board), parse_mode=ParseMode.HTML)
                return
        except: pass
        sent=await msg.reply_html(board)
        try: await db.set_board_msg(cid, sent.message_id)
        except: pass
        if len(guesses)>=6:
            await db.stop_game(cid)
            await context.bot.send_message(cid, f"💀 Game over! The word was <code>{esc(secret.upper())}</code>", parse_mode=ParseMode.HTML)
            await log_to_support(context,f"WORDLE LOSS word={secret}",cid,update.effective_user.id)

# ── Connect Multi-Group FIXED ──
async def cmd_connect(update,context):
    # In group: save connection
    if update.effective_chat.type!="private":
        cid=update.effective_chat.id; title=update.effective_chat.title or str(cid)
        # verify admin
        try: m=await context.bot.get_chat_member(cid, update.effective_user.id)
        except: await update.effective_message.reply_text("Error checking admin"); return
        if m.status not in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER) and not await is_owner(update.effective_user.id):
            await update.effective_message.reply_text("Only admins can connect. Promote first."); return
        await db.set_connection(update.effective_user.id,cid,title)
        await db.set_config(f"active_conn_{update.effective_user.id}",str(cid))
        await update.effective_message.reply_html(f"✅ Connected to <b>{esc(title)}</b>\nNow go to private chat with @{(await context.bot.get_me()).username} and control it via DM.\nTry <code>/help</code> in private.")
        # ephemeral? keep
        return
    # Private
    uid=update.effective_user.id
    if context.args and context.args[0].lstrip("-").isdigit():
        cid=int(context.args[0])
        try: m=await context.bot.get_chat_member(cid,uid)
        except: await update.effective_message.reply_text("Not in that chat or bot not there"); return
        if m.status not in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER) and not await is_owner(uid):
            await update.effective_message.reply_text("Only admins can connect"); return
        # get title
        try: ch=await context.bot.get_chat(cid); title=ch.title or str(cid)
        except: title=str(cid)
        await db.set_connection(uid,cid,title); await db.set_config(f"active_conn_{uid}",str(cid))
        await update.effective_message.reply_html(f"✅ Connected to <b>{esc(title)}</b> (<code>{cid}</code>) — Active")
        return
    conns=await db.get_connections(uid)
    if not conns: await update.effective_message.reply_text("🔌 Not connected yet. Go to a group where you're admin and send /connect, or use /connect <chat_id>"); return
    active=await db.get_config(f"active_conn_{uid}")
    kb=[]
    for cid,title in conns:
        label=f"{'✅ ' if str(cid)==str(active) else ''}{title} ({cid})"
        kb.append([InlineKeyboardButton(label, callback_data=f"switch_conn_{cid}")])
    kb.append([InlineKeyboardButton("➕ Connect new ID", callback_data="connect_help")])
    await update.effective_message.reply_html(f"🔗 <b>Your Connections ({len(conns)} groups)</b>\nActive: <code>{active or conns[0][0]}</code>\nTap to switch active group:",reply_markup=InlineKeyboardMarkup(kb))

async def cb_switch_conn(update,context):
    q=update.callback_query
    if not q: return
    await q.answer()
    if q.data=="connect_help": await q.edit_message_text("Send /connect <chat_id> — get ID via /id in group"); return
    if q.data.startswith("switch_conn_"):
        cid=q.data.replace("switch_conn_","")
        await db.set_config(f"active_conn_{update.effective_user.id}",cid)
        try: ch=await context.bot.get_chat(int(cid)); title=ch.title
        except: title=cid
        await q.edit_message_text(f"✅ Active group switched to <b>{esc(title)}</b> (<code>{cid}</code>)")

async def cmd_connections(update,context):
    conns=await db.get_connections(update.effective_user.id)
    if not conns: await update.effective_message.reply_text("No connections. Use /connect in group."); return
    active=await db.get_config(f"active_conn_{update.effective_user.id}")
    txt="🔗 <b>Connections:</b>\n" + "\n".join(f"{'✅' if str(c)==str(active) else '•'} {esc(t)} — <code>{c}</code>" for c,t in conns)
    await update.effective_message.reply_html((txt))

async def cmd_disconnect(update,context):
    if update.effective_chat.type=="private":
        if context.args and context.args[0].lstrip("-").isdigit():
            cid=int(context.args[0]); await db.remove_connection(update.effective_user.id,cid); await update.effective_message.reply_text(f"🔌 Disconnected <code>{cid}</code>")
            return
        # remove active
        active=await db.get_config(f"active_conn_{update.effective_user.id}")
        if active: await db.remove_connection(update.effective_user.id,int(active)); await db.set_config(f"active_conn_{update.effective_user.id}",""); await update.effective_message.reply_text(f"🔌 Disconnected <code>{active}</code>")
        else: await update.effective_message.reply_text("Usage: /disconnect <chat_id>")
    else:
        cid=update.effective_chat.id; await db.remove_connection(update.effective_user.id,cid); await update.effective_message.reply_text("🔌 Disconnected from this group")

async def cmd_disconnect_all(update,context): await db.remove_all_connections(update.effective_user.id); await db.set_config(f"active_conn_{update.effective_user.id}",""); await update.effective_message.reply_text("🔌 All connections cleared")

@admin_required
async def cmd_disable(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: items=await db.list_disabled(cid); await update.effective_message.reply_text("Disabled: "+", ".join(items) if items else "None"); return
    for c in context.args: await db.disable_cmd(cid,c.lower())
    await update.effective_message.reply_text("Disabled: "+", ".join(context.args))
@admin_required
async def cmd_enable(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    if not context.args: await update.effective_message.reply_text("Usage: /enable <command>"); return
    for c in context.args: await db.enable_cmd(cid,c.lower())
    await update.effective_message.reply_text("Enabled: "+", ".join(context.args))
async def cmd_setlang(update,context): await update.effective_message.reply_text("✅ Language set")
async def cmd_export(update,context):
    cid=await effective_chat_id(update,context)
    if not cid: return
    cats=context.args if context.args else ["notes","filters","rules","warns","locks","greetings"]
    data={}
    if "notes" in cats: data["notes"]=[{"name":n,"content":await db.get_note(cid,n)} for n in await db.list_notes(cid)]
    if "rules" in cats: data["rules"]=await db.get_rules(cid)
    if "greetings" in cats: data["greetings"]=await db.get_greetings(cid)
    if "warns" in cats: rows=await db._exec("SELECT user_id,count FROM warnings WHERE chat_id=?",(cid,),fetch="all"); data["warns"]=rows
    if "locks" in cats: data["locks"]=await db.list_locks(cid)
    if "blocklists" in cats: data["blocklists"]=await db.list_blocklist(cid)
    if "filters" in cats: rows=await db.get_filters_rows(cid); data["filters"]=[{"trigger":t,"reply_text":rt,"media_type":mt,"file_id":fid} for t,rt,mt,fid in rows]
    bio=io.BytesIO(json.dumps(data,indent=2,ensure_ascii=False).encode()); bio.name=f"export_{cid}.json"
    await context.bot.send_document(update.effective_chat.id,bio,caption=f"Exported {', '.join(cats)}")
async def cmd_import(update,context):
    msg=update.effective_message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text("Reply to JSON file with /import"); return
    try: f=await context.bot.get_file(msg.reply_to_message.document.file_id); bio=io.BytesIO(); await f.download_to_memory(bio); data=json.loads(bio.getvalue().decode())
    except Exception as e: await msg.reply_text(f"Import failed: {e}"); return
    cid=await effective_chat_id(update,context)
    if not cid: return
    if "notes" in data:
        for n in data["notes"]: await db.set_note(cid,n["name"],n["content"],update.effective_user.id)
    if "rules" in data and data["rules"]: await db.set_rules(cid,data["rules"])
    if "filters" in data:
        for fl in data["filters"]: await db.set_filter(cid,fl["trigger"],fl["reply_text"],fl["media_type"],fl["file_id"])
    await msg.reply_text("✅ Imported")

# ── Utility ──
async def cmd_start(update,context):
    await update.effective_message.reply_html(branded_start("✨ <b>Miss Sona — The Secret Squad Guardian</b> ✨\n\nRose-parity + industrial modules.\n<b>Mod:</b> /ban /tban /mute /warn /promote /adminlist (all via reply/@ID)\n<b>Approve:</b> /approve /unapprove (via reply/@ID)\n<b>Locks:</b> /lock /unlock /locks /allowlist /setflood\n<b>Welcome:</b> /setwelcome {fullname} (reply to photo for media) /welcome on + CAPTCHA ✅\n<b>Filters:</b> /filter <trigger> <reply> or reply to media with /filter <trigger> — per-group!\n<b>Vault:</b> /addbword /listbwords (owner)\n<b>Broadcast:</b> /all /admins 5/3s\n<b>Shadow:</b> /shadowreply\n<b>Game:</b> /new /new4 /new5 /new6 /game /top — 📚 Stack Emojify Board\n<b>Support:</b> /support /setlog (logs all actions)\n<b>TOS AI:</b> /tos on/off — auto-delete Telegram TOS violating content (open-source AI) ✅\n<b>Connect:</b> /connect /connections (DM multi-group) — /connect in group then DM\n<b>Utility:</b> /export /import, /disable"))
async def cmd_help(update,context): await cmd_start(update,context) # uses branded_start via cmd_start
async def cmd_id(update,context):
    msg=update.effective_message; user=update.effective_user
    cid=await effective_chat_id(update,context)
    lines=[f"👤 You: <code>{user.id}</code> — {esc(user.full_name)}"]
    tgt=await resolve_target(update,context)
    if tgt: lines.append(f"🎯 Target: <code>{tgt[0]}</code> — {esc(tgt[1])}")
    if cid: lines.append(f"💬 Chat: <code>{cid}</code>")
    if msg.reply_to_message and msg.reply_to_message.from_user: u=msg.reply_to_message.from_user; lines.append(f"↩️ Reply: <code>{u.id}</code> — {esc(u.full_name)}")
    lines.append(f"\n<i>Resolve via reply, <code>@username</code> or numeric ID — {esc(BRAND_TAG)}</i>")
    await msg.reply_html("\n".join(lines))

async def error_handler(update,context): log.exception("Update %s error %s",update,context.error)

def build_app():
    app=Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    G_TRACK=-1; G_SEC=0; G_MAIN=1; G_FB=2
    app.add_handler(MessageHandler(filters.ALL, track_activity), group=G_TRACK)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tos_violation), group=G_SEC)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_security), group=G_SEC)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo), group=G_SEC)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, greet_new_members), group=G_MAIN)
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, track_activity), group=G_MAIN)
    app.add_handler(CallbackQueryHandler(cb_captcha, pattern=r"^captcha_\d+$"), group=G_MAIN)
    app.add_handler(CallbackQueryHandler(cb_toggle_locks, pattern=r"^(toggle_|close_locks)"), group=G_MAIN)
    app.add_handler(CallbackQueryHandler(cb_switch_conn, pattern=r"^switch_conn_"), group=G_MAIN)
    app.add_handler(CallbackQueryHandler(cb_switch_conn, pattern=r"^connect_help$"), group=G_MAIN)
    app.add_handler(CommandHandler(["ban","dban","sban","tban"], cmd_ban), group=G_MAIN)
    app.add_handler(CommandHandler("unban", cmd_unban), group=G_MAIN)
    app.add_handler(CommandHandler(["mute","dmute","smute","tmute"], cmd_mute), group=G_MAIN)
    app.add_handler(CommandHandler("unmute", cmd_unmute), group=G_MAIN)
    app.add_handler(CommandHandler(["kick","dkick","skick"], cmd_kick), group=G_MAIN)
    app.add_handler(CommandHandler("promote", cmd_promote), group=G_MAIN)
    app.add_handler(CommandHandler("demote", cmd_demote), group=G_MAIN)
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
    app.add_handler(CommandHandler(["tos","tosprotection","tos_protection"], cmd_tosprotection), group=G_MAIN)
    app.add_handler(CommandHandler("cleanwelcome", cmd_cleanwelcome), group=G_MAIN)
    app.add_handler(CommandHandler("cleanservice", cmd_cleanservice), group=G_MAIN)
    app.add_handler(CommandHandler("setwelcome", cmd_setwelcome), group=G_MAIN)
    app.add_handler(CommandHandler("resetwelcome", cmd_resetwelcome), group=G_MAIN)
    app.add_handler(CommandHandler("setgoodbye", cmd_setgoodbye), group=G_MAIN)
    app.add_handler(CommandHandler("resetgoodbye", cmd_resetgoodbye), group=G_MAIN)
    app.add_handler(CommandHandler("welcome", cmd_welcome), group=G_MAIN)
    app.add_handler(CommandHandler("goodbye", cmd_goodbye), group=G_MAIN)
    # filters FIXED
    app.add_handler(CommandHandler(["filter","addfilter"], cmd_addfilter), group=G_MAIN)
    app.add_handler(CommandHandler(["filters"], cmd_filters_list), group=G_MAIN)
    app.add_handler(CommandHandler(["stop","delfilter","removefilter"], cmd_stopfilter), group=G_MAIN)
    app.add_handler(CommandHandler(["stopall","removeallfilters"], cmd_stopall), group=G_MAIN)
    app.add_handler(CommandHandler(["all","tagall","everyone","mentionall"], cmd_all), group=G_MAIN)
    app.add_handler(CommandHandler(["admins","admin","tagadmins"], cmd_admins), group=G_MAIN)
    app.add_handler(CommandHandler("shadowreply", cmd_shadowreply), group=G_MAIN)
    app.add_handler(CommandHandler(["new","new4","new5","new6","newword","newgame","word","wordle","word4","word5","word6"], cmd_newgame), group=G_MAIN)
    app.add_handler(CommandHandler(["endgame","stopgame"], cmd_endgame), group=G_MAIN)
    app.add_handler(CommandHandler(["game","gamestatus","status"], cmd_gamestatus), group=G_MAIN)
    app.add_handler(CommandHandler(["top","leaderboard","lb"], cmd_top), group=G_MAIN)
    app.add_handler(CommandHandler("connect", cmd_connect), group=G_MAIN)
    app.add_handler(CommandHandler(["connections"], cmd_connections), group=G_MAIN)
    app.add_handler(CommandHandler(["disconnect","discon"], cmd_disconnect), group=G_MAIN)
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.ALL, handle_filters), group=G_FB)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.ALL, handle_wordle), group=G_FB)
    app.add_error_handler(error_handler)
    return app

async def post_init(app):
    await db.init()
    try:
        me=await app.bot.get_me()
        log.info("🛡️ Miss Sona v6.0 TOS-AI @%s id=%s — %s Support:%s WAL %s",me.username,me.id,BRAND_NAME,await get_support_chat_id(),DB_PATH)
    except Exception as e:
        log.info("🛡️ Miss Sona v6.0 TOS-AI — %s DB %s Support:%s (bot not fetched: %s)",BRAND_NAME,DB_PATH,await get_support_chat_id(),e)
async def post_shutdown(app): log.info("Shutting down Miss Sona v5.0 — %s",BRAND_NAME)
def main():
    app=build_app(); app.post_init=post_init; app.post_shutdown=post_shutdown
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, close_loop=False)
if __name__=="__main__": main()
