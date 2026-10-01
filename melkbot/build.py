# -*- coding: utf-8 -*-
"""
ساخت ایندکس جست‌وجو از فایل متنی مخاطبین.

ورودی : data/raw.txt  (لیست «شماره. نام — تلفن»)
خروجی : data/contacts.json + data/meta.json
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from melkbot.normalize import (  # noqa: E402
    compact, digits_only, normalize_text, parse_phone, split_multi_phones,
    to_latin_digits, tokenize,
)

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / "data" / "raw.txt"
OUT = BASE / "data" / "contacts.json"
META = BASE / "data" / "meta.json"

# ---------------------------------------------------------------- برچسب‌ها --
# (برچسب، الگو)  — روی متن نرمال‌شده اعمال می‌شود
TAG_RULES: list[tuple[str, str]] = [
    ("کلنگی", r"کلنگ|کلنک"),
    ("دیوار", r"\bدیوار\b|ديوار|agahi|اگهی دیوار"),
    ("خریدار", r"خريدار|خریدار|خريد\b|خرید\b|خریداریم"),
    ("فروشنده", r"فروشنده|فروش\b|ميفروش|میفروش"),
    ("مشاور املاک", r"املاك|املاک|مشاور|بنگاه|آژانس املاک|مشاورین"),
    ("مشارکت", r"مشارکت|مشاركت|سازنده|بساز|بساز بفروش"),
    ("مستاجر", r"مستاجر|مستأجر|مستاجرى"),
    ("مالک", r"\bمالك\b|\bمالک\b|مالکیت|صاحب ملک"),
    ("اجاره", r"اجاره|رهن"),
    ("آپارتمان", r"آپارتمان|اپارتمان|واحد مسکونی"),
    ("مغازه", r"مغازه|تجارى|تجاری|ويتارا|ویترا|ويتريم|ویترین"),
    ("دفتر", r"\bدفتر\b|دفترکار|دفتر كار"),
    ("زمین", r"\bزمين\b|\bزمین\b|مترى زمين|متری زمین"),
    ("ساختمان", r"ساختمان|ساختمون|برج|مجتمع"),
    ("کارخانه", r"كارخانه|کارخانه|کارگاه|انبار|سوله"),
    ("سرایدار", r"سرايدار|سرایدار|نگهبان"),
    ("همکار", r"همكار|همکار|دوست"),
    ("خارج از کشور", r"دبى|دبی|امارات|انگليس|انگلیس|امريكا|امریکا|كانادا|کانادا|ترك|ترک|آلمان"),
    ("آژانس مسافرتی", r"آژانس هوايى|آژانس هوایی|مسافرتى|مسافرتی|پرواز|تور"),
    ("پزشک", r"دكتر|دکتر|پزشك|پزشک|دندان|مطب|كلينيك|کلینیک|بيمارستان|بیمارستان"),
    ("وکیل/حقوقی", r"وكيل|وکیل|دادگاه|حقوقى|حقوقی|اسناد رسمى|اسناد رسمی|دفترخانه"),
    ("بانک/بیمه", r"بانك|بانک|بيمه|بیمه"),
    ("خدمات ساختمانی", r"بنّا|بنا\b|لوله ?كش|لوله ?کش|برقكار|برقکار|نقاش|كاشى|کاشی|دوربين|دوربین|تعمير|تعمیر|تاسيسات|تأسیسات|جوشكار|جوشکار|آسانسور|نما\b"),
    ("صنایع دستی/پوشاک", r"پوشاك|پوشاک|فرش|قالى|قالی|آخاله|اخاله|بوتیک|بوتیك"),
    ("رستوران/کافه", r"رستوران|كافه|کافه|سفره خانه|فست فود|قهوه"),
    ("آموزش", r"آموزش|مدرسه|دانشگاه|كلاس|کلاس|استاد|آموزشگاه"),
    ("پلاک/سند", r"پلاك|پلاک|سند|قرارداد|شهردارى|شهرداری|ادارى|اداری"),
    ("متراژ بالا", r"\b[1-9]\d{3}\s*متر|\b[1-9]\d{3}\s*متری"),
    ("دو نبش", r"دو ?نبش|نبش"),
    ("ارم/نوساز", r"نوساز|ارم\b|كلید نخورده|کلید نخورده"),
    ("حیاط/باغ", r"باغ|باغچه|حیاط|ویلا"),
    ("فضای سبز/دریا", r"دریا|ساحل|جنگل"),
]

_TAG_RE = [(t, re.compile(p, re.IGNORECASE)) for t, p in TAG_RULES]

_AREA_PATTERNS = [
    re.compile(r"(\d[\d,٬٫.]*)\s*متر\b"),
    re.compile(r"(\d[\d,٬٫.]*)\s*متری"),
    re.compile(r"متری\s*(\d[\d,٬٫.]*)"),
    re.compile(r"متر\s*(\d[\d,٬٫.]*)"),
    re.compile(r"(\d[\d,٬٫.]*)\s*م\b"),
]
_PLAK_RE = re.compile(r"پلاک\s*(\d+)|پلاك\s*(\d+)")
_FLOOR_RE = re.compile(r"ط(?:بقه)?\s*(\d+)|طبقهٔ?\s*(\d+)|(\d+)\s*(?:طبقه|طبقهٔ)")
_BILLION_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:میلیارد|میلیون|ميليارد|ميليون)")
_AREA_DIGIT = re.compile(r"^0?\d{1,4}$")


def extract_tags(text_norm: str, phone: dict, name_norm: str) -> list[str]:
    tags = []
    for tag, rx in _TAG_RE:
        if rx.search(text_norm):
            tags.append(tag)
    if phone["kind"] == "intl":
        if "خارج از کشور" not in tags:
            tags.append("خارج از کشور")
        if phone["country_name"] and phone["country_name"] != "ایران":
            tags.append(phone["country_name"])
    if phone["kind"] == "mobile":
        tags.append("موبایل")
    elif phone["kind"] == "landline":
        tags.append("تلفن ثابت")
        if phone.get("region"):
            tags.append(phone["region"])
    if phone.get("operator"):
        tags.append(phone["operator"])
    # برچسب از خود نام
    if re.search(r"ديوار|دیوار", text_norm) and re.search(r"٩٩|99", to_latin_digits(text_norm)):
        tags.append("دیوار ۹۹")
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def extract_features(text_raw: str) -> dict:
    """متراژ، پلاک، طبقه، مبلغ."""
    t = to_latin_digits(text_raw)
    t = normalize_text(t)
    area = None
    for rx in _AREA_PATTERNS:
        m = rx.search(t)
        if m:
            g = next((x for x in m.groups() if x), None)
            if g:
                g = re.sub(r"[,٬٫.]", "", g)
                try:
                    v = int(g)
                except ValueError:
                    continue
                # فیلتر اعداد بی‌ربط (سال، شماره تلفن)
                if 5 <= v <= 200000:
                    area = v
                    break
    plak = None
    m = _PLAK_RE.search(t)
    if m:
        plak = next((x for x in m.groups() if x), None)
    floor = None
    m = _FLOOR_RE.search(t)
    if m:
        floor = next((x for x in m.groups() if x), None)
    amount = None
    m = _BILLION_RE.search(to_latin_digits(text_raw).replace(" ", ""))
    if m:
        amount = m.group(0)
    return {"area": area, "plak": plak, "floor": floor, "amount": amount}


def parse_raw(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    records = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(\d+)[.)]\s*(.+?)\s*[—–-]{1,2}\s*(.+)$", line)
        if not m:
            continue
        idx, name_raw, phone_raw = m.group(1), m.group(2).strip(), m.group(3).strip()
        records.append({"src": int(idx), "name_raw": name_raw, "phone_raw": phone_raw})
    return records


def build() -> dict:
    if not RAW.exists():
        raise SystemExit(f"فایل ورودی پیدا نشد: {RAW}")
    rows = parse_raw(RAW)
    contacts: list[dict] = []
    cid = 0

    for r in rows:
        phones = split_multi_phones(r["phone_raw"])
        if not phones:
            phones = [r["phone_raw"]]
        name_raw = re.sub(r"\s{2,}", " ", r["name_raw"]).strip()
        for ph_raw in phones:
            phone = parse_phone(ph_raw)
            name_norm = normalize_text(name_raw)
            blob_raw = f"{name_raw} {ph_raw}"
            blob = normalize_text(blob_raw)
            feats = extract_features(blob_raw)
            tags = extract_tags(blob, phone, name_norm)
            cid += 1
            contacts.append({
                "id": cid,
                "src": r["src"],
                "name": name_raw,
                "name_norm": name_norm,
                "name_compact": compact(name_raw),
                "tokens": sorted(set(tokenize(name_raw))),
                "phone_raw": ph_raw.strip(),
                "phone": phone["e164"],
                "phone_local": phone["local"],
                "phone_digits": digits_only(phone["e164"]) or digits_only(ph_raw),
                "kind": phone["kind"],
                "country": phone["country"],
                "country_name": phone["country_name"],
                "flag": phone["flag"],
                "region": phone.get("region", ""),
                "operator": phone.get("operator", ""),
                "valid": bool(phone["valid"]),
                "tags": tags,
                "blob": blob,
                "blob_compact": compact(blob_raw),
                "area": feats["area"],
                "plak": feats["plak"],
                "floor": feats["floor"],
                "amount": feats["amount"],
                "dup_group": "",
                "is_dup": False,
            })

    # ---- تشخیص تکراری‌ها بر اساس شماره استاندارد ----
    by_phone: dict[str, list[int]] = {}
    for c in contacts:
        by_phone.setdefault(c["phone_digits"], []).append(c["id"])
    dup_groups = 0
    for ph, ids in by_phone.items():
        if len(ids) > 1:
            dup_groups += 1
            for i in ids:
                contacts[i - 1]["dup_group"] = ph
                contacts[i - 1]["is_dup"] = True

    # ---- آمار ----
    from collections import Counter as _C
    src_counts = _C(c["src"] for c in contacts if c["src"])
    multi_phone = sum(1 for _, v in src_counts.items() if v > 1)
    all_tags = Counter(t for c in contacts for t in c["tags"])
    meta = {
        "source": RAW.name,
        "total": len(contacts),
        "unique_phones": len(by_phone),
        "duplicate_contacts": sum(1 for c in contacts if c["is_dup"]),
        "duplicate_groups": dup_groups,
        "kinds": dict(Counter(c["kind"] for c in contacts)),
        "countries": dict(Counter(c["country_name"] or "نامشخص" for c in contacts)),
        "operators": dict(Counter(c["operator"] for c in contacts if c["operator"])),
        "tags": dict(all_tags),
        "with_area": sum(1 for c in contacts if c["area"]),
        "multi_phone": multi_phone,
        "invalid": sum(1 for c in contacts if not c["valid"]),
    }

    OUT.write_text(json.dumps(contacts, ensure_ascii=False), encoding="utf-8")
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


if __name__ == "__main__":
    m = build()
    print(json.dumps(m, ensure_ascii=False, indent=2)[:2500])
    print(f"\n✅ {m['total']} مخاطب ساخته شد → {OUT}")
