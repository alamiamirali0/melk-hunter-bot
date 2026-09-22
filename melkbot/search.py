# -*- coding: utf-8 -*-
"""
موتور جست‌وجوی هوشمند مخاطبین.

قابلیت‌ها:
- جست‌وجوی نام با غلط‌گیری املایی (فازی) و پیشوند/پسوند
- جست‌وجوی شماره به هر شکلی: 0912…، 98912…، +98912…، 912…، و حتی بخشی از شماره
- عملگرها:  #برچسب   -حذف   "عبارت دقیق"   متر:200-400   نوع:موبایل   تکراری:بله
- امتیازدهی و رتبه‌بندی، حالت‌های جست‌وجو، پیشنهاد اصلاح، هایلایت نتایج
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from melkbot.normalize import (  # noqa: E402
    compact, digits_only, normalize_text, to_latin_digits, tokenize,
)

TAG_MATCH_MIN = 0.70
GOOD = 0.74

MODE_LABELS = {
    "smart": "🎯 هوشمند",
    "name": "👤 فقط نام",
    "desc": "📝 نام + توضیحات",
    "phone": "🔢 فقط شماره",
    "tag": "🏷 فقط برچسب",
}

FILTER_KINDS = {
    "all": "همه",
    "mobile": "📱 موبایل",
    "landline": "☎️ ثابت",
    "intl": "🌍 بین‌المللی",
}

# ------------------------------------------------------------- عملگرها ------
RE_OP_AREA = re.compile(r"^(?:متر|متراژ|m2)\s*[:=]\s*(.+)$", re.IGNORECASE)
RE_OP_KIND = re.compile(r"^(?:نوع|kind)\s*[:=]\s*(.+)$", re.IGNORECASE)
RE_OP_DUP = re.compile(r"^(?:تکراری|dup)\s*[:=]\s*(.+)$", re.IGNORECASE)
RE_OP_TAG = re.compile(r"^(?:tag|برچسب)\s*[:=]\s*(.+)$", re.IGNORECASE)
RE_RANGE = re.compile(r"^(>=|<=|>|<)?\s*(\d+)(?:\s*[-–تا]\s*(\d+))?$")
RE_QUOTED = re.compile(r'"([^"]+)"|«([^»]+)»')
RE_PHONEISH = re.compile(r"^[+0-9\s\-()]{4,}$")


@dataclass
class ParsedQuery:
    raw: str = ""
    text_tokens: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    tag_exclude: list[str] = field(default_factory=list)
    digits: str = ""
    optional_digits: list[str] = field(default_factory=list)
    area_min: int | None = None
    area_max: int | None = None
    kinds: set[str] = field(default_factory=set)
    dup: str | None = None      # بله | خیر | None
    used_operators: list[str] = field(default_factory=list)


# «۳۰۰ متر» / «متری ۳۰۰» / «متر ۳۰۰» / «۳۰۰متری» را به فیلتر متراژ تبدیل می‌کند
RE_AREA_INLINE = re.compile(
    r"(?:(\d[\d,٬٫.]*)\s*(?:متری|متره|متر)\b|(?:متری|متر)\s*(\d[\d,٬٫.]*))",
)

RE_PURE_DIGITS = re.compile(r"^\+?[\d\s\-().]{2,}$")


def _area_pair(a: int, b: int | None) -> tuple[int, int]:
    if b:
        return min(a, b), max(a, b)
    return max(1, int(a * 0.94)), int(a * 1.06)


def parse_query(raw: str, catalogs: dict | None = None) -> ParsedQuery:
    """تجزیهٔ پرس‌وجو به عملگرها + توکن‌های متنی."""
    catalogs = catalogs or {}
    pq = ParsedQuery(raw=(raw or "").strip())
    s = to_latin_digits(raw or "")

    # --- عبارات داخل گیومه ---
    for m in RE_QUOTED.finditer(s):
        ph = m.group(1) or m.group(2) or ""
        if ph.strip():
            pq.phrases.append(normalize_text(ph))
    s = RE_QUOTED.sub(" ", s)

    # --- متراژ خودکار: «کلنگی ۳۰۰ متر» / «آپارتمان ۲۰۰متری» ---
    def _area_repl(m: re.Match) -> str:
        g = m.group(1) or m.group(2)
        if not g:
            return " "
        try:
            v = int(re.sub(r"[,٬٫.]", "", g))
        except ValueError:
            return " "
        if 5 <= v <= 200000 and ("متر" in m.group(0)):
            lo, hi = _area_pair(v, None)
            pq.area_min = lo if pq.area_min is None else max(pq.area_min, lo)
            pq.area_max = hi if pq.area_max is None else min(pq.area_max, hi)
            if "متراژ" not in pq.used_operators:
                pq.used_operators.append("متراژ")
            return " "
        return " "

    s = RE_AREA_INLINE.sub(_area_repl, s)

    digit_buf = ""
    for tok in re.split(r"[\s|،,]+", s.strip()):
        if not tok:
            continue
        t_norm = normalize_text(tok)
        if not t_norm:
            continue
        low = t_norm.lower()

        # ---- برچسب: #تگ یا tag:تگ یا #-تگ ----
        if tok.startswith("#") or RE_OP_TAG.match(tok):
            v = tok[1:] if tok.startswith("#") else RE_OP_TAG.sub(r"\1", tok)
            neg = v.startswith("-") or v.startswith("!")
            v = normalize_text(v.lstrip("-!"))
            if v:
                (pq.tag_exclude if neg else pq.tags).append(v)
                if "برچسب" not in pq.used_operators:
                    pq.used_operators.append("برچسب")
            continue

        # ---- حذف: -کلمه ----
        if tok.startswith("-") or tok.startswith("!"):
            v = normalize_text(tok[1:])
            if v:
                pq.exclude.append(v)
                if "حذف" not in pq.used_operators:
                    pq.used_operators.append("حذف")
            continue

        # ---- عملگر متراژ: متر:1000-2000 ----
        m = RE_OP_AREA.match(tok)
        if m:
            r = RE_RANGE.match(to_latin_digits(m.group(1)).replace(" ", ""))
            if r:
                op, a, b = r.groups()
                a = int(a)
                if b:
                    pq.area_min, pq.area_max = _area_pair(a, int(b))
                elif op == ">":
                    pq.area_min = a + 1
                elif op == ">=":
                    pq.area_min = a
                elif op == "<":
                    pq.area_max = a - 1
                elif op == "<=":
                    pq.area_max = a
                else:
                    pq.area_min, pq.area_max = _area_pair(a, None)
                if "متراژ" not in pq.used_operators:
                    pq.used_operators.append("متراژ")
            continue

        m = RE_OP_KIND.match(tok)
        if m:
            kind = _match_from_catalog(normalize_text(m.group(1)), FILTER_KINDS)
            if kind and kind != "all":
                pq.kinds.add(kind)
                if "نوع" not in pq.used_operators:
                    pq.used_operators.append("نوع")
            continue

        m = RE_OP_DUP.match(tok)
        if m:
            v = normalize_text(m.group(1))
            if v in {"بله", "بلي", "1", "yes", "y", "on", "اره", "آره"}:
                pq.dup = "yes"
            elif v in {"خیر", "نه", "0", "no", "n", "off", "نیست"}:
                pq.dup = "no"
            if "تکراری" not in pq.used_operators:
                pq.used_operators.append("تکراری")
            continue

        # ---- اعداد خالص: شماره یا بخشی از نام ----
        if RE_PURE_DIGITS.match(tok):
            d = digits_only(tok)
            if not d:
                continue
            looks_phone = (
                len(d) >= 7
                or (len(d) >= 4 and (tok.startswith("0") or tok.startswith("+") or bool(digit_buf)))
                or (not digit_buf and len(d) >= 4 and d.startswith(("9", "98", "0")))
            )
            if digit_buf:                     # ادامهٔ شماره: «0912 477 541»
                digit_buf += d
            elif looks_phone:
                digit_buf = d
            else:
                pq.optional_digits.append(d)
            continue

        pq.text_tokens.append(t_norm)

    pq.digits = digit_buf
    # اگر عبارت متنی باقی نمانده و رقم اختیاری داریم، آن‌ها را جدی بگیر
    if not pq.text_tokens and pq.optional_digits and not pq.digits and not pq.tags:
        pq.digits = "".join(pq.optional_digits)
        pq.optional_digits = []
    return pq


def _match_from_catalog(value: str, catalog: dict) -> str | None:
    v = normalize_text(value)
    for key, label in catalog.items():
        lab = normalize_text(label)
        if v == key or v == lab or v in lab or lab in v:
            return key
    return None


# ---------------------------------------------------------------- امتیاز ----
def token_score(q: str, t: str) -> float:
    """امتیاز مطابقت یک توکن پرس‌وجو با یک توکن مخاطب (۰ تا ۱)."""
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    lq, lt = len(q), len(t)
    if lq >= 2 and t.startswith(q):
        return 0.95
    if lq >= 3 and q in t:
        return 0.90
    if lq >= 4 and lt >= 3 and t in q:
        return 0.86
    if lq >= 3 and lt >= 3:
        if abs(lq - lt) <= max(2, lq // 2):
            r = fuzz.ratio(q, t)
            if r >= 85:
                return 0.58 + (r - 85) / 15 * 0.30      # 0.58 → 0.88
        if lt >= lq:
            r = fuzz.partial_ratio(q, t)
            if r >= 90:
                return 0.55 + (r - 90) / 10 * 0.28      # 0.55 → 0.83
    return 0.0


def _phrase_hit(phrase: str, blob_compact: str) -> bool:
    p = phrase.replace(" ", "")
    return bool(p) and p in blob_compact


# نگاشت نویسه‌های هم‌ارز برای هایلایت (بدون تغییر متن اصلی)
_VARIANTS = {
    "ی": "یيىﻯﻰ", "ک": "کكﻙڪ", "ه": "هةۀ", "ا": "اآأإٱ",
    "و": "وؤ", "0": "0۰٠", "1": "1۱١", "2": "2۲٢", "3": "3۳٣", "4": "4۴٤",
    "5": "5۵٥", "6": "6۶٦", "7": "7۷٧", "8": "8۸٨", "9": "9۹٩",
}
_RE_SPECIAL = set(".^$*+?{}[]\\|()")


def _term_pattern(term: str) -> str:
    """یک عبارت را به الگوی regex مقاوم به تفاوت‌های نگارشی فارسی تبدیل می‌کند."""
    out = []
    for ch in term:
        if ch.isspace():
            out.append(r"\s*")
        elif ch in _VARIANTS:
            out.append("[%s]" % _VARIANTS[ch])
        elif ch in _RE_SPECIAL:
            out.append("\\" + ch)
        else:
            out.append(re.escape(ch))
    return "".join(out)


def highlight(text: str, tokens: list[str]) -> str:
    """هایلایت توکن‌های منطبق با <b> (خروجی HTML امن)."""
    esc = html.escape(text or "")
    if not tokens:
        return esc
    spans: list[tuple[int, int]] = []
    for tok in sorted({normalize_text(t) for t in tokens}, key=len, reverse=True):
        tok = tok.strip()
        if len(tok) < 2:
            continue
        try:
            rx = re.compile(_term_pattern(tok), re.IGNORECASE | re.UNICODE)
        except re.error:
            continue
        for m in rx.finditer(esc):
            if m.end() > m.start():
                spans.append((m.start(), m.end()))
    if not spans:
        return esc
    spans.sort()
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, last = [], 0
    for a, b in merged:
        out.append(esc[last:a])
        out.append("<b>" + esc[a:b] + "</b>")
        last = b
    out.append(esc[last:])
    return "".join(out)


def phone_match_score(q_digits: str, c_digits: str, c_nat: str = "", fuzzy: bool = False) -> float:
    """امتیاز مطابقت شمارهٔ تایپ‌شده با مخاطب (پشتیبانی از شمارهٔ ناقص و صفر ابتدایی)."""
    if not q_digits or not c_digits:
        return 0.0
    if q_digits == c_digits:
        return 1.0
    if c_nat and q_digits.startswith("0") and c_nat.startswith(q_digits[1:]):
        return 0.95
    if c_nat and q_digits.startswith("98") and c_nat.startswith(q_digits[2:]):
        return 0.95
    if c_nat and c_nat.startswith(q_digits):
        return 0.93
    if c_digits.startswith(q_digits):
        return 0.91
    if c_nat and c_nat.endswith(q_digits):
        return 0.88
    if q_digits in c_digits:
        return 0.86
    if fuzzy and len(q_digits) >= 7:
        qn = q_digits[1:] if q_digits.startswith("0") else q_digits
        for cand in (c_nat, c_digits):
            if not cand:
                continue
            if abs(len(cand) - len(qn)) <= 2:
                r = fuzz.ratio(qn, cand)
                if r >= 88:
                    return 0.6 + min(0.25, (r - 88) / 12 * 0.25)
    return 0.0


# ------------------------------------------------------------- موتور ---------
class SearchEngine:
    def __init__(self, contacts: list[dict], meta: dict | None = None):
        self.contacts = contacts
        self.meta = meta or {}
        self.by_id = {c["id"]: c for c in contacts}
        self.tag_counts: dict[str, int] = {}
        for c in contacts:
            for t in c["tags"]:
                self.tag_counts[t] = self.tag_counts.get(t, 0) + 1
        self._prep()

    # ------------------------------------------------------------- setup ----
    def _prep(self) -> None:
        for c in self.contacts:
            keys = set(c.get("tokens") or [])
            keys |= set(tokenize(c.get("name", "")))
            c["_keys"] = list(keys)
            c["_key_tuples"] = [(k, len(k)) for k in c["_keys"]]
            c["_tags_norm"] = [normalize_text(t) for t in c.get("tags", [])]
            c["_blob_compact"] = c.get("blob_compact") or compact(c.get("blob", ""))
            c["_name_compact"] = c.get("name_compact") or compact(c.get("name", ""))
            c["_digits"] = c.get("phone_digits") or ""
            p = c.get("phone_local") or ""
            nat = re.sub(r"\D", "", p)
            if nat.startswith("98"):
                nat = nat[2:]
            elif nat.startswith("0"):
                nat = nat[1:]
            c["_nat"] = nat

    # ------------------------------------------------------------ search ----
    def search(self, query: str, *, mode: str = "smart", limit: int = 20,
               offset: int = 0, kinds: set[str] | None = None,
               tags: set[str] | None = None, include_dup: bool = True,
               only_dup: bool = False, country: str | None = None,
               min_valid: bool = False, fuzzy_phone: bool = False,
               parsed=None) -> dict:
        pq = parsed if parsed is not None else parse_query(query, {"kinds": FILTER_KINDS})
        if mode == "phone" and not pq.digits:
            pq.digits = digits_only(query)
            pq.text_tokens = []
        if mode == "name":
            pq.digits = ""
        if mode == "tag":
            if not pq.tags:
                pq.tags = [normalize_text(query)]
            pq.text_tokens = []

        active_kinds = set(pq.kinds)
        if kinds:
            active_kinds |= {k for k in kinds if k != "all"}
        active_tags = set(pq.tags) | {normalize_text(t) for t in (tags or set())}
        if pq.dup == "yes":
            only_dup = True
        elif pq.dup == "no":
            include_dup = False
        if only_dup:
            include_dup = True

        scored: list[tuple[float, dict, list[str], str]] = []
        strict_n = 0
        for c in self.contacts:
            if active_kinds and c["kind"] not in active_kinds:
                continue
            if country and c.get("country") != country:
                continue
            if only_dup and not c["is_dup"]:
                continue
            if not include_dup and not only_dup and c["is_dup"]:
                continue
            if min_valid and not c.get("valid"):
                continue
            if pq.area_min is not None or pq.area_max is not None:
                a = c.get("area")
                if not a:
                    continue
                if pq.area_min is not None and a < pq.area_min:
                    continue
                if pq.area_max is not None and a > pq.area_max:
                    continue
            if pq.tag_exclude:
                cn = c["_tags_norm"]
                if any(_match_from_catalog(t, {x: x for x in cn}) for t in pq.tag_exclude):
                    continue

            # --- برچسب‌ها ---
            tag_hits = 0
            if active_tags:
                cn = c["_tags_norm"] + [normalize_text(c.get("region", "")), normalize_text(c.get("operator", ""))]
                ok = True
                for want in active_tags:
                    if max((token_score(want, x) for x in cn), default=0.0) < TAG_MATCH_MIN:
                        ok = False
                        break
                if not ok:
                    continue
                tag_hits = len(active_tags)

            # --- حذف‌ها ---
            if pq.exclude:
                keys = c["_keys"]
                if any(max((token_score(x, k) for k in keys), default=0.0) >= GOOD or x in c["_blob_compact"] for x in pq.exclude):
                    continue

            # --- شماره ---
            phone_score = 0.0
            if pq.digits:
                phone_score = phone_match_score(pq.digits, c["_digits"], c.get("_nat") or "", fuzzy_phone)
                if phone_score <= 0:
                    continue

            # --- متن ---
            token_scores: list[float] = []
            matched_terms: list[str] = []
            if pq.text_tokens:
                keys = c["_keys"]
                nc = c["_name_compact"]
                for q in pq.text_tokens:
                    best, best_k = 0.0, ""
                    for k in keys:
                        s = token_score(q, k)
                        if s > best:
                            best, best_k = s, k
                    # تطبیق چندکلمه‌ای به‌هم‌چسبیده («نوبخت170» یا «آرشجنتآباد»)
                    if best < GOOD and len(q) >= 3:
                        if q.replace(" ", "") in nc:
                            best, best_k = 0.80, q
                    token_scores.append(best)
                    if best >= GOOD:
                        matched_terms.append(best_k)

            # --- ارقام اختیاری (مثل «۱۷۰» در «نوبخت ۱۷۰») ---
            opt_hits = 0
            for d in pq.optional_digits:
                if d in c["_digits"] or d in c["_name_compact"] or any(d == k for k in c["_keys"]):
                    opt_hits += 1
                elif c.get("area") and len(d) >= 2:
                    try:
                        v = int(d)
                    except ValueError:
                        continue
                    if 5 <= v <= 200000 and abs(c["area"] - v) <= max(3, v * 0.06):
                        opt_hits += 1

            phrase_ok = True
            for ph in pq.phrases:
                p = ph.replace(" ", "")
                if p and p not in c["_blob_compact"]:
                    phrase_ok = False
                    break
            if not phrase_ok:
                continue

            n_tok = len(token_scores)
            got = sum(1 for s in token_scores if s >= GOOD)

            if n_tok and got < n_tok:
                continue  # مرحلهٔ سخت‌گیرانه: همهٔ واژه‌ها
            if not n_tok and not pq.digits and not active_tags and not pq.phrases \
                    and not only_dup and not active_kinds and not country \
                    and pq.area_min is None and pq.area_max is None and not min_valid:
                continue

            # --- امتیاز نهایی ---
            if n_tok:
                strict_n += 1
                avg = sum(token_scores) / n_tok
                score = avg * 100.0
                score += 12 if got == n_tok else 0
                first = pq.text_tokens[0]
                if c["_name_compact"].startswith(first) or (
                    c["_keys"] and any(k.startswith(first) for k in c["_keys"])
                ):
                    score += 14
                if c["_name_compact"].startswith(first):
                    score += 6
                if pq.digits:
                    score += 8 * phone_score
            elif pq.digits:
                score = 55.0 + 45.0 * phone_score
            else:
                score = 50.0

            if phone_score >= 0.99 and pq.digits:
                score = max(score, 200.0)
            elif phone_score >= 0.9 and pq.digits:
                score = max(score, 120.0)

            if matched_terms:
                score += 6
            if opt_hits:
                score += 10 * opt_hits
            if tag_hits:
                score += 5 * tag_hits
            score -= min(6.0, 0.35 * max(0, len(c["_keys"]) - 1))
            score -= min(4.0, 0.4 * max(0, len(c["_name_compact"]) - 22) / 3)

            scored.append((score, c, matched_terms or pq.phrases or pq.text_tokens, ""))

        # --- اگر سخت‌گیرانه نتیجهٔ کم داد، شل بگیر ---
        relaxed_used = False
        if len(scored) < max(3, limit) and pq.text_tokens and len(pq.text_tokens) > 1:
            relaxed_used = True
            scored = self._relax(pq, active_kinds, active_tags, include_dup, only_dup, country, scored)

        if not scored and pq.digits and not fuzzy_phone:
            # شمارهٔ تایپ‌شده دقیق نبود → تطبیق تقریبی شماره
            retry = self.search(query, mode=mode, limit=limit, offset=offset, kinds=kinds,
                                tags=tags, include_dup=include_dup, only_dup=only_dup,
                                country=country, min_valid=min_valid, fuzzy_phone=True,
                                parsed=pq)
            retry["fuzzy_phone"] = True
            return retry

        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        total = len(scored)
        page = scored[offset: offset + limit]
        return {
            "query": query,
            "parsed": pq,
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": page,
            "relaxed": relaxed_used,
            "strict": strict_n,
            "fuzzy_phone": False,
        }

    # ------------------------------------------------------------- relax ----
    def _relax(self, pq, kinds, tags, include_dup, only_dup, country, already):
        seen = {id(x[1]) for x in already}
        out = list(already)
        for c in self.contacts:
            if id(c) in seen:
                continue
            if kinds and c["kind"] not in kinds:
                continue
            if only_dup and not c["is_dup"]:
                continue
            if not include_dup and not only_dup and c["is_dup"]:
                continue
            if country and c.get("country") != country:
                continue
            if pq.area_min is not None or pq.area_max is not None:
                a = c.get("area")
                if not a or (pq.area_min is not None and a < pq.area_min) or (pq.area_max is not None and a > pq.area_max):
                    continue
            if tags:
                cn = c["_tags_norm"]
                if any(max((token_score(w, x) for x in cn), default=0.0) < TAG_MATCH_MIN for w in tags):
                    continue
            keys = c["_keys"]
            scores = [max((token_score(q, k) for k in keys), default=0.0) for q in pq.text_tokens]
            got = sum(1 for s in scores if s >= GOOD)
            if got < len(scores) - 1 or got == 0:
                continue
            avg = sum(scores) / len(scores)
            score = avg * 72.0 + 20.0 * (got / len(scores))
            if pq.digits:
                ps = phone_match_score(pq.digits, c["_digits"], c.get("_nat") or "")
                if ps <= 0:
                    continue
                score += 30 * ps
            out.append((score - 12.0, c, [q for q, s in zip(pq.text_tokens, scores) if s >= GOOD], ""))
        return out

    # ------------------------------------------------------------ helpers ---
    def get(self, cid: int) -> dict | None:
        return self.by_id.get(cid)

    def similar(self, cid: int, limit: int = 12) -> list[dict]:
        """مخاطبین مشابه (برای «بیشتر شبیه این»)."""
        c = self.by_id.get(cid)
        if not c:
            return []
        tokens = [t for t in c["_keys"] if len(t) >= 3]
        out: list[tuple[float, dict]] = []
        for o in self.contacts:
            if o["id"] == cid:
                continue
            sc = 0.0
            for t in tokens:
                sc += max((token_score(t, k) for k in o["_keys"]), default=0.0)
            if sc > 0.8:
                out.append((sc, o))
        out.sort(key=lambda x: -x[0])
        return [o for _, o in out[:limit]]

    def duplicates_of(self, cid: int) -> list[dict]:
        c = self.by_id.get(cid)
        if not c or not c["dup_group"]:
            return []
        return [o for o in self.contacts if o["dup_group"] == c["dup_group"] and o["id"] != cid]

    def top_tags(self, n: int = 40) -> list[tuple[str, int]]:
        return sorted(self.tag_counts.items(), key=lambda x: -x[1])[:n]

    def suggest(self, query: str, n: int = 5) -> list[str]:
        """پیشنهاد نزدیک‌ترین نام‌ها وقتی نتیجه‌ای پیدا نشد."""
        q = normalize_text(query)
        if not q:
            return []
        out: list[tuple[float, str]] = []
        for c in self.contacts:
            r = max((token_score(q, k) for k in c["_keys"]), default=0.0)
            if r > 0.45:
                out.append((r, c["name"]))
        seen, res = set(), []
        for _, nm in sorted(out, key=lambda x: -x[0]):
            if nm not in seen:
                seen.add(nm)
                res.append(nm)
            if len(res) >= n:
                break
        return res

    def tags_matching(self, q: str, n: int = 8) -> list[str]:
        qn = normalize_text(q)
        out = []
        for tag, cnt in sorted(self.tag_counts.items(), key=lambda x: -x[1]):
            tn = normalize_text(tag)
            if qn and (qn in tn or token_score(qn, tn) > 0.7):
                out.append(tag)
            if len(out) >= n:
                break
        return out
