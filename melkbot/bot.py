# -*- coding: utf-8 -*-
"""
Melk Hunter — ربات جست‌وجوی حرفه‌ای مخاطبین
--------------------------------------------
• جست‌وجوی فازی فارسی + غلط‌گیری املایی
• جست‌وجوی شماره به هر شکل (۰۹۱۲… / 98912… / ۹۱۲… / ناقص)
• عملگرها: #برچسب ، -حذف ، "عبارت دقیق" ، متر:۲۰۰-۴۰۰ ، نوع:موبایل ، تکراری:بله
• کارت مخاطب با دکمه‌های تماس/واتس‌اپ/ذخیره/مشابه/تکراری
• علاقه‌مندی، تاریخچه، دیده‌بان، خروجی CSV/Excel/vCard، ایمپورت VCF
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import random
import re
import tempfile
import time
from datetime import time as dtime
from types import SimpleNamespace
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle,
    InputTextMessageContent, KeyboardButton, ReplyKeyboardMarkup, Update,
    BotCommand, MenuButtonCommands, CopyTextButton,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut
from telegram.ext import (
    AIORateLimiter, Application, ApplicationBuilder, CallbackQueryHandler,
    CommandHandler, ContextTypes, InlineQueryHandler, MessageHandler,
    TypeHandler, filters,
)

from io import BytesIO

from melkbot import ocr as ocr_mod
from melkbot import smart as sm
from melkbot import store as st
from melkbot.build import build as rebuild_index
from melkbot.normalize import digits_only, normalize_text, parse_phone, to_latin_digits
from melkbot.search import (GOOD, MODE_LABELS, SearchEngine, highlight,
                            phone_match_score, token_score)

BASE = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE / "config.json"
DATA = BASE / "data"
EXPORTS = BASE / "exports"
LOGS = BASE / "logs"
for d in (DATA, EXPORTS, LOGS):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler(LOGS / "bot.log", encoding="utf-8"), logging.StreamHandler()],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)   # صدای اضافهٔ ضربان را از لاگ پاک می‌کند
logging.getLogger("telegram").setLevel(logging.WARNING)
log = logging.getLogger("melkbot")

TEHRAN = ZoneInfo("Asia/Tehran")
PAGE_SIZE = 4          # تعداد کارت در هر صفحه
TAG_LIMIT = 30
FIXED_LIMIT = 30

# ---------------------------------------------------------------- config ----
DEFAULT_CONFIG = {
    "token": "",
    "admin_ids": [],
    "allowed_users": [],        # خالی = برای همه باز
    "watch_hour": 10,           # ساعت گزارش دیده‌بان به وقت تهران
    "max_results": 2000,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:  # pragma: no cover
            log.error("config.json خراب است: %s", e)
    else:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def apply_env(cfg: dict) -> dict:
    """متغیرهای محیطی روی config اولویت دارند (برای Render/Railway/Docker)."""
    env = os.environ
    pairs = {
        "MELKBOT_TOKEN": "token",
        "TELEGRAM_BOT_TOKEN": "token",
        "MELKBOT_MODE": "mode",
        "WEBHOOK_URL": "webhook_url",
        "WEBHOOK_PATH": "webhook_path",
        "SECRET_TOKEN": "secret_token",
        "MELKBOT_PORT": "port",
        "MELKBOT_WATCH_HOUR": "watch_hour",
    }
    for env_key, cfg_key in pairs.items():
        if env.get(env_key):
            cfg[cfg_key] = env[env_key]
    for env_key, cfg_key in (("ADMIN_IDS", "admin_ids"), ("ALLOWED_USERS", "allowed_users")):
        if env.get(env_key):
            try:
                cfg[cfg_key] = [int(x) for x in str(env[env_key]).replace(" ", "").split(",") if x]
            except ValueError:
                log.warning("متغیر %s قابل خواندن نبود: %s", env_key, env[env_key])
    if env.get("WATCH_HOUR"):
        try:
            cfg["watch_hour"] = int(env["WATCH_HOUR"])
        except ValueError:
            pass
    return cfg


CFG = apply_env(load_config())
TOKEN = CFG["token"] or ""
ADMINS = set(int(x) for x in CFG.get("admin_ids") or [])
ALLOWED = set(int(x) for x in CFG.get("allowed_users") or [])

# ------------------------------------------------------------- app state ----
STORE = st.Store()
ENGINE: SearchEngine | None = None
META: dict = {}
SESS: dict[int, dict] = {}       # وضعیت هر کاربر در حافظه
LAST_CALL: dict[int, float] = {}  # محدودکنندهٔ نرخ


def load_engine() -> SearchEngine:
    """بارگذاری ایندکس جست‌وجو از دیسک (به‌همراه مخاطبین اضافه‌شده)."""
    global ENGINE, META
    rebuild_index()
    contacts = json.loads((DATA / "contacts.json").read_text(encoding="utf-8"))
    extra = st.load_extra()
    if extra:
        base_n = max((c["id"] for c in contacts), default=0)
        for i, e in enumerate(extra, 1):
            p = parse_phone(e.get("phone", ""))
            nm = e.get("name", "")
            tg = [t.strip() for t in re.split(r"[,،\s]+", e.get("tags", "")) if t.strip()]
            base_n += 1
            contacts.append({
                "id": base_n, "src": 0, "name": nm, "name_norm": normalize_text(nm),
                "name_compact": normalize_text(nm).replace(" ", ""),
                "tokens": sorted(set(normalize_text(nm).split())),
                "phone_raw": e.get("phone", ""), "phone": p["e164"],
                "phone_local": p["local"], "phone_digits": digits_only(p["e164"]),
                "kind": p["kind"], "country": p["country"], "country_name": p["country_name"],
                "flag": p["flag"], "region": p.get("region", ""), "operator": p.get("operator", ""),
                "valid": p["valid"], "tags": ["افزوده‌شده"] + tg, "blob": normalize_text(nm),
                "blob_compact": normalize_text(nm).replace(" ", ""), "area": None, "plak": None,
                "floor": None, "amount": None, "dup_group": "", "is_dup": False,
            })
    META = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    ENGINE = SearchEngine(contacts, META)
    if extra:
        log.info("ایندکس: %d مخاطب (+%d افزوده‌شده)", len(contacts), len(extra))
    return ENGINE


def filtered_count() -> int:
    """تعداد مخاطبین مؤثر (کل + افزوده‌شده‌ها)."""
    return len(ENGINE.contacts) if ENGINE else 0


# ------------------------------------------------------------- زیباسازی -----
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(n) -> str:
    return f"{n:,}".translate(FA_DIGITS) if isinstance(n, int) else str(n).translate(FA_DIGITS)


def fmt_phone(c: dict) -> str:
    p = c.get("phone_local") or c.get("phone_raw") or ""
    if not p:
        return "—"
    d = digits_only(p)
    if c.get("kind") == "mobile" and len(d) == 11:
        return f"{d[:4]} {d[4:7]} {d[7:]}"
    if c.get("kind") == "landline" and len(d) in (10, 11):
        return f"{d[:3]} {d[3:] if len(d) == 10 else d[3:]}"
    return fa(p)


def e164(c: dict) -> str:
    return c.get("phone") or ("+" + digits_only(c.get("phone_raw", "")))


def wa_link(c: dict) -> str | None:
    d = digits_only(e164(c))
    if len(d) < 10:
        return None
    return "https://wa.me/" + d


def esc(s: str) -> str:
    return html.escape(s or "")


def bar(count: int, total: int, width: int = 12) -> str:
    n = 0 if not total else max(1, round(count / total * width))
    return "█" * n + "░" * (width - n)


def user_settings(uid: int) -> dict:
    return {
        "mode": STORE.get(uid, "mode", "smart"),
        "kind": STORE.get(uid, "kind", "all"),
        "dup": STORE.get(uid, "dup", "show"),   # show | only | hide
        "sort": STORE.get(uid, "sort", "score"),  # score | name | newest
    }


# ------------------------------------------------------------- ترسیم کارت ---
KIND_LABEL = {"mobile": "📱 موبایل", "landline": "☎️ ثابت",
              "intl": "🌍 بین‌المللی", "unknown": "❓ نامشخص"}


TAG_ICONS = {
    "دیوار": "📰", "دیوار ۹۹": "📰", "خریدار": "🛒", "فروشنده": "🏷", "کلنگی": "🧱",
    "مشاور املاک": "🏢", "مشارکت": "🤝", "مستاجر": "🔑", "مالک": "👤", "اجاره": "📄",
    "آپارتمان": "🏬", "مغازه": "🏪", "دفتر": "🗂", "زمین": "🌱", "ساختمان": "🏗",
    "کارخانه": "🏭", "سرایدار": "🛡", "همکار": "👥", "خارج از کشور": "✈️",
    "پزشک": "🩺", "وکیل/حقوقی": "⚖️", "بانک/بیمه": "🏦", "خدمات ساختمانی": "🔧",
    "آژانس مسافرتی": "🧳", "صنایع دستی/پوشاک": "🧵", "رستوران/کافه": "☕",
    "آموزش": "🎓", "پلاک/سند": "📃", "متراژ بالا": "📐", "دو نبش": "📐",
    "ارم/نوساز": "✨", "حیاط/باغ": "🌳", "افزوده‌شده": "➕", "موبایل": "📱",
    "تلفن ثابت": "☎️", "همراه اول": "📶", "ایرانسل": "📶", "رایتل": "📶",
}


def tag_line(c: dict, limit: int = 5) -> str:
    skip = {"موبایل", "تلفن ثابت", "همراه اول", "ایرانسل", "رایتل", "شاتل/سایر"}
    tags = [t for t in c.get("tags", []) if t not in skip]
    if not tags and c.get("tags"):
        tags = c["tags"][:3]
    out = []
    for t in tags[:limit]:
        icon = TAG_ICONS.get(t, "")
        out.append(f"{icon} {esc(t)}".strip())
    extra = f" <i>+{fa(len(tags) - limit)}</i>" if len(tags) > limit else ""
    return " · ".join(out) + extra if out else "—"


def card_text(c: dict, idx: int, total: int, query: str, hl: bool = True) -> str:
    name = highlight(c["name"], [query]) if hl and query else esc(c["name"])
    kind_icon = {"mobile": "📱", "landline": "☎️", "intl": "🌍"}.get(c["kind"], "❓")
    flag = c.get("flag") or ""
    sub = []
    if c.get("operator"):
        sub.append(esc(c["operator"]))
    if c.get("region"):
        sub.append(esc(c["region"]))
    if c.get("country_name") and c.get("kind") == "intl":
        sub.append(esc(c["country_name"]))
    lines = [f"<b>{name}</b>"]
    lines.append(f"{kind_icon} <code>{esc(fmt_phone(c))}</code> {flag} {' · '.join(sub)}".strip())
    lines.append(f"🏷 {tag_line(c)}")
    facts = []
    if c.get("area"):
        facts.append(f"📐 {fa(c['area'])} متر")
    if c.get("plak"):
        facts.append(f"پلاک {fa(str(c['plak']))}")
    if c.get("floor"):
        facts.append(f"طبقه {fa(str(c['floor']))}")
    if c.get("amount"):
        facts.append(esc(c["amount"]))
    if facts:
        lines.append(" · ".join(facts))
    badges = []
    if c.get("is_dup"):
        badges.append("🔁 تکراری")
    if not c.get("valid"):
        badges.append("⚠️ شماره مشکوک")
    if badges:
        lines.append(" · ".join(badges))
    if total:
        lines.append(f"<i>{fa(idx)} از {fa(total)}</i>")
    return "\n".join(lines)


def short_name(name: str, limit: int = 18) -> str:
    """نام کوتاه برای برچسب دکمه (تک‌خطی و کوتاه‌شده)."""
    s = " ".join(str(name or "").split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def full_info_text(c: dict, uid: int) -> str:
    """اطلاعات کامل یک مخاطب — فقط همین شخص، بدون چیز اضافه."""
    name = esc(c.get("name") or "—")
    d = ["👤 <b>اطلاعات کامل این مخاطب</b>", ""]
    d.append(f"📍 <b>عنوان / آدرس ثبت‌شده</b>\n{name}")
    d.append("")
    d.append(f"☎️ <b>شماره</b>: <code>{esc(c.get('phone_local') or c.get('phone_raw') or '—')}</code>"
             f"  ·  بین‌المللی: <code>{esc(e164(c))}</code>")
    line = [f"📱 {esc(c.get('kind') or '—')}"]
    if c.get("operator"):
        line.append(f"اپراتور: {esc(c['operator'])}")
    if c.get("region"):
        line.append(f"منطقه: {esc(c['region'])}")
    if c.get("country_name"):
        line.append(esc(c["country_name"]))
    if not c.get("valid", True):
        line.append("⚠️ شماره مشکوک")
    d.append(" · ".join(line))
    d.append("")
    if c.get("area") or c.get("plak") or c.get("floor") or c.get("amount"):
        d.append("🏠 <b>مشخصات ملک</b>")
        if c.get("area"):
            d.append(f"📐 متراژ: <b>{fa(c['area'])}</b> متر")
        if c.get("amount"):
            d.append(f"💰 مبلغ: {esc(c['amount'])}")
        if c.get("plak"):
            d.append(f"🔢 پلاک: {fa(str(c['plak']))}")
        if c.get("floor"):
            d.append(f"🏢 طبقه: {fa(str(c['floor']))}")
        d.append("")
    d.append(f"🏷 <b>برچسب‌ها</b>: {' · '.join('#' + esc(t) for t in c.get('tags') or []) or '—'}")
    d.append(f"📄 <b>خط اصلی فایل</b>:\n<code>{name} — {esc(c.get('phone_raw') or '')}</code>")
    d.append(f"📇 رکورد شمارهٔ <b>{fa(c.get('src') or 0)}</b> در دفترچهٔ اصلی")
    if c.get("is_dup"):
        d.append(f"🔁 این شماره <b>{fa(len(ENGINE.duplicates_of(c['id'])) + 1)}</b> بار در دفترچه آمده"
                 f" (دکمهٔ «🔁 تکراری» را بزن)")
    note = STORE.get(uid, f"note:{c['id']}")
    saved = STORE.is_fav(uid, c["id"])
    tail = f"🆔 شناسه: <code>{fa(c['id'])}</code> · ⭐ {'ذخیره‌شده' if saved else 'ذخیره‌نشده'}"
    if note:
        tail += f"\n📝 یادداشت من: {esc(note)}"
    d.append("")
    d.append(tail)
    return "\n".join(d)


def card_keyboard(c: dict, page: int, pages: int, uid: int, query: str = "") -> InlineKeyboardMarkup:
    rows = []
    wa = wa_link(c)
    tel = e164(c)
    rows.append([
        InlineKeyboardButton("📋 کپی شماره", copy_text=CopyTextButton(text=tel)),
        InlineKeyboardButton("💬 واتس‌اپ", url=wa) if wa else InlineKeyboardButton("📵",
                                                                                   callback_data="noop"),
    ])
    fav_icon = "🌟 ذخیره‌شده" if STORE.is_fav(uid, c["id"]) else "⭐ ذخیره"
    dup_n = len(ENGINE.duplicates_of(c["id"]))
    row2 = [InlineKeyboardButton(fav_icon, callback_data=f"f:{c['id']}"),
            InlineKeyboardButton("🔗 مشابه", callback_data=f"sim:{c['id']}")]
    if dup_n:
        row2.append(InlineKeyboardButton(f"🔁 تکراری ({fa(dup_n + 1)})", callback_data=f"dup:{c['id']}"))
    rows.append(row2)
    rows.append([
        InlineKeyboardButton("👤 اطلاعات کامل", callback_data=f"info:{c['id']}"),
        InlineKeyboardButton("🔗 اشتراک کارت", callback_data=f"share:{c['id']}"),
        InlineKeyboardButton("🏷 برچسب‌ها", callback_data=f"ctags:{c['id']}"),
    ])
    nav = []
    if pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"pg:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{fa(page + 1)}/{fa(pages)}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"pg:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("⚙️ فیلترها", callback_data="menu:filters"),
        InlineKeyboardButton("🔽 ترتیب", callback_data="menu:sort"),
        InlineKeyboardButton("📤 خروجی", callback_data="menu:export"),
    ])
    return InlineKeyboardMarkup(rows)


def results_text(sess: dict) -> str:
    q = sess.get("query", "")
    total = sess.get("total", 0)
    head = f"🔎 <b>{esc(q)}</b>\n" if q else ""
    bits = []
    if sess.get("relaxed"):
        bits.append("🧠 نتایج تقریبی (چند مورد دقیق پیدا نشد)")
    if sess.get("fuzzy"):
        bits.append("🔢 شمارهٔ نزدیک")
    if sess.get("title"):
        head = f"{esc(sess['title'])}\n"
    kind_lbl = {"all": "", "mobile": " · 📱 موبایل", "landline": " · ☎️ ثابت", "intl": " · 🌍 بین‌المللی"}
    bits.append(f"{fa(total)} نتیجه{kind_lbl.get(sess.get('kind', 'all'), '')}")
    if sess.get("dup") == "only":
        bits.append("🔁 فقط تکراری‌ها")
    elif sess.get("dup") == "hide":
        bits.append("🚫 بدون تکراری")
    return head + " | ".join(bits)


def render_page(sess: dict, page: int | None = None, query: str = ""):
    ids = sess["ids"]
    if page is None:
        page = sess.get("page", 0)
    pages = max(1, (len(ids) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    sess["page"] = page
    chunk = ids[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    items = [ENGINE.get(int(i)) for i in chunk]
    items = [c for c in items if c]
    parts, kb = [], None
    for n, c in enumerate(items):
        idx = page * PAGE_SIZE + n + 1
        parts.append(card_text(c, idx, len(ids), query))
    if not items:
        return "🫙 نتیجه‌ای برای نمایش نیست.", None
    text = results_text(sess) + "\n\n" + ("\n\n".join(parts))
    kb = card_keyboard(items[0], page, pages, sess["uid"], query)
    # 👤 دکمهٔ «اطلاعات کامل» برای هر نامِ همین صفحه (هر نتیجه دکمهٔ خودش را دارد)
    rows = [list(r) for r in kb.inline_keyboard]
    per_card, buf = [], []
    for n, c in enumerate(items):
        idx = page * PAGE_SIZE + n + 1
        buf.append(InlineKeyboardButton(f"👤 {fa(idx)}. {short_name(c['name'])}",
                                        callback_data=f"info:{c['id']}"))
        if len(buf) == 2:
            per_card.append(buf)
            buf = []
    if buf:
        per_card.append(buf)
    rows[3:3] = per_card
    return text, InlineKeyboardMarkup(rows)


def main_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton("🔍 جست‌وجوی سریع"), KeyboardButton("🎲 مخاطب شانسی")],
        [KeyboardButton("⭐ علاقه‌مندی‌ها"), KeyboardButton("🏷 برچسب‌ها")],
        [KeyboardButton("📊 آمار و کیفیت"), KeyboardButton("🧭 دیده‌بان‌ها")],
        [KeyboardButton("📷 افزودن از عکس"), KeyboardButton("🏓 سلامت ربات")],
        [KeyboardButton("📤 خروجی کامل"), KeyboardButton("❓ راهنما")],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True, is_persistent=True)


# ------------------------------------------------------------ دسترسی -------
def allowed(uid: int) -> bool:
    if not ALLOWED:
        return True
    return uid in ALLOWED or uid in ADMINS


async def guard(update: Update) -> bool:
    u = update.effective_user
    if not u:
        return False
    if not allowed(u.id):
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔️ دسترسی شما به این ربات مجاز نیست.\n"
                f"شناسهٔ شما: <code>{u.id}</code>", parse_mode=ParseMode.HTML)
        return False
    STORE.touch_user(u)
    return True


def throttled(uid: int, gap: float = 0.6) -> bool:
    now = time.time()
    last = LAST_CALL.get(uid, 0)
    LAST_CALL[uid] = now
    return (now - last) < gap


# ------------------------------------------------------------ جست‌وجو -------
def run_search(uid: int, query: str, settings: dict | None = None) -> dict:
    settings = settings or user_settings(uid)
    kinds = None if settings.get("kind") in (None, "all") else {settings["kind"]}
    dup = settings.get("dup", "show")
    res = ENGINE.search(
        query,
        mode=settings.get("mode", "smart"),
        limit=CFG.get("max_results", 2000),
        kinds=kinds,
        include_dup=(dup != "hide"),
        only_dup=(dup == "only"),
    )
    return res


_VOCAB: set[str] = set()


def vocab() -> set[str]:
    """واژگان واقعی دفترچه — برای «منظورت این بود؟» و اصلاح املایی."""
    global _VOCAB
    if not _VOCAB and ENGINE:
        _VOCAB = sm.build_vocab(ENGINE.contacts)
        log.info("🧠 واژگان هوشمند ساخته شد: %d کلمه", len(_VOCAB))
    return _VOCAB


def search_smart(uid: int, query: str, settings: dict | None = None) -> tuple[dict, str, bool]:
    """
    جست‌وجوی هوشمند: اگر نتیجه کم/صفر بود، خودش با مترادف‌ها گشادتر می‌کند.
    خروجی: (نتیجه، پرس‌وجوی مؤثر، آیا گشاد شد)
    """
    base = sm.strip_fillers(query) or query
    res = run_search(uid, base, settings)
    best_q, expanded = base, False
    if res["total"] < 3:
        for alt in sm.expand_queries(base):
            if alt == base:
                continue
            r2 = run_search(uid, alt, settings)
            if r2["total"] > res["total"]:
                res, best_q, expanded = r2, alt, True
            if res["total"] >= 3:
                break
    return res, best_q, expanded


def sort_ids(res: dict, sort: str) -> list[int]:
    items = res["items"]
    if sort == "name":
        items = sorted(items, key=lambda x: x[1]["name_norm"])
    elif sort == "newest":
        items = sorted(items, key=lambda x: -x[1].get("src", 0))
    return [c["id"] for _, c, _, _ in items]


async def do_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str,
                    remember: bool = True) -> None:
    uid = update.effective_user.id
    msg = update.effective_message
    if not query.strip():
        await msg.reply_text("🔍 چه چیزی را جست‌وجو کنیم؟ مثلاً <code>آرش جنت آباد</code> یا "
                             "<code>#کلنگی متر:۳۰۰</code>", parse_mode=ParseMode.HTML)
        return
    if throttled(uid):
        await asyncio.sleep(0.6)
    settings = user_settings(uid)
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    t0 = time.time()
    res = run_search(uid, query, settings)
    dt = (time.time() - t0) * 1000
    ids = sort_ids(res, settings.get("sort", "score"))
    log.info("🔎 «%s» → %s نتیجه (%sms) برای %s", query, res["total"], int(dt), uid)

    # 🛡 محافظ: اگر فیلترهای فعال نتیجه را صفر کرده‌اند، کاربر را گیر نینداز
    if not res["total"]:
        act = []
        if settings.get("kind") not in (None, "all"):
            act.append(KIND_LABEL.get(settings["kind"], settings["kind"]))
        if settings.get("dup") == "hide":
            act.append("🚫 بدون تکراری")
        elif settings.get("dup") == "only":
            act.append("🔁 فقط تکراری‌ها")
        if act:
            probe = run_search(uid, query,
                               {**settings, "kind": "all", "dup": "show"})
            if probe["total"]:
                await msg.reply_text(
                    f"🎛 <b>فیلترهای فعال نتیجه را صفر کرد!</b>\n"
                    f"فیلترِ {' + '.join(act)} روشن است، ولی <b>بدون فیلتر "
                    f"{fa(probe['total'])} نتیجه</b> برای «{esc(query)}» وجود دارد.\n\n"
                    f"<i>یک لمس کافی است:</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("♻️ حذف فیلتر و جست‌وجو",
                                             callback_data="chip:reset")],
                        [InlineKeyboardButton("⚙️ دیدن/تنظیم فیلترها",
                                              callback_data="menu:filters")]]))
                return

    if not res["total"]:
        sug = ENGINE.suggest(query, 6)
        tags = ENGINE.tags_matching(query, 6)
        txt = [f"🫙 برای <b>{esc(query)}</b> چیزی پیدا نشد. ({fa(int(dt))} میلی‌ثانیه)"]
        rows = []
        if sug:
            txt.append("\n<b>منظورت این بود؟</b>")
            rows.append([InlineKeyboardButton(s, callback_data=f"q:{s[:40]}") for s in sug[:2]])
            if len(sug) > 2:
                rows.append([InlineKeyboardButton(s, callback_data=f"q:{s[:40]}") for s in sug[2:4]])
            if len(sug) > 4:
                rows.append([InlineKeyboardButton(s, callback_data=f"q:{s[:40]}") for s in sug[4:6]])
        if tags:
            txt.append("\n<b>برچسب‌های نزدیک:</b> " + " · ".join(f"#{esc(t)}" for t in tags))
        txt.append(
            "\n<i>نکته: شماره را ناقص هم بنویسید کار می‌کند (مثلاً </i>"
            "<code>0912 477</code><i>)، یا از عملگرها استفاده کنید: </i>"
            "<code>#دیوار متر:۳۰۰ نوع:موبایل</code>")
        kb = InlineKeyboardMarkup(rows) if rows else None
        await msg.reply_text("\n".join(txt), parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    sess = SESS.setdefault(uid, {})
    sess.update({
        "uid": uid, "query": query, "ids": ids, "total": res["total"],
        "page": 0, "relaxed": res.get("relaxed"), "fuzzy": res.get("fuzzy_phone"),
        "title": "", "kind": settings.get("kind", "all"), "dup": settings.get("dup", "show"),
        "mode": settings.get("mode", "smart"), "when": time.time(),
        "ms": int(dt),
    })
    text, kb = render_page(sess, 0, query)
    if remember:
        STORE.add_history(uid, query)
        STORE.bump_search(uid)
    # اگر صفحه‌بندی دارد، وضعیت را ذخیره کن و اولین صفحه را بفرست
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb,
                         disable_web_page_preview=True)
    sess["msg_id"] = None


# --------------------------------------------------------------- /start -----
WELCOME = """<b>🏙 Melk Hunter</b> — دفترچهٔ هوشمند شما
<i>{n} مخاطب ایندکس‌شده · جست‌وجوی فازی فارسی · شمارهٔ ناقص هم پیدا می‌شود</i>

کافیست هر چیزی که یادت هست بنویسی:
• <code>آرش جنت آباد</code> — حتی با غلط املایی
• <code>0912 477</code> — بخشی از شماره
• <code>#کلنگی متر:۳۰۰-۶۰۰</code> — برچسب + متراژ
• <code>دیوار -همکار نوع:موبایل</code> — حذف و فیلتر
• <code>"خانم شریف"</code> — عبارت دقیق

برای دیدن همهٔ امکانات: /help
"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    # اولین کسی که ربات را استارت می‌زند، مالک محسوب می‌شود (برای هشدار قطعی/وصلی)
    if not ADMINS and update.effective_user:
        ADMINS.add(update.effective_user.id)
        try:
            _cfg = load_config()
            _cfg["admin_ids"] = sorted(ADMINS)
            CONFIG_PATH.write_text(json.dumps(_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("👑 مالک ربات ثبت شد: %s", update.effective_user.id)
        except Exception as e:
            log.warning("ثبت مالک ناموفق: %s", e)
    await update.message.reply_text(
        WELCOME.format(n=fa(filtered_count())),
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard())
    await update.message.reply_text("این دکمه‌ها همیشه در دسترس‌اند 👇", reply_markup=main_keyboard())


HELP = """📚 <b>راهنمای Melk Hunter</b>

<b>۱) جست‌وجو</b> — هر متنی بنویس:
 • نام، لقب، شغل، محله، هر کلمه‌ای در نام مخاطب
 • غلط املایی مهم نیست: «ارش جنت» = «آرش جنت‌آباد»
 • شماره: <code>0912 477</code> · <code>+98912…</code> · <code>912…</code> · <code>477541</code>

<b>۲) عملگرها</b>
 • <code>#برچسب</code> → <code>#کلنگی</code> <code>#دیوار</code> <code>#مستاجر</code> <code>#خریدار</code>
 • <code>#-برچسب</code> یا <code>-کلمه</code> → حذف از نتایج
 • <code>متر:۳۰۰</code> یا <code>متر:۲۰۰-۵۰۰</code> یا <code>متر:&gt;۱۰۰۰</code> → متراژ
 • <code>نوع:موبایل</code> / <code>نوع:ثابت</code> / <code>نوع:بین‌المللی</code>
 • <code>تکراری:بله</code> → فقط رکوردهای تکراری
 • <code>"عبارت دقیق"</code> → جست‌وجوی عبارت

<b>۳) کارت مخاطب</b>
 📋 کپی شماره (لمس کن و کپی کن) · 💬 واتس‌اپ · ⭐ ذخیره · 🔗 مشابه · 🔁 تکراری‌ها · ℹ️ جزئیات


<b>۴) دستورها</b>
 /search &lt;متن&gt; · /favs · /tags · /stats · /quality · /dups · /rand · /export · /add · /watch &lt;عبارت&gt; · /watches · /history · /filters · /help · /id

<b>۵) خروجی و ورودی</b>
 /export → CSV / Excel / vCard از نتایج
 ارسال فایل <code>.vcf</code> یا <code>.csv</code> یا <code>.txt</code> → افزودن به دفترچه (بدون تکرار)

<b>۶) نحو گروه و اینلاین</b>
 در گروه: <code>/search آرش</code> یا ریپلای روی پیام + <code>/search</code>
 در اینلاین: در هر چتی <code>@MelkHunter_bot آرش</code> را تایپ کن
"""


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """سلامت لحظه‌ای ربات: تأخیر API، وضعیت اتصال، صف تلگرام."""
    if not await guard(update):
        return
    msg = await update.effective_message.reply_text("🏓 در حال سنجش…")
    t0 = time.time()
    try:
        await context.bot.get_me()
        rtt = (time.time() - t0) * 1000
        ok = True
    except Exception as e:  # noqa: BLE001
        rtt, ok = 0, False
        log.warning("ping failed: %s", e)
    pend = "؟"
    try:
        info = await context.bot.get_webhook_info()
        pend = fa(info.pending_update_count)
    except Exception:
        pass
    up = time.time() - HB["started"]
    gap = time.time() - HB["last_ok"] if HB["last_ok"] else 0
    lines = [
        "🏓 <b>سلامت ربات</b>",
        ("🟢 اتصال تلگرام: سالم — تأخیر " + fa(int(rtt)) + " میلی‌ثانیه") if ok
        else "🔴 اتصال تلگرام: قطع (در حال تلاش برای وصل شدن)",
        f"💓 آخرین ضربان: {fa(int(gap))} ثانیه پیش (هر ۱۰ ثانیه چک می‌شود)",
        f"⏱ روشن بوده: {fa(int(up // 3600))} ساعت و {fa(int(up % 3600 // 60))} دقیقه",
        f"♻️ تازه‌سازی اتصال: {fa(HB['restarts'])} بار",
        f"📥 پیام‌های دریافتی: {fa(HB['updates'])}",
        f"⌛️ صف تلگرام: {pend}",
        f"🗂 مخاطبین ایندکس‌شده: <b>{fa(filtered_count())}</b>",
    ]
    if HB["last_error"]:
        lines.append(f"⚠️ آخرین خطا: <code>{esc(HB['last_error'][:90])}</code>")
    out = "\n".join(lines)
    try:
        if hasattr(msg, "edit_text"):
            await msg.edit_text(out, parse_mode=ParseMode.HTML)
        else:
            raise AttributeError
    except (BadRequest, AttributeError):
        await update.effective_message.reply_text(out, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML,
                                    reply_markup=main_keyboard(), disable_web_page_preview=True)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    u = update.effective_user
    await update.message.reply_text(
        f"🆔 شناسهٔ شما: <code>{u.id}</code>\nنام: {esc(u.full_name)}\n"
        f"یوزرنیم: @{u.username or '—'}", parse_mode=ParseMode.HTML)


# -------------------------------------------------------------- /search -----
async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    q = " ".join(context.args) if context.args else ""
    if not q and update.message.reply_to_message:
        q = update.message.reply_to_message.text or ""
    if not q:
        await update.message.reply_text("مثال: <code>/search آرش جنت</code>", parse_mode=ParseMode.HTML)
        return
    await do_search(update, context, q)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    text = (update.message.text or "").strip()
    uid = update.effective_user.id
    sess = SESS.get(uid, {})

    # --- حالت‌های انتظار (افزودن مخاطب / برچسب‌گذاری / پیام همگانی) ---
    if sess.get("await"):
        await handle_await(update, context, sess, text)
        return

    quick = {
        "🔍 جست‌وجوی سریع": None,
        "🎲 مخاطب شانسی": None,
        "⭐ علاقه‌مندی‌ها": None,
        "🏷 برچسب‌ها": None,
        "📊 آمار و کیفیت": None,
        "🧭 دیده‌بان‌ها": None,
        "📷 افزودن از عکس": None,
        "🏓 سلامت ربات": None,
        "📤 خروجی کامل": None,
        "❓ راهنما": None,
    }
    if text in quick:
        mapping = {
            "🎲 مخاطب شانسی": cmd_rand,
            "⭐ علاقه‌مندی‌ها": cmd_favs,
            "🏷 برچسب‌ها": cmd_tags,
            "📊 آمار و کیفیت": cmd_stats,
            "🧭 دیده‌بان‌ها": cmd_watches,
            "🏓 سلامت ربات": cmd_ping,
            "📤 خروجی کامل": cmd_export,
            "❓ راهنما": cmd_help,
            "📷 افزودن از عکس": cmd_photo,
        }
        if text in mapping:
            await mapping[text](update, context)
        elif text == "🔍 جست‌وجوی سریع":
            await quick_panel(update, context)
        else:
            await update.message.reply_text(quick[text])
        return

    if text.startswith("/"):
        return
    if len(text) > 120 and not any(ch.isdigit() for ch in text):
        await update.message.reply_text("🤏 عبارت کوتاه‌تری بنویس (حداکثر ۱۲۰ نویسه).")
        return
    if not any(ch.isalnum() for ch in text) and not text.startswith("#"):
        await update.message.reply_text("🔍 چیزی برای جست‌وجو پیدا نکردم. یک نام یا شماره بنویس.")
        return

    # در گروه فقط با منشن یا دستور
    if update.effective_chat.type in ("group", "supergroup"):
        me = context.bot.username
        if f"@{me}" not in text and not update.message.reply_to_message:
            return
        text = text.replace(f"@{me}", "").strip()
    await do_search(update, context, text)


# ------------------------------------------------------------- دستورها ------
async def quick_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پنل میانبر: آخرین جست‌وجوها + پرکاربردترین برچسب‌ها."""
    uid = update.effective_user.id
    rows = []
    hist = STORE.history(uid, 6)
    if hist:
        rows.append([InlineKeyboardButton("🕘 آخرین جست‌وجوها", callback_data="noop")])
        for h in hist[:4]:
            rows.append([InlineKeyboardButton(f"🔁 {h[:38]}", callback_data=f"q:{h[:40]}")])
    tops = [t for t, _ in ENGINE.top_tags(6)]
    if tops:
        rows.append([InlineKeyboardButton("🏷 پرکاربردترین برچسب‌ها", callback_data="noop")])
        for i in range(0, min(6, len(tops)), 2):
            rows.append([InlineKeyboardButton(f"#{t}", callback_data=f"tg:{t[:40]}")
                         for t in tops[i:i + 2]])
    rows.append([InlineKeyboardButton("🎲 یک مخاطب شانسی", callback_data="q:random"),
                 InlineKeyboardButton("📊 آمار دفترچه", callback_data="stats:1")])
    await update.message.reply_text(
        "🔍 <b>چه چیزی را پیدا کنم؟</b>\n"
        "هر کلمه‌ای از نام، محله، شماره یا برچسب را بنویس — یا یکی از میان‌برها را بزن:\n\n"
        "<i>مثال: </i><code>ارش جنت</code> <i>· </i><code>0912 477</code> <i>· </i>"
        "<code>#کلنگی متر:۳۰۰</code>",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))


async def cmd_rand(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    n = 1
    if context.args and context.args[0].isdigit():
        n = min(10, max(1, int(context.args[0])))
    cs = random.sample(ENGINE.contacts, k=min(n, len(ENGINE.contacts)))
    parts = [card_text(c, 0, 0, "", hl=False) for c in cs]
    await update.effective_message.reply_text("🎲 <b>مخاطب شانسی</b>\n\n" + "\n\n".join(parts),
                                              parse_mode=ParseMode.HTML)


async def cmd_favs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    uid = update.effective_user.id
    ids = STORE.fav_ids(uid)
    if not ids:
        await update.message.reply_text("⭐ هنوز چیزی ذخیره نکرده‌ای.\n"
                                        "زیر هر کارت دکمهٔ «⭐ ذخیره» را بزن.")
        return
    sess = SESS.setdefault(uid, {})
    sess.update({"uid": uid, "query": "", "ids": ids, "total": len(ids), "page": 0,
                 "title": "⭐ علاقه‌مندی‌های من", "relaxed": False, "fuzzy": False,
                 "kind": "all", "dup": "show"})
    text, kb = render_page(sess, 0, "")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_tags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    tops = ENGINE.top_tags(TAG_LIMIT)
    rows, row = [], []
    for tag, cnt in tops:
        row.append(InlineKeyboardButton(f"{TAG_ICONS.get(tag, '•')} {tag} ({fa(cnt)})",
                                        callback_data=f"tg:{tag[:40]}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    total = len(ENGINE.tag_counts)
    await update.message.reply_text(
        f"🏷 <b>برچسب‌ها</b> — {fa(total)} برچسب فعال\nیکی را بزن تا همهٔ مخاطبینش را ببینی:",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    E = ENGINE
    n = len(E.contacts)
    kinds = {"mobile": "📱 موبایل", "landline": "☎️ ثابت", "intl": "🌍 بین‌المللی", "unknown": "❓ نامشخص"}
    lines = [f"📊 <b>آمار دفترچه</b>\n",
             f"👥 کل مخاطبین: <b>{fa(n)}</b>",
             f"☎️ شمارهٔ یکتا: <b>{fa(META.get('unique_phones', 0))}</b>",
             f"🔁 گروه‌های تکراری: <b>{fa(META.get('duplicate_groups', 0))}</b>",
             f"📐 با متراژ: <b>{fa(META.get('with_area', 0))}</b>",
             f"➕ افزوده‌شده توسط کاربران: <b>{fa(STORE.manual_count())}</b>",
             f"🧩 رکوردهای واردشده (فایل/دستی): <b>{fa(len(st.load_extra()))}</b>", ""]
    lines.append("<b>نوع شماره</b>")
    for k, lbl in kinds.items():
        c = sum(1 for x in E.contacts if x["kind"] == k)
        if c:
            lines.append(f"{lbl}: {bar(c, n)} {fa(c)}")
    lines.append("\n<b>برچسب‌های برتر</b>")
    for tag, c in E.top_tags(10):
        lines.append(f"{TAG_ICONS.get(tag, '•')} {esc(tag)}: {bar(c, n, 8)} {fa(c)}")
    bs = STORE.stats()
    lines += ["", f"👤 کاربران ربات: <b>{fa(bs['users'])}</b>",
              f"🔎 جست‌وجوها: <b>{fa(bs['searches'])}</b>",
              f"⭐ ذخیره‌شده‌ها: <b>{fa(bs['favs'])}</b>",
              f"🧭 دیده‌بان‌ها: <b>{fa(bs['watches'])}</b>"]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_quality(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    E = ENGINE
    dup_ids = [c["id"] for c in E.contacts if c["is_dup"]]
    invalid = [c for c in E.contacts if not c["valid"]]
    short = [c for c in E.contacts if len(digits_only(c["_digits"])) < 10]
    uniq = META.get("unique_phones", 0)
    n = len(E.contacts)
    lines = [
        "🧪 <b>کیفیت داده</b>", "",
        f"🔁 رکورد تکراری: <b>{fa(len(dup_ids))}</b> ({fa(META.get('duplicate_groups', 0))} گروه)",
        f"⚠️ شمارهٔ مشکوک/ناقص: <b>{fa(len(invalid) + len(short))}</b>",
        f"✂️ شماره‌های کوتاه‌تر از ۱۰ رقم: <b>{fa(len(short))}</b>",
        f"📞 چند شماره در یک رکورد: <b>{fa(META.get('multi_phone', 0))}</b>",
        f"✅ نرخ پوشش شمارهٔ سالم: <b>{fa(round((n - len(invalid) - len(short)) / max(1, n) * 100))}٪</b>",
    ]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔁 دیدن تکراری‌ها ({fa(len(dup_ids))})", callback_data="list:dup")],
        [InlineKeyboardButton("⚠️ دیدن شماره‌های مشکوک", callback_data="list:invalid")],
        [InlineKeyboardButton("📤 خروجی فایل مشکل‌دارها", callback_data="exp:issues")],
    ])
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_dups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    ids = [c["id"] for c in ENGINE.contacts if c["is_dup"]]
    if not ids:
        await update.message.reply_text("✅ هیچ رکورد تکراری‌ای نیست.")
        return
    uid = update.effective_user.id
    sess = SESS.setdefault(uid, {})
    sess.update({"uid": uid, "query": "", "ids": ids, "total": len(ids), "page": 0,
                 "title": "🔁 رکوردهای تکراری", "relaxed": False, "fuzzy": False,
                 "kind": "all", "dup": "only"})
    text, kb = render_page(sess, 0, "")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    uid = update.effective_user.id
    hist = STORE.history(uid, 15)
    if not hist:
        await update.message.reply_text("🕘 تاریخچه‌ای نیست.")
        return
    rows = [[InlineKeyboardButton(f"🔁 {h[:40]}", callback_data=f"q:{h[:40]}")] for h in hist[:10]]
    await update.message.reply_text("🕘 <b>آخرین جست‌وجوها</b>", parse_mode=ParseMode.HTML,
                                    reply_markup=InlineKeyboardMarkup(rows))


async def cmd_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(filters_text(update.effective_user.id),
                                    parse_mode=ParseMode.HTML,
                                    reply_markup=filters_keyboard(update.effective_user.id))


def filters_text(uid: int) -> str:
    s = user_settings(uid)
    kinds = {"all": "همه", "mobile": "📱 موبایل", "landline": "☎️ ثابت", "intl": "🌍 بین‌المللی"}
    dups = {"show": "نمایش همه", "only": "🔁 فقط تکراری‌ها", "hide": "🚫 بدون تکراری"}
    sorts = {"score": "ارتباط", "name": "الفبا", "newest": "جدیدترین"}
    return ("⚙️ <b>تنظیمات جست‌وجو</b>\n\n"
            f"🎯 حالت: <b>{MODE_LABELS.get(s['mode'], s['mode'])}</b>\n"
            f"📞 نوع شماره: <b>{kinds.get(s['kind'], s['kind'])}</b>\n"
            f"🔁 تکراری‌ها: <b>{dups.get(s['dup'], s['dup'])}</b>\n"
            f"🔽 ترتیب: <b>{sorts.get(s['sort'], s['sort'])}</b>")


def filters_keyboard(uid: int) -> InlineKeyboardMarkup:
    s = user_settings(uid)
    rows = []
    rows.append([InlineKeyboardButton(("✅ " if s["mode"] == m else "") + lbl,
                                      callback_data=f"set:mode:{m}") for m, lbl in MODE_LABELS.items()][:3])
    rows.append([InlineKeyboardButton(("✅ " if s["mode"] == "desc" else "") + MODE_LABELS["desc"],
                                      callback_data="set:mode:desc"),
                 InlineKeyboardButton(("✅ " if s["mode"] == "tag" else "") + MODE_LABELS["tag"],
                                      callback_data="set:mode:tag")])
    rows.append([InlineKeyboardButton(("✅ " if s["kind"] == k else "") + lbl,
                                      callback_data=f"set:kind:{k}")
                 for k, lbl in [("all", "همه"), ("mobile", "📱"), ("landline", "☎️"), ("intl", "🌍")]])
    rows.append([InlineKeyboardButton(("✅ " if s["dup"] == d else "") + lbl,
                                      callback_data=f"set:dup:{d}")
                 for d, lbl in [("show", "همه"), ("only", "🔁 تکراری"), ("hide", "🚫 بدون تکراری")]])
    rows.append([InlineKeyboardButton(("✅ " if s["sort"] == x else "") + lbl,
                                      callback_data=f"set:sort:{x}")
                 for x, lbl in [("score", "ارتباط"), ("name", "الفبا"), ("newest", "جدیدترین")]])
    rows.append([InlineKeyboardButton("🔁 اجرای دوبارهٔ آخرین جست‌وجو", callback_data="re:1"),
                 InlineKeyboardButton("🏠 بستن", callback_data="noop")])
    return InlineKeyboardMarkup(rows)


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.effective_message.reply_text(
        "📤 <b>خروجی</b>\nچه چیزی بسازم؟",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 نتایج آخر (CSV)", callback_data="exp:csv"),
             InlineKeyboardButton("📊 نتایج آخر (Excel)", callback_data="exp:xlsx")],
            [InlineKeyboardButton("📇 کارت ویزیت (vCard نتایج آخر)", callback_data="exp:vcf")],
            [InlineKeyboardButton("🗂 کل دفترچه (Excel)", callback_data="exp:allxlsx"),
             InlineKeyboardButton("📦 بکاپ کامل (JSON+CSV)", callback_data="exp:backup")],
        ]))


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    uid = update.effective_user.id
    SESS.setdefault(uid, {})["await"] = "add"
    await update.message.reply_text(
        "➕ <b>افزودن مخاطب</b>\nیک پیام با این قالب بفرست:\n"
        "<code>نام | شماره | برچسب‌ها</code>\nمثال:\n"
        "<code>آقای رضایی خرید کلنگی | 0912 111 2222 | کلنگی,خریدار</code>",
        parse_mode=ParseMode.HTML)


async def cmd_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """افزودن گروهی: چند خط «نام | شماره» یا «نام — شماره» را یک‌جا وارد می‌کند."""
    if not await guard(update):
        return
    uid = update.effective_user.id
    SESS.setdefault(uid, {})["await"] = "bulk"
    await update.message.reply_text(
        "📥 <b>افزودن گروهی</b>\n"
        "چند خط بنویس (هر خط یک نفر) — همان‌طور که در دفترچه هست:\n\n"
        "<code>حسین کوکبیان | 09127814246\n"
        "حسین صمدی | 0912248127\n"
        "09123456789</code>\n\n"
        "قالب‌های قبول‌شده: <code>نام | شماره</code> · <code>نام — شماره</code> · "
        "<code>نام , شماره</code> · یا فقط خودِ شماره در یک خط.\n"
        "<i>برچسب «دستی» می‌خورد و همان لحظه قابل جست‌وجو می‌شود. "
        "می‌توانی با ویس هم بگویی — خودم تبدیل می‌کنم.</i>",
        parse_mode=ParseMode.HTML)


async def handle_await(update: Update, context: ContextTypes.DEFAULT_TYPE, sess: dict, text: str) -> None:
    uid = update.effective_user.id
    mode = sess.pop("await", None)
    if mode == "bulk":
        items = parse_txt_list(text) or []
        if not items:
            await update.message.reply_text("🤷 از این متن چیزی درنیامد. هر خط: <code>نام | شماره</code>",
                                            parse_mode=ParseMode.HTML)
            return
        for it in items:
            it["tags"] = it.get("tags") or "دستی"
        res = st.merge_extra(items)
        load_engine()
        await update.message.reply_text(
            f"✅ <b>{fa(res['added'])}</b> مخاطب اضافه شد"
            + (f" · ♻️ <b>{fa(res['updated'])}</b> به‌روزرسانی" if res.get("updated") else "")
            + f"\n🗃 مجموع دفترچه: <b>{fa(filtered_count())}</b>",
            parse_mode=ParseMode.HTML)
        return
    if mode == "add":
        parts = [p.strip() for p in re.split(r"[|،]", text)]
        if len(parts) < 2:
            await update.message.reply_text("❗️ قالب درست نیست. مثال:\n"
                                            "<code>آقای رضایی | 09121112222 | کلنگی,خریدار</code>",
                                            parse_mode=ParseMode.HTML)
            return
        name, phone = parts[0], parts[1]
        tags = parts[2] if len(parts) > 2 else ""
        if not digits_only(phone):
            await update.message.reply_text("❗️ شمارهٔ درست بفرست.")
            return
        STORE.add_manual(uid, name, phone, tags)
        st.merge_extra([{"name": name, "phone": phone, "tags": tags, "source": "دستی"}])
        load_engine()
        await update.message.reply_text(
            f"✅ افزوده شد:\n<b>{esc(name)}</b>\n<code>{esc(to_latin_digits(phone))}</code>",
            parse_mode=ParseMode.HTML)
    elif mode == "note":
        cid = sess.get("note_cid")
        STORE.conn.execute("INSERT INTO settings(user_id,key,value) VALUES(?,?,?) "
                           "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
                           (uid, f"note:{cid}", json.dumps(text, ensure_ascii=False)))
        STORE.conn.commit()
        await update.message.reply_text("📝 یادداشت ذخیره شد.")


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    uid = update.effective_user.id
    q = " ".join(context.args) if context.args else (SESS.get(uid, {}).get("query") or "")
    if not q:
        await update.message.reply_text("مثال: <code>/watch #کلنگی متر:500</code>\n"
                                        "یا بعد از یک جست‌وجو فقط <code>/watch</code> را بفرست.",
                                        parse_mode=ParseMode.HTML)
        return
    ids = sort_ids(run_search(uid, q), "score")
    wid = STORE.add_watch(uid, q, ids)
    await update.message.reply_text(
        f"🧭 دیده‌بان ساخته شد (#{fa(wid)})\nعبارت: <code>{esc(q)}</code>\n"
        f"الآن {fa(len(ids))} نتیجه دارد. هر روز ساعت "
        f"{fa(CFG.get('watch_hour', 10))} خبر می‌دهم اگر مخاطب جدیدی اضافه شد.",
        parse_mode=ParseMode.HTML)


async def cmd_watches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    uid = update.effective_user.id
    ws = STORE.watches(uid)
    if not ws:
        await update.message.reply_text("🧭 دیده‌بانی نداری.\nبا <code>/watch کلنگی</code> بساز "
                                        "تا مخاطب جدید مرتبط را خبرت کنم.", parse_mode=ParseMode.HTML)
        return
    rows = [[InlineKeyboardButton(f"❌ {w['query'][:38]}", callback_data=f"wdel:{w['id']}")]
            for w in ws]
    txt = ["🧭 <b>دیده‌بان‌ها</b>"]
    for w in ws:
        txt.append(f"• <code>{esc(w['query'])}</code> — {fa(len(json.loads(w['last_ids'])))} نتيجة ذخیره‌شده"
                   f" | هشدارها: {fa(w['hits'])}")
    await update.message.reply_text("\n".join(txt), parse_mode=ParseMode.HTML,
                                    reply_markup=InlineKeyboardMarkup(rows))


# ---------------------------------------------------------- callbackها ------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not await guard(update):
        return
    await q.answer()
    uid = update.effective_user.id
    data = q.data or ""
    sess = SESS.setdefault(uid, {})

    if data.startswith("pg:"):
        if not sess.get("ids"):
            await q.answer("اول یک جست‌وجو کن 🙂", show_alert=True)
            return
        page = int(data.split(":")[1])
        text, kb = render_page(sess, page, sess.get("query", ""))
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb,
                                      disable_web_page_preview=True)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise
        return

    if data.startswith("f:"):
        cid = int(data.split(":")[1])
        on = STORE.toggle_fav(uid, cid)
        c = ENGINE.get(cid)
        rows = []
        for row in card_keyboard(c, sess.get("page", 0),
                                 max(1, (len(sess.get("ids", [1])) + PAGE_SIZE - 1) // PAGE_SIZE),
                                 uid, sess.get("query", "")).inline_keyboard:
            rows.append(row)
        try:
            await q.edit_message_reply_markup(InlineKeyboardMarkup(rows))
        except BadRequest:
            pass
        await q.answer("⭐ ذخیره شد" if on else "حذف شد", show_alert=False)
        return

    if data in ("padd", "paddsure", "pcancel"):
        items = PHOTO_PENDING.pop(uid, None)
        if not items:
            await q.answer("چیزی برای افزودن نیست — یک عکس تازه بفرست.", show_alert=True)
            return
        if data == "pcancel":
            try:
                await q.edit_message_text("❌ لغو شد — چیزی اضافه نشد.")
            except BadRequest:
                pass
            return
        if data == "paddsure":
            items = [it for it in items if it.get("sure", True)]
            if not items:
                await q.answer("مورد مطمئنی نبود", show_alert=True)
                return
        for it in items:
            it.pop("votes", None)
            it.pop("sure", None)
        res = st.merge_extra(items)
        load_engine()
        await q.answer("اضافه شد ✅")
        try:
            await q.edit_message_reply_markup(None)
        except BadRequest:
            pass
        await q.message.reply_text(
            f"✅ <b>{fa(res['added'])}</b> مخاطب تازه اضافه شد"
            + (f" · ♻️ <b>{fa(res['updated'])}</b> مورد به‌روزرسانی شد" if res.get("updated") else "")
            + f"\n🗃 مجموع دفترچه: <b>{fa(filtered_count())}</b>\n\n"
            "🏷 برچسب «عکس» خورده — با زدن دکمهٔ «🏷 برچسب‌ها» پیدایشان کن.",
            parse_mode=ParseMode.HTML)
        return

    if data.startswith("info:"):
        c = ENGINE.get(int(data.split(":")[1]))
        if not c:
            return
        rows = [
            [InlineKeyboardButton("📋 کپی شماره", copy_text=CopyTextButton(text=e164(c))),
             InlineKeyboardButton("📄 کپی خط کامل",
                                  copy_text=CopyTextButton(text=f"{c.get('name') or ''} — {c.get('phone_raw') or ''}"))],
        ]
        saved = STORE.is_fav(uid, c["id"])
        rows.append([
            InlineKeyboardButton("⭐ ذخیره‌شده (حذف)" if saved else "⭐ ذخیره",
                                 callback_data=f"f:{c['id']}"),
            InlineKeyboardButton("📝 یادداشت", callback_data=f"note:{c['id']}"),
            InlineKeyboardButton("🔗 مشابه", callback_data=f"sim:{c['id']}"),
        ])
        if sess.get("ids"):
            rows.append([InlineKeyboardButton("🔙 بازگشت به نتایج",
                                              callback_data=f"pg:{sess.get('page', 0)}")])
        await q.message.reply_text(full_info_text(c, uid), parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup(rows),
                                   disable_web_page_preview=True)
        return

    if data.startswith("note:"):
        cid = int(data.split(":")[1])
        sess["await"] = "note"
        sess["note_cid"] = cid
        await q.message.reply_text("📝 یادداشتت را برای این مخاطب بنویس و بفرست:")
        return

    if data.startswith("sim:"):
        cid = int(data.split(":")[1])
        c = ENGINE.get(cid)
        sims = ENGINE.similar(cid, 40)
        if not sims:
            await q.message.reply_text("🔗 مخاطب مشابهی پیدا نشد.")
            return
        sess.update({"uid": uid, "ids": [x["id"] for x in sims], "total": len(sims), "page": 0,
                     "title": f"🔗 مشابه «{c['name']}»", "query": "", "relaxed": False, "fuzzy": False})
        text, kb = render_page(sess, 0, "")
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("dup:"):
        cid = int(data.split(":")[1])
        dups = [ENGINE.get(cid)] + ENGINE.duplicates_of(cid)
        sess.update({"uid": uid, "ids": [d["id"] for d in dups if d], "total": len(dups),
                     "page": 0, "title": "🔁 رکوردهای هم‌شماره", "query": "",
                     "relaxed": False, "fuzzy": False})
        text, kb = render_page(sess, 0, "")
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("share:"):
        c = ENGINE.get(int(data.split(":")[1]))
        if not c:
            return
        body = f"{c['name']}\n{e164(c)}\n{' · '.join(c['tags'][:5])}"
        url = "https://t.me/share/url?" + "text=" + __import__("urllib.parse", fromlist=["quote"]).quote(body)
        await q.message.reply_text("🔗 برای اشتراک‌گذاری این کارت:",
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 ارسال به چت دیگر", url=url)]]))
        return

    if data.startswith("ctags:"):
        c = ENGINE.get(int(data.split(":")[1]))
        if not c:
            return
        rows, row = [], []
        for t in c["tags"]:
            row.append(InlineKeyboardButton(f"#{t}", callback_data=f"tg:{t[:40]}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("🏠 بستن", callback_data="noop")])
        await q.message.reply_text(f"🏷 برچسب‌های <b>{esc(c['name'])}</b>\n"
                                   "برای دیدن همهٔ مخاطبین یک برچسب، رویش بزن:",
                                   parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("tg:"):
        tag = data[3:]
        await do_search(update, context, f"#{tag}")
        return

    if data.startswith("q:"):
        arg = data[2:]
        if arg == "random":
            await cmd_rand(update, SimpleNamespace(bot=context.bot, args=["1"]))
            return
        await do_search(update, context, arg)
        return

    if data == "stats:1":
        await cmd_stats(update, context)
        return

    if data.startswith("wdel:"):
        wid = int(data.split(":")[1])
        STORE.del_watch(wid, uid)
        await q.message.reply_text("🗑 دیده‌بان حذف شد.")
        return

    if data.startswith("set:"):
        _, key, val = data.split(":", 2)
        if key == "mode" and val in MODE_LABELS:
            STORE.set(uid, "mode", val)
        elif key == "kind" and val in ("all", "mobile", "landline", "intl"):
            STORE.set(uid, "kind", val)
        elif key == "dup" and val in ("show", "only", "hide"):
            STORE.set(uid, "dup", val)
        elif key == "sort" and val in ("score", "name", "newest"):
            STORE.set(uid, "sort", val)
        try:
            await q.edit_message_text(filters_text(uid), parse_mode=ParseMode.HTML,
                                      reply_markup=filters_keyboard(uid))
        except BadRequest:
            pass
        return

    if data == "re:1":
        qy = sess.get("query")
        if qy:
            await do_search(update, context, qy)
        else:
            await q.message.reply_text("آخرین جست‌وجویی موجود نیست.")
        return

    if data == "re:1":
        qy = sess.get("query")
        if qy:
            await do_search(update, context, qy)
        else:
            await q.message.reply_text("آخرین جست‌وجویی موجود نیست.")
        return

    if data.startswith("chip:"):
        what = data.split(":")[1]
        if what in ("mobile", "landline", "intl"):
            STORE.set(uid, "kind", what)
        elif what == "nodup":
            STORE.set(uid, "dup", "hide")
        elif what == "reset":
            STORE.set(uid, "kind", "all")
            STORE.set(uid, "dup", "show")
        qy = sess.get("query")
        if qy:
            await do_search(update, context, qy, remember=False)
            labels = {"mobile": "📱 فقط موبایل", "landline": "☎️ فقط ثابت",
                      "intl": "🌍 بین‌المللی", "nodup": "🚫 بدون تکراری", "reset": "♻️ بازنشانی"}
            await q.answer(labels.get(what, "اعمال شد"))
        else:
            await q.answer("اول یک جست‌وجو کن 🙂", show_alert=True)
        return

    if data.startswith("vcf:"):
        c = ENGINE.get(int(data.split(":")[1]))
        if not c:
            await q.answer("مخاطب پیدا نشد", show_alert=True)
            return
        try:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=BytesIO(sm.vcf(c).encode("utf-8")),
                filename=sm.vcf_filename(c),
                caption=(f"👤 <b>{esc(c['name'])}</b>\n"
                         f"<code>{esc(fmt_phone(c))}</code>\n"
                         f"<i>فایل را باز کن و «افزودن به مخاطبین» را بزن.</i>"),
                parse_mode=ParseMode.HTML)
            await q.answer("کارت مخاطب فرستاده شد 👤")
        except Exception as e:
            log.warning("ارسال vCard ناموفق: %s", e)
            await q.message.reply_text("⚠️ ارسال کارت نشد — از دکمهٔ «📋 کپی شماره» استفاده کن.")
        return

    if data.startswith("menu:"):
        what = data.split(":")[1]
        if what == "filters":
            await q.message.reply_text(filters_text(uid), parse_mode=ParseMode.HTML,
                                       reply_markup=filters_keyboard(uid))
        elif what == "sort":
            await q.message.reply_text("🔽 ترتیب نتیجه‌ها:", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎯 ارتباط", callback_data="set:sort:score"),
                InlineKeyboardButton("🔤 الفبا", callback_data="set:sort:name"),
                InlineKeyboardButton("🆕 جدیدترین", callback_data="set:sort:newest")]]))
        elif what == "export":
            await cmd_export(update, context)
        return

    if data.startswith("list:"):
        what = data.split(":")[1]
        if what == "dup":
            ids = [c["id"] for c in ENGINE.contacts if c["is_dup"]]
        else:
            ids = [c["id"] for c in ENGINE.contacts
                   if not c["valid"] or len(digits_only(c["_digits"])) < 10]
        if not ids:
            await q.message.reply_text("✅ چیزی برای نمایش نیست.")
            return
        sess.update({"uid": uid, "ids": ids, "total": len(ids), "page": 0,
                     "title": "🔁 تکراری‌ها" if what == "dup" else "⚠️ شماره‌های مشکوک",
                     "query": "", "relaxed": False, "fuzzy": False})
        text, kb = render_page(sess, 0, "")
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("exp:"):
        await handle_export(update, context, data.split(":")[1], q)
        return

    if data == "noop":
        return


# ------------------------------------------------------------ خروجی‌ها ------
def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def contact_row(c: dict) -> dict:
    return {
        "نام": c["name"], "شماره_بین‌المللی": e164(c), "شماره_داخلی": c.get("phone_local", ""),
        "نوع": {"mobile": "موبایل", "landline": "ثابت", "intl": "بین‌المللی"}.get(c["kind"], ""),
        "کشور": c.get("country_name", ""), "اپراتور": c.get("operator", ""),
        "منطقه": c.get("region", ""), "برچسب‌ها": ", ".join(c.get("tags", [])),
        "متراژ": c.get("area") or "", "پلاک": c.get("plak") or "", "طبقه": c.get("floor") or "",
        "تکراری": "بله" if c.get("is_dup") else "خیر", "شناسه": c["id"],
    }


def write_xlsx(path: Path, rows: list[dict]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "مخاطبین"
    ws.sheet_view.rightToLeft = True
    fields = list(rows[0].keys()) if rows else ["نام"]
    ws.append(fields)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F6F4E")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for r in rows:
        ws.append([r.get(k, "") for k in fields])
    widths = {"نام": 34, "شماره_بین‌المللی": 17, "شماره_داخلی": 16, "برچسب‌ها": 34,
              "کشور": 12, "اپراتور": 12, "منطقه": 12}
    for i, f in enumerate(fields, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(f, 10)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)


def write_vcf(path: Path, cs: list[dict]) -> None:
    out = []
    for c in cs:
        nm = c["name"].replace(",", " ").replace(";", " ")
        parts = [p for p in re.split(r"\s+", nm) if p]
        last = parts[0] if parts else "مخاطب"
        first = " ".join(parts[1:])
        out += ["BEGIN:VCARD", "VERSION:3.0",
                f"N:{last};{first};;;", f"FN:{nm}",
                f"TEL;TYPE=CELL:{e164(c)}",
                f"NOTE:{'، '.join(c.get('tags', []))}"]
        out.append("END:VCARD")
    path.write_text("\r\n".join(out), encoding="utf-8")


async def handle_export(update: Update, context: ContextTypes.DEFAULT_TYPE, what: str, q=None):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    sess = SESS.get(uid, {})
    stamp = time.strftime("%Y%m%d-%H%M")
    note_target = q.message if q else update.message
    try:
        if what in ("csv", "xlsx", "vcf", "issues"):
            if what == "issues":
                cs = [c for c in ENGINE.contacts if not c["valid"] or len(digits_only(c["_digits"])) < 10
                      or c["is_dup"]]
                name = "issues"
            else:
                ids = sess.get("ids") or [c["id"] for c in ENGINE.contacts]
                cs = [ENGINE.get(int(i)) for i in ids]
                cs = [c for c in cs if c]
                name = "last-search"
            if not cs:
                await note_target.reply_text("چیزی برای خروجی گرفتن نیست — اول یک جست‌وجو کن.")
                return
            rows = [contact_row(c) for c in cs]
            if what == "csv" or what == "issues":
                p = EXPORTS / f"{name}-{stamp}.csv"
                write_csv(p, rows, list(rows[0].keys()))
            elif what == "xlsx":
                p = EXPORTS / f"{name}-{stamp}.xlsx"
                write_xlsx(p, rows)
            else:
                p = EXPORTS / f"{name}-{stamp}.vcf"
                write_vcf(p, cs)
        elif what in ("allxlsx", "allcsv"):
            rows = [contact_row(c) for c in ENGINE.contacts]
            ext = "xlsx" if what == "allxlsx" else "csv"
            p = EXPORTS / f"full-phonebook-{stamp}.{ext}"
            if ext == "xlsx":
                write_xlsx(p, rows)
            else:
                write_csv(p, rows, list(rows[0].keys()))
        elif what == "backup":
            import zipfile
            rows = [contact_row(c) for c in ENGINE.contacts]
            cp = EXPORTS / f"backup-{stamp}.csv"
            write_csv(cp, rows, list(rows[0].keys()))
            p = EXPORTS / f"backup-{stamp}.zip"
            with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(cp, "contacts.csv")
                if (DATA / "contacts.json").exists():
                    z.write(DATA / "contacts.json", "contacts.json")
                if (DATA / "extra.json").exists():
                    z.write(DATA / "extra.json", "extra.json")
                if (DATA / "bot.sqlite3").exists():
                    z.write(DATA / "bot.sqlite3", "bot.sqlite3")
            cp.unlink(missing_ok=True)
        else:
            return
    except Exception as e:  # pragma: no cover
        log.exception("export failed")
        await note_target.reply_text(f"❗️ خطا در ساخت فایل: {esc(str(e))}")
        return

    size = p.stat().st_size / 1024
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
    with open(p, "rb") as f:
        await context.bot.send_document(chat_id, f, filename=p.name,
                                        caption=f"📤 {esc(p.name)} — {fa(round(size))} کیلوبایت")
    try:
        if p.stat().st_size > 0 and p.suffix in (".csv", ".xlsx", ".vcf"):
            p.unlink()  # فایل‌های تولیدی را بعد از ارسال پاک کن
    except OSError:
        pass


# ------------------------------------------------------- ایمپورت فایل -------
def parse_vcf(text: str) -> list[dict]:
    out = []
    cards = re.split(r"BEGIN:VCARD", text, flags=re.IGNORECASE)
    for card in cards:
        if not card.strip():
            continue
        name = ""
        phones = []
        for line in card.splitlines():
            line = line.strip()
            m = re.match(r"^FN(?:;[^:]*)?:(.+)$", line, re.IGNORECASE)
            if m:
                name = m.group(1).strip()
            m = re.match(r"^N(?:;[^:]*)?:(.+)$", line, re.IGNORECASE)
            if m and not name:
                parts = [p.strip() for p in m.group(1).split(";")]
                name = " ".join([p for p in parts if p][::-1]).strip()
            for m in re.finditer(r"^TEL(?:;[^:]*)?:(.+)$", line, re.IGNORECASE | re.MULTILINE):
                phones.append(m.group(1).strip())
        for ph in phones:
            out.append({"name": name, "phone": ph, "source": "vcf"})
    # VCFهای خط‌شکسته
    if not out and "TEL" in text.upper():
        cur_name = ""
        for line in text.splitlines():
            line = line.strip()
            m = re.match(r"^FN(?:;[^:]*)?:(.+)$", line, re.IGNORECASE)
            if m:
                cur_name = m.group(1).strip()
            m = re.match(r"^TEL(?:;[^:]*)?:(.+)$", line, re.IGNORECASE)
            if m:
                out.append({"name": cur_name, "phone": m.group(1).strip(), "source": "vcf"})
    return out


def parse_txt_list(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\d+[.)]\s*(.+?)\s*[—–-]{1,2}\s*(.+)$", line)
        if m:
            out.append({"name": m.group(1).strip(), "phone": m.group(2).strip(), "source": "متن"})
            continue
        m = re.match(r"^(.+?)\s*[|,;،\t]\s*(\+?[\d\s\-()]{6,})\s*(?:[|]\s*(.*))?$", line)
        if not m:
            m = re.match(r"^(.+?)\s*[—–]{1,2}\s*(\+?[\d\s\-()]{6,})\s*(?:[|]\s*(.*))?$", line)
        if m:
            out.append({"name": m.group(1).strip(), "phone": m.group(2).strip(),
                        "tags": (m.group(3) or "").strip(), "source": "bulk"})
            continue
        d = digits_only(line)
        if len(d) >= 8:
            nm = re.sub(r"[\d+()\-\s]{6,}", " ", line).strip(" ,;-")
            out.append({"name": nm or "بدون نام", "phone": line, "source": "متن"})
    return out


PHOTO_PENDING: dict[int, list[dict]] = {}   # عکس‌های خوانده‌شده که منتظر تأیید کاربرند


async def cmd_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """راهنمای بخش «افزودن از عکس»."""
    if not await guard(update):
        return
    ready = ocr_mod.available()
    txt = ["📷 <b>افزودن از عکس</b>", ""]
    if not ready:
        txt.append("⚠️ موتور خواندن عکس روی سرور نصب نیست. به مالک ربات خبر بده تا نصب شود.")
    txt += [
        "یک عکس از فهرست شماره‌ها (چاپی، اسکرین‌شات، تابلوی آگهی) برایم بفرست — ",
        "خودم شماره‌ها را درمی‌آورم و <b>قبل از افزودن</b> نشانت می‌دهم:",
        "",
        "۱) عکس را بفرست",
        "۲) لیست خوانده‌شده را ببین و اگر درست بود «➕ افزودن» را بزن",
        "۳) همان لحظه در دفترچه قابل جست‌وجو می‌شود (برچسب: عکس)",
        "",
        "<i>هر موردی که نخواستی، به‌جای افزودن، متن درست را بنویس تا دستی اضافه کنم "
        "(/add نام | شماره).</i>",
    ]
    await update.effective_message.reply_text("\n".join(txt), parse_mode=ParseMode.HTML)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """عکس فهرست شماره‌ها → خواندن با OCR → نمایش برای تأیید → افزودن به لیست."""
    if not await guard(update):
        return
    msg = update.effective_message
    uid = update.effective_user.id
    photo = msg.photo[-1] if msg.photo else None
    doc = msg.document
    is_img_doc = bool(doc and (doc.mime_type or "").lower().startswith("image/"))
    if not photo and not is_img_doc:
        return
    if not ocr_mod.available():
        await msg.reply_text("⚠️ موتور خواندن عکس روی سرور نیست — به مالک خبر بده.")
        return
    status = await msg.reply_text("📷 عکس را گرفتم — دارم شماره‌ها را درمی‌آورم…")
    try:
        src = photo or doc
        f = await src.get_file()
        raw = bytes(await f.download_as_bytearray())
        if len(raw) > 15 * 1024 * 1024:
            await status.edit_text("❗️ عکس سنگین است (حداکثر ۱۵ مگابایت).")
            return
        suffix = ".png" if "png" in ((doc.mime_type or "") if is_img_doc else "").lower() else ".jpg"
        tmp = Path(tempfile.gettempdir()) / f"melk_photo_{uid}{suffix}"
        tmp.write_bytes(raw)
        items = ocr_mod.extract_with_reliability(tmp)
    except Exception as e:  # noqa: BLE001
        log.exception("ocr photo: %s", e)
        await status.edit_text(f"❌ خواندن عکس نشد: {type(e).__name__}")
        return

    if not items:
        await status.edit_text(
            "🤷 از این عکس شماره‌ای درنیامد.\n\n"
            "موتور خواندن من برای <b>متن چاپی</b> خوب است (اسکرین‌شات واتس‌اپ، فهرست تایپی، "
            "عکس تابلوی آگهی) ولی <b>دست‌خط دفترچه</b> را نمی‌تواند بخواند — این محدودیت خود موتور است.\n\n"
            "راه‌های جایگزین:\n"
            "۱) شماره‌ها را بنویس و بفرست، هر خط یکی: <code>نام | ۰۹۱۲…</code>\n"
            "۲) یا دستور /add (یک‌یکی اضافه می‌کند)\n"
            "۳) اگر عکس اسکرین‌شات است، کادر را بزرگ‌تر/روشن‌تر بگیر و دوباره بفرست",
            parse_mode=ParseMode.HTML)
        return

    PHOTO_PENDING[uid] = items
    sure, unsure = ocr_mod.counts(items)
    body = ocr_mod.preview_lines(items)
    head = (f"📷 <b>{fa(len(items))} شماره از عکس خوانده شد</b>\n"
            f"✅ مطمئن: <b>{fa(sure)}</b>"
            + (f" · ⚠️ مشکوک: <b>{fa(unsure)}</b>" if unsure else "") + "\n"
            "<i>یک بار نگاه کن؛ «⚠️» یعنی رقم‌هایش در دو خوانش یکی نبود.</i>\n\n")
    btns = [InlineKeyboardButton(f"➕ افزودن همه ({fa(len(items))})", callback_data="padd")]
    if unsure and sure:
        btns.append(InlineKeyboardButton(f"✅ فقط مطمئن‌ها ({fa(sure)})", callback_data="paddsure"))
    btns.append(InlineKeyboardButton("❌ لغو", callback_data="pcancel"))
    kb = InlineKeyboardMarkup([btns])
    chunk = head + f"<pre>{esc(body)}</pre>"
    if len(chunk) > 3800:
        chunk = head + f"<pre>{esc(ocr_mod.preview_lines(items, 12))}</pre>"
    await status.edit_text(chunk, parse_mode=ParseMode.HTML, reply_markup=kb)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    doc = update.message.document
    if not doc:
        return
    fname = (doc.file_name or "").lower()
    if not fname.endswith((".vcf", ".txt", ".csv", ".json", ".xlsx")):
        await update.message.reply_text("📥 فرمت‌های پشتیبانی‌شده: .vcf و .txt و .csv و .json و .xlsx")
        return
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("❗️ فایل خیلی بزرگ است (حداکثر ۲۰ مگابایت).")
        return
    status = await update.message.reply_text("📥 در حال خواندن فایل…")
    f = await doc.get_file()
    raw = bytes(await f.download_as_bytearray())
    text = raw.decode("utf-8", errors="ignore")
    if fname.endswith(".xlsx"):
        import io
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        text = "\n".join(
            ", ".join("" if c is None else str(c) for c in row)
            for row in wb[wb.sheetnames[0]].iter_rows(values_only=True))
    items = []
    if fname.endswith(".vcf") or "BEGIN:VCARD" in text.upper():
        items = parse_vcf(text)
    elif fname.endswith(".json"):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                for d in data:
                    if isinstance(d, dict):
                        items.append({"name": d.get("name") or d.get("نام") or "",
                                      "phone": d.get("phone") or d.get("شماره") or "",
                                      "tags": d.get("tags") or "", "source": "json"})
        except Exception:
            items = []
    if not items:
        items = parse_txt_list(text)
    if not items:
        await status.edit_text("🤷 نتوانستم مخاطبی از این فایل بیرون بکشم. "
                               "فایل VCF یا لیست «نام — شماره» بفرست.")
        return
    res = st.merge_extra(items)
    load_engine()
    await status.edit_text(
        f"✅ پردازش شد\n➕ افزوده: <b>{fa(res['added'])}</b>\n"
        f"♻️ به‌روزرسانی: <b>{fa(res['updated'])}</b>\n"
        f"🗃 مجموع مخاطبین واردشده: <b>{fa(res['total'])}</b>\n"
        f"👥 کل دفترچهٔ قابل جست‌وجو: <b>{fa(filtered_count())}</b>",
        parse_mode=ParseMode.HTML)


# ------------------------------------------------------------- اینلاین ------
async def on_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.inline_query
    if not q or not q.query.strip():
        return
    uid = q.from_user.id
    if not allowed(uid):
        return
    res = run_search(uid, q.query, user_settings(uid))
    out = []
    for score, c, _, _ in res["items"][:25]:
        tel = e164(c)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📋 کپی", copy_text=CopyTextButton(text=tel)),
            InlineKeyboardButton("💬 واتس‌اپ", url=wa_link(c) or "https://wa.me/"),
        ]])
        body = (f"<b>{esc(c['name'])}</b>\n<code>{esc(tel)}</code>\n"
                f"🏷 {' · '.join(esc(t) for t in c['tags'][:5])}")
        out.append(InlineQueryResultArticle(
            id=str(c["id"]),
            title=c["name"][:60],
            description=f"{fmt_phone(c)} · {' · '.join(c['tags'][:4])}",
            input_message_content=InputTextMessageContent(body, parse_mode=ParseMode.HTML),
            reply_markup=kb,
        ))
    await q.answer(out, cache_time=5, is_personal=True)


# ---------------------------------------------------------- 보고/دیده‌بان ---
async def watch_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """هر روز: بررسی دیده‌بان‌ها و اطلاع مخاطب جدید."""
    for w in STORE.all_watches():
        uid = w["user_id"]
        try:
            prev = set(json.loads(w["last_ids"] or "[]"))
            res = run_search(uid, w["query"], user_settings(uid))
            ids = sort_ids(res, "score")
            new = [i for i in ids if i not in prev]
            STORE.update_watch(w["id"], ids, hits=1 if new else 0)
            if not new:
                continue
            sess = {"uid": uid, "ids": new[:40], "total": len(new), "page": 0,
                    "title": f"🧭 دیده‌بان «{w['query']}» — {fa(len(new))} مخاطب جدید",
                    "query": "", "relaxed": False, "fuzzy": False}
            await context.bot.send_message(
                uid, f"🧭 <b>دیده‌بان</b> <code>{esc(w['query'])}</code>\n"
                     f"👀 {fa(len(new))} مخاطب جدید مرتبط پیدا شد:", parse_mode=ParseMode.HTML)
            for cid in new[:6]:
                c = ENGINE.get(cid)
                if c:
                    await context.bot.send_message(uid, card_text(c, 0, 0, "", False),
                                                   parse_mode=ParseMode.HTML)
        except (Forbidden, BadRequest) as e:
            log.warning("watch %s: %s", w["id"], e)
        except Exception as e:  # pragma: no cover
            log.exception("watch job: %s", e)


HEARTBEAT = DATA / "heartbeat.json"
HB = {"ok_ts": 0.0, "updates": 0, "fails": 0, "restarts": 0, "last_error": "",
      "started": time.time(), "last_ok": 0.0, "gap": 0.0, "uptime": 0.0}
GAP_NOTIFY = 45          # اگر بیش از این مدت اتصال نبوده، به مالک خبر بده


def write_heartbeat(extra: dict | None = None) -> None:
    data = {
        "ts": time.time(),
        "ok_ts": HB["ok_ts"],
        "updates": HB["updates"],
        "fails": HB["fails"],
        "restarts": HB["restarts"],
        "last_error": HB["last_error"],
        "started": HB["started"],
        "last_ok": HB["last_ok"],
        "gap": HB["gap"],
        "pid": os.getpid(),
    }
    if extra:
        data.update(extra)
    try:
        tmp = HEARTBEAT.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(HEARTBEAT)
    except OSError:
        pass


async def heartbeat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """هر ۲۰ ثانیه اتصال را می‌سنجد؛ اگر polling گیر کرده باشد، بدون ری‌استارت کل ربات تازه‌اش می‌کند."""
    app = context.application
    updater = app.updater
    HB["uptime"] = time.time() - HB["started"]
    running = bool(updater and updater.running)
    ok = False
    err = ""
    try:
        await asyncio.wait_for(app.bot.get_me(), timeout=8)
        ok = True
        HB["ok_ts"] = time.time()
        HB["fails"] = 0
        # ---- بازگشت پس از قطعی: به مالک خبر بده ----
        gap = time.time() - HB["last_ok"] if HB["last_ok"] else 0
        if gap > GAP_NOTIFY:
            HB["gap"] = gap
            pend = 0
            try:
                info = await asyncio.wait_for(app.bot.get_webhook_info(), timeout=8)
                pend = info.pending_update_count
            except Exception:
                pass
            log.warning("🔌 بازگشت پس از %.0f ثانیه قطعی (%d پیام در صف)", gap, pend)
            text = ("🔌 <b>دوباره وصل شدم</b>\n"
                    f"مدت قطعی: <b>{fa(int(gap))}</b> ثانیه\n"
                    f"پیام‌های در صف که پردازش می‌شوند: <b>{fa(pend)}</b>\n\n"
                    "<i>هیچ پیامی از دست نرفته — همه را جواب می‌دهم.</i>")
            for admin in sorted(ADMINS):
                try:
                    await app.bot.send_message(admin, text, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
        HB["last_ok"] = time.time()
    except Exception as e:  # noqa: BLE001
        HB["fails"] += 1
        err = f"{type(e).__name__}: {e}"
        HB["last_error"] = err
        log.warning("💔 ضربان ناموفق (%d): %s", HB["fails"], err)

    write_heartbeat({"polling": running})   # هر ۱۰ ثانیه وضعیت را تازه می‌کند تا پایش زنده از بیرون ممکن باشد

    # 🛡 خودترمیمی: اگر polling مرده یا اتصال مکرراً شکست خورده، فرآیند را سخت تمام می‌کنیم
    # (لانچر بیرونی بلافاصله نسخهٔ تازه را بالا می‌آورد — تجربهٔ اثبات‌شده)
    dead_polling = bool(updater and not updater.running and not app.updater.running)
    if HB["uptime"] > 45 and dead_polling:
        log.error("☠️ حلقهٔ polling مرده است — خروج برای راه‌اندازی تازه")
        write_heartbeat({"reason": "polling-dead"})
        os._exit(42)
    if HB["fails"] >= 6:
        log.error("☠️ %d شکست پیاپی اتصال — خروج برای راه‌اندازی تازه", HB["fails"])
        write_heartbeat({"reason": "network-fails"})
        os._exit(43)


async def log_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """گروه ۱-: هر آپدیت دریافتی را لاگ می‌کند تا سلامت polling قابل رصد باشد."""
    HB["updates"] += 1
    HB["ok_ts"] = time.time()
    write_heartbeat()
    u = update.effective_user
    what = ""
    if update.message and update.message.text:
        what = update.message.text[:60]
    elif update.callback_query:
        what = update.callback_query.data or ""
    elif update.inline_query:
        what = update.inline_query.query[:60]
    log.info("📥 update از %s (%s): %s", u.full_name if u else "?", u.id if u else "?", what)


async def post_init(app: Application) -> None:
    try:
        await _post_init(app)
    except Exception as e:  # نوسان شبکه در استارت نباید ربات را زمین بزند
        log.warning("post_init با خطا رد شد (بی‌خطر): %s", e)


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "شروع و راهنمای سریع"),
        BotCommand("search", "جست‌وجو (پیش‌فرض: هر متنی بنویس)"),
        BotCommand("tags", "برچسب‌ها و دسته‌ها"),
        BotCommand("favs", "علاقه‌مندی‌ها"),
        BotCommand("stats", "آمار دفترچه"),
        BotCommand("quality", "کیفیت داده و مشکل‌دارها"),
        BotCommand("dups", "رکوردهای تکراری"),
        BotCommand("rand", "مخاطب شانسی"),
        BotCommand("export", "خروجی CSV / Excel / vCard"),
        BotCommand("add", "افزودن مخاطب جدید"),
        BotCommand("photo", "افزودن از عکس (شماره‌ها را از عکس می‌خواند)"),
        BotCommand("bulk", "افزودن گروهی (چند خط «نام | شماره»)"),
        BotCommand("watch", "ساخت دیده‌بان"),
        BotCommand("watches", "لیست دیده‌بان‌ها"),
        BotCommand("filters", "تنظیمات جست‌وجو"),
        BotCommand("history", "تاریخچهٔ جست‌وجو"),
        BotCommand("help", "راهنمای کامل"),
        BotCommand("ping", "سلامت و وضعیت اتصال"),
        BotCommand("id", "شناسهٔ من"),
    ])
    try:
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:
        log.warning("menu button: %s", e)
    make_health_server()
    me = await app.bot.get_me()
    log.info("ربات آماده است: @%s (id=%s) — %s مخاطب ایندکس‌شده",
             me.username, me.id, filtered_count())
    # 🟢 اطلاع روشن‌شدن به مالک: تا کاربر مطمئن شود ربات واقعاً آنلاین است
    try:
        for uid in (ADMINS or set()):
            await app.bot.send_message(
                chat_id=uid,
                text=(f"🟢 <b>ربات روشن شد</b>\n"
                      f"📇 {filtered_count():,} مخاطب ایندکس‌شده\n"
                      f"برای اطمینان /ping بزن."),
                parse_mode=ParseMode.HTML)
    except Exception as e:  # noqa: BLE001
        log.warning("اطلاع روشن‌شدن ارسال نشد: %s", e)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, RetryAfter):
        await asyncio.sleep(err.retry_after)
        return
    if isinstance(err, (BadRequest, TimedOut)):
        log.warning("telegram error: %s", err)
        return
    log.error("خطای پیش‌بینی‌نشده", exc_info=err)


def make_health_server() -> "HTTPServer | None":
    """یک وب‌سرور کوچک کنار ربات: /health برای بیدارباش و مانیتورینگ."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") in ("/health", "", "/ping"):
                body = json.dumps({
                    "status": "alive",
                    "bot": (META.get("source") or "melkhunter"),
                    "contacts": filtered_count(),
                    "mode": (CFG.get("mode") or "polling"),
                    "uptime_sec": int(time.time() - HB["started"]),
                    "last_heartbeat_sec_ago": int(time.time() - HB["last_ok"]) if HB["last_ok"] else -1,
                    "updates": HB["updates"],
                    "connection_refreshes": HB["restarts"],
                }, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("Melk Hunter is running ✅".encode())

        def log_message(self, *a):  # لاگ بی‌صدا
            return

    port = int(os.environ.get("HEALTH_PORT") or CFG.get("health_port") or 0)
    if not port:
        return None
    try:
        srv = HTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        log.warning("سرور سلامت روی پورت %s بالا نیامد: %s", port, e)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("🩺 سرور سلامت روی پورت %s (مسیر /health)", port)
    return srv


def build_app() -> Application:
    if not TOKEN:
        raise SystemExit("❗️ توکن ربات تنظیم نشده. در config.json مقدار token را بگذار.")
    builder = (
        ApplicationBuilder()
        .token(TOKEN)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .post_init(post_init)
        .concurrent_updates(True)
        .connect_timeout(15)
        .read_timeout(20)
        .write_timeout(30)
        .pool_timeout(15)
        .get_updates_connect_timeout(10)
        .get_updates_read_timeout(10)
    )
    # پشتیبانی پروکسی: اگر سرور (مثلاً داخل ایران) به تلگرام دسترسی نداشت،
    # در config.json مقدار proxy را بگذار:  "proxy": "socks5://user:pass@host:port"
    proxy = CFG.get("proxy")
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(CFG.get("proxy_get_updates") or proxy)
        log.info("🌐 استفاده از پروکسی: %s", proxy.split("@")[-1])
    app = builder.build()
    app.add_handler(TypeHandler(Update, log_update), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("s", cmd_search))
    app.add_handler(CommandHandler("rand", cmd_rand))
    app.add_handler(CommandHandler("favs", cmd_favs))
    app.add_handler(CommandHandler("tags", cmd_tags))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("quality", cmd_quality))
    app.add_handler(CommandHandler("dups", cmd_dups))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("filters", cmd_filters))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("photo", cmd_photo))
    app.add_handler(CommandHandler("bulk", cmd_bulk))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("watches", cmd_watches))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(InlineQueryHandler(on_inline))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    if app.job_queue:
        app.job_queue.run_repeating(heartbeat_job, interval=10, first=6, name="heartbeat")
        app.job_queue.run_daily(
            watch_job,
            time=dtime(hour=int(CFG.get("watch_hour", 10)), minute=0, tzinfo=TEHRAN),
            name="daily-watch",
        )
    return app


def check_telegram_access() -> bool:
    """قبل از شروع، دسترسی سرور به تلگرام را می‌سنجد (مهم برای سرورهای ایران)."""
    import socket
    try:
        s = socket.create_connection(("api.telegram.org", 443), timeout=10)
        s.close()
        return True
    except OSError as e:
        log.error("⛔️ این سرور به api.telegram.org دسترسی ندارد (%s)", e)
        log.error("   راه‌حل ۱: از پروکسی استفاده کن → در config.json مقدار \"proxy\" را بگذار "
                  "(مثلاً socks5://user:pass@host:port)")
        log.error("   راه‌حل ۲: از سرور خارج از ایران (مثل سرور ژاپن چابکان) استفاده کن")
        return False


def main() -> None:
    load_engine()
    check_telegram_access()
    app = build_app()
    mode = (CFG.get("mode") or "polling").lower()
    if mode == "webhook":
        base = (CFG.get("webhook_url") or "").rstrip("/")
        if not base:
            raise SystemExit("❗️ برای حالت webhook مقدار webhook_url در config.json لازم است.")
        port = int(os.environ.get("PORT") or CFG.get("port") or 8443)
        path = CFG.get("webhook_path") or "telegram"
        url = f"{base}/{path}"
        log.info("🌐 شروع وبهوک روی پورت %s → %s", port, url)
        app.run_webhook(listen="0.0.0.0", port=port, url_path=path, webhook_url=url,
                        secret_token=(CFG.get("secret_token") or None),
                        drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
        return

    log.info("شروع polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False,
                    bootstrap_retries=-1, close_loop=False)


if __name__ == "__main__":
    main()
