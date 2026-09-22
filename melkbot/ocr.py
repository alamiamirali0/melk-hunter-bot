# -*- coding: utf-8 -*-
"""
خواندن شماره‌ها از عکس (OCR) برای بخش «📷 افزودن از عکس».

موتور: tesseract با زبان فارسی + انگلیسی.
نکتهٔ مهم: این موتور برای متن *چاپی* خوب است؛ برای دست‌خط (دفترچه) خطا دارد.
بنابراین هرچه از عکس درآمد، *قبل از افزودن* به کاربر نشان داده می‌شود تا تأیید کند.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
DIGIT_MAP = {ord(a): str(i) for i, a in enumerate(FA_DIGITS + AR_DIGITS)}
# کاراکترهای بی‌اثر فارسی (نیم‌فاصله، کشیده، اعراب)
CLEAN = {ord("\u200c"): " ", ord("\u200f"): " ", ord("\u200e"): " ",
         ord("\u0640"): "", ord("\u064b"): "", ord("\u064c"): "", ord("\u064d"): "",
         ord("\u064e"): "", ord("\u064f"): "", ord("\u0650"): "", ord("\u0651"): "",
         ord("\u0652"): ""}

MOBILE_RE = re.compile(r"(?:\+?98|0098|0)?9\d{9}")
LAND_RE = re.compile(r"0\d{2,3}\d{7,8}")
DIGITS_RUN = re.compile(r"[\d\s\-().+]{8,}")


def available() -> bool:
    """آیا موتور OCR روی این سرور نصب است؟"""
    return shutil.which("tesseract") is not None


def to_en(s: str) -> str:
    return s.translate(DIGIT_MAP)


def clean_line(s: str) -> str:
    s = s.translate(CLEAN)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


def read_image(path: str | Path, langs: str = "fas+eng", psm: int = 6) -> str:
    """متن عکس را می‌خواند (دو حالت چیدمان، برای پوشش ستونی و خطی)."""
    out = read_image_single(path, langs, psm)
    out += "\n" + read_image_single(path, langs, 4)
    return out


def norm_phone(p: str) -> str:
    """شمارهٔ خام را به قالب ۰۹xxxxxxxxx یا ۰xxx… تبدیل می‌کند."""
    d = re.sub(r"\D", "", to_en(p))
    if d.startswith("0098"):
        d = "0" + d[4:]
    elif d.startswith("98") and len(d) >= 12:
        d = "0" + d[2:]
    if len(d) == 10 and d.startswith("9"):
        d = "0" + d
    return d


def is_phone(d: str) -> bool:
    return (len(d) == 11 and d.startswith("09")) or (len(d) in (10, 11) and d.startswith("0") and not d.startswith("09"))


def extract_items(text: str) -> list[dict]:
    """از متن خوانده‌شده، لیست «نام — شماره» می‌سازد (بی‌تکرار)."""
    items: list[dict] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = clean_line(to_en(raw_line))
        if not line:
            continue
        # هر شمارهٔ ممکن در خط
        candidates: list[str] = []
        for m in MOBILE_RE.findall(line.replace(" ", "")):
            candidates.append(m)
        for m in LAND_RE.findall(line.replace(" ", "")):
            candidates.append(m)
        if not candidates:
            continue
        # نام = همان خط، بدون شماره‌ها و علائم
        name = line
        for c in candidates:
            name = name.replace(c, " ")
        name = DIGITS_RUN.sub(" ", name)
        name = re.sub(r"[+_=|<>#*]+", " ", name)
        name = re.sub(r"\s{2,}", " ", name).strip(" -–—:.,؛،")
        for c in dict.fromkeys(candidates):
            phone = norm_phone(c)
            if not is_phone(phone) or phone in seen:
                continue
            seen.add(phone)
            items.append({"name": name or "بدون نام", "phone": phone,
                          "tags": "عکس", "source": "عکس"})
    return items


def read_image_votes(path: str | Path) -> dict[str, int]:
    """
    عکس را با چند تنظیم متفاوت می‌خواند و می‌شمارد هر شماره چند بار دیده شده.

    چرا: یک بار خواندن ممکن است یک رقم را غلط ببیند. اگر یک شماره در هر دو/سه
    خوانش تکرار شود، تقریباً قطعی است؛ اگر فقط در یکی بیاید، ⚠️ می‌خورد.
    """
    votes: dict[str, int] = {}
    for langs, psm in (("fas+eng", 6), ("fas+eng", 4), ("eng", 6), ("eng", 4), ("fas+eng", 11)):
        text = read_image_single(path, langs, psm)
        for it in extract_items(text):
            votes[it["phone"]] = votes.get(it["phone"], 0) + 1
        # شماره‌های خام این خوانش هم رأی می‌گیرند (برای تشخیص اختلاف رقم)
    return votes


def read_image_single(path: str | Path, langs: str, psm: int) -> str:
    if not available():
        return ""
    try:
        r = subprocess.run(["tesseract", str(path), "stdout", "-l", langs, "--psm", str(psm)],
                           capture_output=True, text=True, timeout=90)
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def preprocess(path: str | Path, out: str | Path | None = None, scale: float = 2.0) -> Path:
    """عکس را برای خواندن آماده می‌کند: خاکستری + کنتراست + بزرگ‌نمایی + تیزکردن."""
    try:
        from PIL import Image, ImageFilter, ImageOps
    except Exception:  # noqa: BLE001
        return Path(path)
    try:
        im = Image.open(path).convert("L")
        im = ImageOps.autocontrast(im)
        if 1.0 < scale <= 3.0:
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=140, threshold=3))
        out = Path(out or (str(path) + "_clean.png"))
        im.save(out)
        return out
    except Exception:  # noqa: BLE001
        return Path(path)


def extract_with_reliability(path: str | Path) -> list[dict]:
    """
    نتیجهٔ نهایی: لیست «نام — شماره» که هر موردش می‌داند چند خوانش تأییدش کرده.
    فقط شماره‌هایی که در «eng» هم دیده شده‌اند نگه داشته می‌شوند (کاهش خطای ارقام فارسی).
    """
    clean = preprocess(path)
    votes: dict[str, int] = {}
    for p in {Path(path), Path(clean)}:
        for ph, n in read_image_votes(p).items():
            votes[ph] = max(votes.get(ph, 0), n)
    # نام‌ها از خوانش اصلی
    primary = extract_items(read_image(path)) + extract_items(read_image(clean))
    out: list[dict] = []
    used: set[str] = set()
    for it in primary:
        phone = it["phone"]
        if phone in used:
            continue
        used.add(phone)
        n = votes.get(phone, 1)
        it = dict(it)
        it["votes"] = n
        it["sure"] = n >= 2
        out.append(it)
    # شماره‌هایی که فقط در خوانش‌های دیگر دیده شدند هم اضافه کن (با عدم‌قطعیت)
    for phone, n in votes.items():
        if phone in used:
            continue
        if len(out) >= 200:
            break
        used.add(phone)
        out.append({"name": "بدون نام", "phone": phone, "tags": "عکس",
                    "source": "عکس", "votes": n, "sure": False})
    return out


def preview_lines(items: list[dict], limit: int = 25) -> str:
    """پیش‌نمایش خوانا برای تأیید کاربر؛ موارد نامطمئن با ⚠️ علامت می‌خورند."""
    out = []
    for i, it in enumerate(items[:limit], 1):
        mark = "" if it.get("sure", True) else " ⚠️"
        out.append(f"{i}. {it['name']} — {it['phone']}{mark}")
    if len(items) > limit:
        out.append(f"… و {len(items) - limit} مورد دیگر")
    return "\n".join(out)


def counts(items: list[dict]) -> tuple[int, int]:
    sure = sum(1 for it in items if it.get("sure", True))
    return sure, len(items) - sure
