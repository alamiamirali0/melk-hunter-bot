# -*- coding: utf-8 -*-
"""
نرمال‌سازی متن فارسی/عربی + پارس و استانداردسازی شماره تلفن.

هدف: کاربر هر شکلی تایپ کند («آرش»، «ارش»، «٠٩١٢...»)، ما همان را پیدا کنیم.
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- digits ----
_FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_OTHER_DIGITS = "０１２３４５６７８９"  # fullwidth
_DIGIT_MAP = {}
for i, ch in enumerate(_FA_DIGITS):
    _DIGIT_MAP[ord(ch)] = str(i)
for i, ch in enumerate(_AR_DIGITS):
    _DIGIT_MAP[ord(ch)] = str(i)
for i, ch in enumerate(_OTHER_DIGITS):
    _DIGIT_MAP[ord(ch)] = str(i)

# جداکننده‌های شماره‌ای که در فارسی رایج‌اند
_NUM_SEP = dict.fromkeys(map(ord, ".,،٬٫  ‌-–—_()[]"), " ")


def to_latin_digits(s: str) -> str:
    """همه ارقام فارسی/عربی/تمام‌عرض را به 0-9 لاتین تبدیل می‌کند."""
    return s.translate(_DIGIT_MAP)


# ------------------------------------------------------------- text norm ----
_CHAR_MAP = {
    "ي": "ی", "ى": "ی", "ﻯ": "ی", "ﻰ": "ی", "ئ": "ی",
    "ك": "ک", "ﻙ": "ک", "ڪ": "ک",
    "ة": "ه", "ۀ": "ه", "ﻩ": "ه",
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ا": "ا",
    "ؤ": "و", "و": "و",
    "٠": "0",
    "٤": "4", "٥": "5", "٦": "6",
}
# اعداد/حروف عربی پرکاربرد دیگر
_TRANS_TABLE = {ord(k): v for k, v in _CHAR_MAP.items()}
_PUNCT = dict.fromkeys(
    map(ord, ".,،؛:!؟?()[]{}«»\"'`´-–—_/\\|*+=#@&^%$~<>٪٫٬…٭•·"+ "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u2060\ufeff"),
    " ",
)


def normalize_text(s: str) -> str:
    """
    متن را برای جست‌وجو یکدست می‌کند:
    - یکسان‌سازی ی/ک/ه/الف
    - ارقام → لاتین
    - حذف نیم‌فاصله، علائم، اعراب، کشیده
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.translate(_TRANS_TABLE)
    s = "".join(c for c in s if not unicodedata.combining(c))  # اعراب
    s = s.replace("ـ", "")           # کشیده
    s = s.translate(_PUNCT)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def compact(s: str) -> str:
    """بدون فاصله (برای مطابقت چندکلمه‌ای)."""
    return normalize_text(s).replace(" ", "")


def tokenize(s: str) -> list[str]:
    return [t for t in normalize_text(s).split(" ") if t]


def digits_only(s: str) -> str:
    """فقط ارقام لاتین یک رشته را برمی‌گرداند."""
    return re.sub(r"\D", "", to_latin_digits(s or ""))


def has_digit(s: str) -> bool:
    return bool(re.search(r"\d", to_latin_digits(s or "")))


# ---------------------------------------------------------------- phone -----
# پیش‌شماره کشورها: (کد، نام، طول کل بدون +)
COUNTRY_CODES = {
    "98": ("🇮🇷", "ایران", 12),
    "971": ("🇦🇪", "امارات", 12),
    "44": ("🇬🇧", "انگلستان", 12),
    "1": ("🇺🇸", "آمریکا/کانادا", 11),
    "90": ("🇹🇷", "ترکیه", 12),
    "7": ("🇷🇺", "روسیه", 11),
    "49": ("🇩🇪", "آلمان", 13),
    "46": ("🇸🇪", "سوئد", 12),
    "61": ("🇦🇺", "استرالیا", 11),
    "994": ("🇦🇿", "آذربایجان", 12),
    "973": ("🇧🇭", "بحرین", 11),
    "974": ("🇶🇦", "قطر", 11),
    "965": ("🇰🇼", "کویت", 11),
    "966": ("🇸🇦", "عربستان", 12),
    "968": ("🇴🇲", "عمان", 11),
    "962": ("🇯🇴", "اردن", 12),
    "961": ("🇱🇧", "لبنان", 11),
    "92": ("🇵🇰", "پاکستان", 12),
    "91": ("🇮🇳", "هند", 12),
    "93": ("🇦🇫", "افغانستان", 12),
    "993": ("🇹🇲", "ترکمنستان", 11),
    "996": ("🇰🇬", "قرقیزستان", 12),
    "998": ("🇺🇿", "ازبکستان", 12),
    "380": ("🇺🇦", "اوکراین", 12),
    "31": ("🇳🇱", "هلند", 11),
    "33": ("🇫🇷", "فرانسه", 11),
    "41": ("🇨🇭", "سوئیس", 11),
    "43": ("🇦🇹", "اتریش", 12),
    "39": ("🇮🇹", "ایتالیا", 12),
    "34": ("🇪🇸", "اسپانیا", 11),
    "47": ("🇳🇴", "نروژ", 10),
    "45": ("🇩🇰", "دانمارک", 10),
    "358": ("🇫🇮", "فینلاند", 12),
    "48": ("🇵🇱", "لهستان", 11),
    "20": ("🇪🇬", "مصر", 12),
    "964": ("🇮🇶", "عراق", 12),
    "967": ("🇾🇪", "یمن", 12),
    "355": ("🇦🇱", "آلبانی", 12),
    "995": ("🇬🇪", "گرجستان", 12),
    "374": ("🇦🇲", "ارمنستان", 11),
    "972": ("🇮🇱", "اسرائیل", 12),
    "852": ("🇭🇰", "هنگ‌کنگ", 11),
    "86": ("🇨🇳", "چین", 13),
    "81": ("🇯🇵", "ژاپن", 12),
    "82": ("🇰🇷", "کره", 12),
    "60": ("🇲🇾", "مالزی", 11),
    "65": ("🇸🇬", "سنگاپور", 10),
    "66": ("🇹🇭", "تایلند", 11),
}
_CC_SORTED = sorted(COUNTRY_CODES, key=len, reverse=True)

IR_LANDLINE_PREFIXES = {
    "21": "تهران", "26": "کرج", "31": "اصفهان", "41": "تبریز", "51": "مشهد",
    "13": "مازندران", "11": "مازندران غربی", "17": "گلستان", "71": "فارس",
    "61": "خوزستان", "28": "قزوین", "24": "زنجان", "35": "یزد", "54": "کرمان",
    "56": "خراسان جنوبی", "58": "خراسان شمالی", "83": "کرمانشاه", "84": "ایلام",
    "86": "مرکزی", "87": "همدان", "23": "سمنان", "25": "قم", "44": "ارومیه",
    "45": "اردبیل", "66": "لرستان", "74": "کهگیلویه", "76": "هرمزگان",
    "77": "بوشهر", "34": "کرمان(بافت)", "38": "چهارمحال",
}

RE_PHONE_LIKE = re.compile(r"\+?[\d\s\-().]{7,}")


def clean_phone_raw(raw: str) -> str:
    """خارج کردن یک شماره از میان متن؛ اگر چند شماره بود اولین را برمی‌گرداند."""
    s = to_latin_digits(raw or "")
    s = re.sub(r"[^\d+]", "", s)
    s = re.sub(r"\+(?=.*\+)", "", s)  # چند +
    return s


def split_multi_phones(raw: str) -> list[str]:
    """خط‌هایی که چند شماره دارند (با ، یا / جدا شده) را جدا می‌کند."""
    s = to_latin_digits(raw or "")
    parts = re.split(r"[،,؛;/\\|]+|\s{2,}", s)
    out = []
    for p in parts:
        p = clean_phone_raw(p)
        if len(digits_only(p)) >= 6:
            out.append(p)
    return out or ([clean_phone_raw(s)] if digits_only(s) else [])


def parse_phone(raw: str) -> dict:
    """
    شماره خام را به ساختار استاندارد تبدیل می‌کند.
    خروجی: e164 (+98912...)، local (0912... / 021...)، kind، country، operator
    """
    d = digits_only(clean_phone_raw(raw))
    res = {
        "raw": (raw or "").strip(),
        "e164": "",
        "local": "",
        "nat": "",      # فقط رقم‌های داخلی (0912... → 912...)
        "kind": "unknown",   # mobile | landline | intl | unknown
        "country": "",
        "country_name": "",
        "flag": "",
        "region": "",
        "operator": "",
        "valid": False,
    }
    if not d:
        return res
    if d.startswith("00"):
        d = d[2:]

    # --- ایران ---
    if d.startswith("98") and not d.startswith("980") and len(d) in (12, 13):
        rest = d[2:]
    elif d.startswith("0") and re.match(r"^09\d{9}$", d):
        rest = d[1:]
    elif re.match(r"^9\d{9}$", d):
        rest = d
    elif re.match(r"^0(2\d|3\d|4\d|5\d|6\d|7\d|8\d)\d{7,8}$", d):
        rest = d[1:]
    else:
        rest = None

    if rest is not None:
        res["country"] = "98"
        res["country_name"] = "ایران"
        res["flag"] = "🇮🇷"
        if re.match(r"^9\d{9}$", rest):  # موبایل
            res.update(
                e164="+98" + rest, local="0" + rest, nat=rest,
                kind="mobile", valid=True, operator=_ir_operator(rest),
            )
        elif re.match(r"^(21|26|31|41|51|[1-8]\d?)\d{7}$", rest) or len(rest) in (9, 10):
            res.update(
                e164="+98" + rest, local="0" + rest, nat=rest,
                kind="landline", valid=len(rest) >= 9,
                region=_ir_region(rest),
            )
        else:
            res.update(e164="+98" + rest, local="0" + rest, nat=rest, kind="unknown")
        return res

    # --- بین‌المللی ---
    for cc in _CC_SORTED:
        if d.startswith(cc) and len(d) - len(cc) >= 6:
            flag, name, _ = COUNTRY_CODES[cc]
            res.update(
                country=cc, country_name=name, flag=flag,
                e164="+" + d, local="+" + d, nat=d[len(cc):],
                kind="intl", valid=True,
            )
            return res

    res.update(e164="+" + d, local=d, nat=d, kind="unknown")
    return res


def _ir_operator(nat: str) -> str:
    """حدس اپراتور از پیش‌شماره موبایل."""
    n = ("0" + nat)[:4]
    if n.startswith(("0910", "0911", "0912", "0913", "0914", "0915", "0916", "0917", "0918", "0919",
                     "0990", "0991", "0992", "0993", "0994", "0995", "0996")):
        return "همراه اول"
    if n.startswith(("0930", "0933", "0935", "0936", "0937", "0938", "0939",
                     "0901", "0902", "0903", "0904", "0905", "0941", "0998", "0999")):
        return "ایرانسل"
    if n.startswith(("0920", "0921", "0922", "0923")):
        return "رایتل"
    if n.startswith(("0931", "0932", "0934", "0940", "0942", "0943", "0944", "0945", "0946", "0947", "0948", "0949")):
        return "شاتل/سایر"
    return ""


def _ir_region(nat: str) -> str:
    for pref in ("21", "26", "31", "41", "51", "86", "13", "11", "17", "71", "61", "28", "24", "35", "54", "56", "58", "83", "84", "87", "23", "25", "44", "45", "66", "74", "76", "77", "34", "38"):
        if nat.startswith(pref):
            return IR_LANDLINE_PREFIXES.get(pref, "")
    return ""


def phone_variants(raw: str) -> set[str]:
    """همهٔ شکل‌هایی که کاربر ممکن است تایپ کند (برای ایندکس جست‌وجو)."""
    p = parse_phone(raw)
    out = set()
    for v in (p["e164"], p["local"], p["nat"], digits_only(raw)):
        if v:
            out.add(v.replace("+", ""))
    if p["kind"] == "mobile":
        out.add("98" + p["nat"])
        out.add("0" + p["nat"])
    if p["kind"] == "landline":
        out.add("98" + p["nat"])
        out.add("0" + p["nat"])
    return {v for v in out if v}
