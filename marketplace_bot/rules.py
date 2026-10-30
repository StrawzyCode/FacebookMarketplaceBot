"""Free option with no AI at all: reads your prompt and checks listings with keyword rules.

It understands location and distance ("within 25 miles of Denver, CO"), budgets ("under $300",
"$1,000-$1,500"), listing age ("posted in the last week"), years ("2016-2019"), measurements
("at least 60 inches", "under 120k miles") and deal-breakers ("no rust"). Everything else in
your prompt becomes keywords that a listing's title and description are checked for.

It is instant and works offline, but it only matches words: it can't look at photos or tell
that "barely used" means good condition. For that, use the free local AI (see ollama_ai.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from .models import FinalRanking, Listing, ListingEvaluation, SearchPlan

NUM = r"\d[\d,]*(?:\.\d+)?"

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "at", "by", "from", "is", "be", "it",
    "that", "this", "my", "me", "i", "im", "i'm", "want", "need", "looking", "some", "any", "one", "has", "have",
    "must", "should", "would", "like", "please", "also", "very", "really", "just", "good", "great", "nice", "decent",
    "condition", "shape", "quality", "something", "thing", "item", "if", "possible", "ideally", "preferably",
    "should", "can", "will", "being", "are", "was", "its", "it's", "but", "so", "too", "as", "than", "more", "less",
}
CONDITION_WORDS = {"used", "new", "like", "good", "great", "nice", "cheap", "working", "clean", "decent", "quality",
                   "excellent", "condition", "gently", "lightly", "barely", "brand"}
NEGATIONS = {"no", "not", "zero", "never", "without", "non", "nothing", "none"}
SYNONYMS = [
    ["couch", "sofa", "sectional", "loveseat"],
    ["tv", "television"],
    ["fridge", "refrigerator"],
    ["bike", "bicycle"],
    ["standing desk", "sit stand desk", "adjustable desk", "height adjustable desk"],
    ["truck", "pickup"],
    ["grill", "bbq"],
    ["washer", "washing machine"],
    ["4x4", "4wd"],
    ["ps5", "playstation 5"],
    ["ps4", "playstation 4"],
    ["dresser", "chest of drawers"],
    ["table saw", "tablesaw"],
    ["mower", "lawnmower", "lawn mower"],
]
DEFAULT_EXCLUDES = ["wanted", "iso", "in search of", "looking for", "parts only", "for parts", "not working"]

UNIT_FAMILIES = {
    # unit word -> (family, factor to the family's base unit)
    "inches": ("length", 1.0), "inch": ("length", 1.0), "in": ("length", 1.0), '"': ("length", 1.0),
    "feet": ("length", 12.0), "foot": ("length", 12.0), "ft": ("length", 12.0),
    "cm": ("length", 1 / 2.54), "mm": ("length", 1 / 25.4),
    "miles": ("distance", 1.0), "mile": ("distance", 1.0), "mi": ("distance", 1.0),
    "km": ("distance", 0.621371), "kms": ("distance", 0.621371), "kilometers": ("distance", 0.621371),
    "lbs": ("weight", 1.0), "lb": ("weight", 1.0), "pounds": ("weight", 1.0), "kg": ("weight", 2.20462),
    "gb": ("storage", 1.0), "tb": ("storage", 1024.0),
    "years": ("years", 1.0), "year": ("years", 1.0), "yrs": ("years", 1.0),
    "hours": ("hours", 1.0), "hrs": ("hours", 1.0),
    "seats": ("seats", 1.0), "seater": ("seats", 1.0),
    "watts": ("watts", 1.0), "w": ("watts", 1.0), "hp": ("hp", 1.0), "cc": ("cc", 1.0),
}
_UNIT_ALT = "|".join(sorted((re.escape(u) for u in UNIT_FAMILIES), key=len, reverse=True))
UNIT_AFTER_RE = re.compile(rf"^\s*k?\s*(?:{_UNIT_ALT})(?![a-z])", re.I)
DIMENSIONS_RE = re.compile(
    rf"(?<![\d.])({NUM})\s*(?:x|×|by)\s*({NUM})(?:\s*(?:x|×|by)\s*({NUM}))?\s*({_UNIT_ALT})(?![a-z])", re.I
)
MEASURE_RE = re.compile(rf"(?<![\d.])({NUM})\s*(k)?\s*(?:-\s*)?({_UNIT_ALT})(?![a-z])", re.I)
NUM_REQ_RE = re.compile(
    rf"(?P<op>at least|min(?:imum)?|over|more than|bigger than|larger than|under|less than|below|"
    rf"max(?:imum)?|no more than|up to|at most|smaller than)\s+(?P<num>{NUM})\s*(?P<k>k)?\s*(?P<unit>{_UNIT_ALT})(?![a-z])"
    rf"|(?P<num2>{NUM})\s*(?P<k2>k)?\s*\+\s*(?P<unit2>{_UNIT_ALT})(?![a-z])",
    re.I,
)
YEAR_RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*(?:-|–|to)\s*((?:19|20)\d{2}|\d{2})\b")
YEAR_MIN_RE = re.compile(r"\b((?:19|20)\d{2})\s*(?:\+|or newer|or later|and newer|and up)")
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")

_WORD = r"(?:(?:St|Ft|Mt|Pt)\.|[A-Z][\w'’-]*)"
LOC_CAP = rf"(?:\d{{5}}(?:-\d{{4}})?|{_WORD}(?:\s+{_WORD})*(?:,\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?|,\s*[A-Z]{{2}}\b)?)"
LOC_LOWER = r"(?:[a-z][a-z.'’ -]*?(?:,\s*[a-z]{2}\b)?)(?=\s*(?:[,;.!?\n]|$|\s(?:under|below|for|and|with|max|budget|less|between|that|which|no|must|posted)\b))"
RADIUS_RE = re.compile(
    r"(?P<pre>\bwithin\b|\bradius(?:\s+of)?\b|\binside\b|\bno (?:more|further|farther) than\b|\bup to\b|\bmax(?:imum)?\b)?"
    r"\s*(?<![\d,.])(?P<num>\d+(?:\.\d+)?)\s*-?\s*(?P<unit>miles?|mi|kilomet(?:er|re)s?|kms?)\b"
    r"(?P<post>\s+(?:radius|away|drive)\b)?(?P<of>\s+(?:of|from|around|near)\b)?",
    re.I,
)
NEAR_RE = re.compile(rf"\b(?:near|around|close to)\s+({LOC_CAP})")
IN_RE = re.compile(r"\bin\s+((?:" + _WORD + r")(?:\s+" + _WORD + r")*,?\s+(?:[A-Z]{2}\b|[A-Z][a-z]+)|\d{5})\b")
ZIP_RE = re.compile(r"\bzip(?:\s*code)?\s*:?\s*(\d{5})\b", re.I)

MAX_PRICE_RE = re.compile(
    rf"(?:\bunder\b|\bbelow\b|\bless than\b|\bno more than\b|\bnot more than\b|\bmax(?:imum)?(?:\s+price)?\b|\bup to\b|"
    rf"\bat most\b|\bbudget(?:\s+(?:is|of))?\b|\bcheaper than\b|<)\s*(?:of\s+)?(?P<cur>\$\s?)?(?P<num>{NUM})(?P<k>\s*k\b)?",
    re.I,
)
TRAILING_MAX_RE = re.compile(rf"\$\s?(?P<num>{NUM})(?P<k>\s*k\b)?\s*(?:or less|or under|or below|max(?:imum)?|tops|budget)\b", re.I)
MIN_PRICE_RE = re.compile(rf"(?:\bover\b|\babove\b|\bat least\b|\bmore than\b|\bmin(?:imum)?\b|\bstarting at\b)\s*\$\s?(?P<num>{NUM})(?P<k>\s*k\b)?", re.I)
RANGE_RE = re.compile(
    rf"(?:\bbetween\s+)?\$\s?(?P<a>{NUM})(?P<ka>\s*k\b)?\s*(?:-|–|to|and)\s*\$?\s?(?P<b>{NUM})(?P<kb>\s*k\b)?"
    rf"|\bbetween\s+(?P<c>{NUM})(?P<kc>\s*k\b)?\s+and\s+(?P<d>{NUM})(?P<kd>\s*k\b)?(?!\s*(?:{_UNIT_ALT})(?![a-z]))",
    re.I,
)
DAYS_PATTERNS = [
    (re.compile(r"(?:posted|listed|added)?\s*(?:in\s+)?(?:the\s+)?(?:last|past|within(?:\s+the\s+last)?)\s+(\d+)\s+days?", re.I), None),
    (re.compile(r"(?:posted|listed|added)\s+today|(?:last|past)\s+24\s+hours", re.I), 1),
    (re.compile(r"(?:(?:posted|listed|added)\s+)?(?:in\s+)?(?:the\s+)?(?:last|past|this)\s+week|within\s+a\s+week", re.I), 7),
    (re.compile(r"(?:(?:posted|listed|added)\s+)?(?:in\s+)?(?:the\s+)?(?:last|past|this)\s+month|within\s+a\s+month", re.I), 30),
]
LEAD_IN_RE = re.compile(
    r"^(?:(?:hi|hey|hello)\b[,!]?\s*)?(?:i\s*(?:'m|’m|am)\s+)?(?:(?:looking|searching|hunting)\s+for|i\s+(?:want|need|would like)"
    r"|(?:please\s+)?find(?:\s+me)?|(?:i'd|i’d)\s+like|get\s+me|in\s+the\s+market\s+for|need|want|seeking)\s+",
    re.I,
)
ARTICLE_RE = re.compile(r"^(?:an?|the|some|any|one|a\s+pair\s+of)\s+", re.I)
DEAL_BREAKER_RE = re.compile(
    r"^(?:no|not|without|avoid|nothing|never|exclude|don'?t want|do not want|can'?t be|cannot be|shouldn'?t be|"
    r"must not be|mustn'?t be|must not have|no\s+\w+\s+allowed)\b\s*(?:an?\s+|any\s+)?",
    re.I,
)
NICE_RE = re.compile(
    r"\b(?:would be nice|nice to have|bonus(?: if)?|prefer(?:ably|red)?|ideally|if possible|plus if|extra points?|"
    r"would love|optional)\b[:,]?\s*",
    re.I,
)
NICE_PREFIX_RE = re.compile(r"^(?:if\s+)?(?:it\s+)?(?:has|had|comes with|came with|includes|included|is)\s+", re.I)
MUST_PREFIX_RE = re.compile(r"^(?:must (?:have|be|include)|needs? to (?:have|be)|has to (?:have|be)|needs?|requires?|should (?:have|be)|with)\s+", re.I)
SHIPPING_OK_RE = re.compile(r"\b(?:shipping (?:is )?(?:ok|okay|fine)|can be shipped|will pay (?:for )?shipping|ship(?:ped)? to me)\b", re.I)
# (pattern, warning, only relevant when the shopper wants local pickup)
SCAM_PATTERNS = [
    (re.compile(r"\b(zelle|cash ?app|venmo|wire transfer|western union|money ?gram|gift ?cards?|paypal friends)\b", re.I),
     "Mentions {0}: pay in person when you pick it up, never in advance", False),
    (re.compile(r"\b(deposit)\b", re.I), "Mentions a {0}: don't pay before you've seen the item", False),
    (re.compile(r"\b(shipping only|will ship|can ship)\b", re.I),
     "Mentions {0}: be careful paying before you've seen the item", True),
    (re.compile(r"\b(?:text|call|email|whatsapp)\s+(?:me\s+)?(?:at|on)\b|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b", re.I),
     "Asks you to contact them outside Facebook", False),
]
STRUCTURED_KEYS = {
    "item": "item", "looking for": "item", "want": "item", "what": "item",
    "search": "queries", "searches": "queries", "search terms": "queries", "keywords": "queries", "queries": "queries",
    "location": "location", "near": "location", "where": "location", "around": "location", "city": "location", "zip": "location",
    "distance": "radius", "radius": "radius", "within": "radius", "max distance": "radius",
    "max price": "max_price", "budget": "max_price", "maximum price": "max_price", "max": "max_price",
    "min price": "min_price", "minimum price": "min_price", "min": "min_price", "price": "price",
    "must have": "must", "must haves": "must", "must-have": "must", "must-haves": "must", "requirements": "must",
    "needs": "must", "required": "must",
    "nice to have": "nice", "nice to haves": "nice", "nice-to-have": "nice", "prefer": "nice", "preferences": "nice",
    "bonus": "nice",
    "avoid": "avoid", "deal breakers": "avoid", "deal-breakers": "avoid", "dealbreakers": "avoid", "exclude": "avoid",
    "posted within": "days", "listed within": "days", "days": "days", "max age": "days",
}
STRUCTURED_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z \-]{1,25}?)\s*:\s*(.+?)\s*$")


# ---------------------------------------------------------------------------------------------------------------
# Text helpers


def _amount(num: str, k: str | None = None) -> float:
    value = float(num.replace(",", ""))
    return value * 1000 if k and k.strip() else value


def _stem(word: str) -> str:
    word = word.strip("'’.")
    if word.endswith(("'s", "’s")):
        word = word[:-2]
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "xes", "sses", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _normalize(text: str) -> str:
    text = (text or "").lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9.+#'\"]+", " ", text)
    return " " + " ".join(text.split()) + " "


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in _normalize(text).split() if t.strip(".")]


def content_terms(text: str) -> list[str]:
    """The meaningful words of a phrase ("must have memory presets" -> ["memory", "preset"])."""
    out = []
    for t in _tokens(text):
        t = t.strip(".")
        if t and t not in STOPWORDS and not t.isdigit() and t not in out:
            out.append(t)
    return out


def _alternatives(term: str) -> list[str]:
    for group in SYNONYMS:
        if term in group:
            return group
    return [term]


class ListingText:
    """A listing's text, prepared for repeated keyword checks."""

    def __init__(self, *parts: str):
        self.raw = "\n".join(p for p in parts if p)
        self.norm = _normalize(self.raw)
        self.tokens = _tokens(self.raw)
        self.token_set = set(self.tokens)

    def has(self, term: str) -> bool:
        for alt in _alternatives(term):
            if " " in alt:
                if f" {alt} " in self.norm:
                    return True
            elif _stem(alt) in self.token_set:
                return True
        return False

    def has_unnegated(self, phrase: str) -> bool:
        """True if the phrase appears somewhere that isn't 'no X' / 'X-free'."""
        terms = content_terms(phrase)
        if not terms:
            return False
        first = terms[0]
        n = len(terms)
        for i, tok in enumerate(self.tokens):
            if tok != first or self.tokens[i : i + n] != terms:
                continue
            before = self.tokens[max(0, i - 3) : i]
            after = self.tokens[i + n : i + n + 1]
            if not (NEGATIONS & set(before)) and after != ["free"]:
                return True
        return False


@dataclass
class _NumReq:
    op: str  # "max" or "min"
    value: float
    family: str


def _num_requirement(text: str) -> _NumReq | None:
    m = NUM_REQ_RE.search(text)
    if not m:
        return None
    if m.group("num2"):
        op, num, k, unit = "min", m.group("num2"), m.group("k2"), m.group("unit2")
    else:
        word = m.group("op").lower()
        op = "min" if word in {"at least", "min", "minimum", "over", "more than", "bigger than", "larger than"} else "max"
        num, k, unit = m.group("num"), m.group("k"), m.group("unit")
    family, factor = UNIT_FAMILIES[unit.lower()]
    return _NumReq(op, _amount(num, k) * factor, family)


def _measurements(text: str, family: str) -> list[float]:
    """Every measurement in the text in the family's base unit. "72 x 30 inches" counts as its largest side."""
    values = []
    for m in DIMENSIONS_RE.finditer(text):
        fam, factor = UNIT_FAMILIES[m.group(4).lower()]
        if fam == family:
            values.append(max(_amount(n) for n in m.groups()[:3] if n) * factor)
    text = DIMENSIONS_RE.sub(" ", text)
    for num, k, unit in MEASURE_RE.findall(text):
        fam, factor = UNIT_FAMILIES[unit.lower()]
        if fam == family:
            values.append(_amount(num, k) * factor)
    return values


def _year_requirement(text: str) -> tuple[int, int] | None:
    m = YEAR_RANGE_RE.search(text)
    if m:
        start, end = int(m.group(1)), m.group(2)
        end = int(end) if len(end) == 4 else int(str(start)[:2] + end)
        return (min(start, end), max(start, end))
    m = YEAR_MIN_RE.search(text)
    if m:
        return (int(m.group(1)), 2100)
    return None


def check_requirement(requirement: str, listing: ListingText, title: str = "") -> bool | None:
    """True = clearly met, False = clearly not met, None = the listing doesn't say."""
    num_req = _num_requirement(requirement)
    if num_req:
        values = _measurements(listing.raw, num_req.family)
        if not values:
            return None
        ok = [v <= num_req.value if num_req.op == "max" else v >= num_req.value for v in values]
        return True if all(ok) else False if not any(ok) else None
    years = _year_requirement(requirement)
    if years:
        found = YEAR_RE.findall(title) or YEAR_RE.findall(listing.raw)
        if not found:
            return None
        return years[0] <= int(found[0]) <= years[1]
    terms = content_terms(requirement)
    if not terms:
        return None
    return True if all(listing.has(t) for t in terms) else None


# ---------------------------------------------------------------------------------------------------------------
# Understanding the prompt


def _structured_fields(prompt: str) -> tuple[dict[str, list[str]], str]:
    fields: dict[str, list[str]] = {}
    rest = []
    for line in prompt.splitlines():
        m = STRUCTURED_LINE_RE.match(line)
        key = STRUCTURED_KEYS.get(m.group(1).strip().lower()) if m else None
        if key:
            fields.setdefault(key, []).append(m.group(2))
        else:
            rest.append(line)
    return fields, "\n".join(rest)


def _split_list(values: list[str]) -> list[str]:
    out = []
    for v in values:
        out += [p.strip() for p in re.split(r"[,;]", v) if p.strip()]
    return out


class _Consumer:
    """Tracks which parts of the prompt have been understood, so the rest can become keywords."""

    def __init__(self, text: str):
        self.text = text
        self.spans: list[tuple[int, int]] = []

    def take(self, start: int, end: int) -> None:
        self.spans.append((start, end))

    def remainder(self) -> str:
        chars = list(self.text)
        for start, end in self.spans:
            for i in range(start, end):
                chars[i] = ","
        return "".join(chars)


def _parse_radius(text: str, consumer: _Consumer) -> tuple[float | None, str]:
    for m in RADIUS_RE.finditer(text):
        if not (m.group("pre") or m.group("post") or m.group("of")):
            continue
        miles = float(m.group("num"))
        if m.group("unit").lower().startswith("k"):
            miles *= 0.621371
        location = ""
        end = m.end()
        if m.group("of"):
            tail = text[m.end():]
            lm = re.match(rf"\s+({LOC_CAP})", tail) or re.match(rf"\s+({LOC_LOWER})", tail)
            if lm:
                location = lm.group(1).strip(" ,")
                end = m.end() + lm.end()
        consumer.take(m.start(), end)
        return miles, location
    return None, ""


def _parse_location(text: str, consumer: _Consumer) -> str:
    for regex in (NEAR_RE, IN_RE, ZIP_RE):
        m = regex.search(text)
        if m and m.group(1).lower() != "me":
            consumer.take(m.start(), m.end())
            return m.group(1).strip(" ,")
    return ""


def _parse_prices(text: str, consumer: _Consumer) -> tuple[int | None, int | None]:
    lo = hi = None
    m = RANGE_RE.search(text)
    if m:
        if m.group("a"):
            lo, hi = _amount(m.group("a"), m.group("ka")), _amount(m.group("b"), m.group("kb"))
        else:
            lo, hi = _amount(m.group("c"), m.group("kc")), _amount(m.group("d"), m.group("kd"))
        consumer.take(m.start(), m.end())
        return int(min(lo, hi)), int(max(lo, hi))
    for m in MAX_PRICE_RE.finditer(text):
        if not m.group("cur") and UNIT_AFTER_RE.match(text[m.end():]):
            continue  # "under 120k miles" is a mileage, not a price
        if not m.group("cur") and re.match(r"\s*(?:inches|inch|in\b|ft|feet|cm|years?|yrs?)", text[m.end():], re.I):
            continue
        hi = _amount(m.group("num"), m.group("k"))
        consumer.take(m.start(), m.end())
        break
    if hi is None:
        m = TRAILING_MAX_RE.search(text)
        if m:
            hi = _amount(m.group("num"), m.group("k"))
            consumer.take(m.start(), m.end())
    m = MIN_PRICE_RE.search(text)
    if m:
        lo = _amount(m.group("num"), m.group("k"))
        consumer.take(m.start(), m.end())
    return (None if lo is None else int(lo)), (None if hi is None else int(hi))


def _parse_days(text: str, consumer: _Consumer) -> int | None:
    for regex, fixed in DAYS_PATTERNS:
        m = regex.search(text)
        if m:
            consumer.take(m.start(), m.end())
            return fixed if fixed is not None else int(m.group(1))
    return None


def _clean_clause(clause: str) -> str:
    clause = clause.strip(" -–:*•\t")
    clause = LEAD_IN_RE.sub("", clause)
    clause = ARTICLE_RE.sub("", clause)
    return clause.strip(" ,.-")


def _queries_for(item: str, extra: list[str]) -> list[str]:
    words = [w for w in re.split(r"\s+", item.lower()) if w]
    words = [w for w in words if w.strip(",.") not in CONDITION_WORDS and not YEAR_RE.fullmatch(w)]
    core = " ".join(words).strip()
    queries = [q.strip().lower() for q in extra if q.strip()]
    if core:
        queries.append(" ".join(core.split()[:6]))
        meaningful = [w for w in core.split() if w not in STOPWORDS]
        if len(meaningful) > 2:
            queries.append(" ".join(meaningful[-2:]))
        for group in SYNONYMS:
            for term in group:
                if f" {term} " in f" {core} ":
                    queries += [
                        f" {core} ".replace(f" {term} ", f" {alt} ").strip()
                        for alt in group
                        if alt != term and f" {alt} " not in f" {core} "
                    ]
                    break
    seen, out = set(), []
    for q in queries:
        q = " ".join(q.split())
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:6]


def parse_prompt(prompt: str, default_location: str = "", default_radius: float | None = None) -> SearchPlan:
    fields, text = _structured_fields(prompt)
    consumer = _Consumer(text)

    radius, location = _parse_radius(text, consumer)
    if not location:
        location = _parse_location(text, consumer)
    min_price, max_price = _parse_prices(text, consumer)
    days = _parse_days(text, consumer)

    # Structured "key: value" lines win over what was read from free text.
    if fields.get("location"):
        location = fields["location"][0]
    if fields.get("radius"):
        r, _ = _parse_radius("within " + fields["radius"][0] + " of", _Consumer(""))
        radius = r if r is not None else _first_number(fields["radius"][0])
    for key in ("max_price", "min_price", "price"):
        if fields.get(key):
            lo, hi = _parse_prices(_price_phrase(key, fields[key][0]), _Consumer(""))
            min_price = lo if lo is not None else min_price
            max_price = hi if hi is not None else max_price
    if fields.get("days"):
        days = _parse_days(fields["days"][0], _Consumer("")) or _first_number(fields["days"][0]) or days

    item = (fields.get("item") or [""])[0]
    must: list[str] = []
    nice: list[str] = []
    avoid: list[str] = []
    remainder = consumer.remainder()
    for raw in re.split(r"[,;\n!?]|\.(?=\s|$)|\bbut\b", remainder):
        clause = _clean_clause(raw)
        if not clause or not content_terms(clause) and not _num_requirement(clause) and not _year_requirement(clause):
            continue
        if DEAL_BREAKER_RE.match(clause):
            avoid.append(DEAL_BREAKER_RE.sub("", clause).strip())
        elif NICE_RE.search(clause):
            nice.append(NICE_PREFIX_RE.sub("", NICE_RE.sub("", clause).strip()).strip())
        elif not item:
            item = clause
        else:
            must.append(MUST_PREFIX_RE.sub("", clause).strip())

    # Pull requirements out of the item phrase itself: years, sizes, "with ..." clauses.
    years = YEAR_RANGE_RE.search(item) or YEAR_MIN_RE.search(item)
    if years:
        must.insert(0, f"year {years.group(0)}")
        item = (item[: years.start()] + " " + item[years.end():]).strip()
    num = NUM_REQ_RE.search(item)
    if num:
        must.insert(0, item[num.start():].strip())
        item = item[: num.start()].strip()
    parts = re.split(r"\s+(?:with|that|which|w/)\s+", item, maxsplit=1)
    item = parts[0].strip()
    if len(parts) > 1:
        must.insert(0, parts[1].strip())

    must += _split_list(fields.get("must", []))
    nice += _split_list(fields.get("nice", []))
    avoid += _split_list(fields.get("avoid", []))
    must = [m for m in dict.fromkeys(must) if m]
    location = location or default_location
    radius = radius if radius is not None else default_radius

    prompt_lower = prompt.lower()
    excludes = [kw for kw in DEFAULT_EXCLUDES if not any(w in prompt_lower for w in ("part", "broken", "not working"))
                or kw in {"wanted", "iso", "in search of", "looking for"}]

    summary = item[:1].upper() + item[1:] if item else "Item from your prompt"
    if max_price is not None:
        summary += f", up to {max_price:,}"
    if location:
        summary += f", near {location}"

    return SearchPlan(
        summary=summary,
        queries=_queries_for(item, _split_list(fields.get("queries", []))),
        location=location,
        radius_miles=radius,
        min_price=min_price,
        max_price=max_price,
        days_since_listed=days,
        must_haves=must,
        nice_to_haves=[n for n in dict.fromkeys(nice) if n],
        deal_breakers=[a for a in dict.fromkeys(avoid) if a],
        exclude_title_keywords=excludes,
        local_pickup_only=not SHIPPING_OK_RE.search(prompt),
    )


def _first_number(text: str) -> float | None:
    m = re.search(NUM, text)
    return _amount(m.group(0)) if m else None


def _price_phrase(key: str, value: str) -> str:
    value = value.strip()
    if key == "price":
        return value if "$" in value or re.search(r"[a-z]", value, re.I) else "$" + value.replace("-", "-$")
    if not value.startswith("$"):
        value = "$" + value
    return ("under " if key == "max_price" else "over ") + value


# ---------------------------------------------------------------------------------------------------------------
# Scoring listings


def _item_fit(plan: SearchPlan, text: ListingText, title: ListingText) -> tuple[float, list[str]]:
    terms = content_terms(plan.queries[0]) if plan.queries else []
    if not terms:
        return 1.0, []
    score = 0.0
    missing = []
    for t in terms:
        if title.has(t):
            score += 1
        elif text.has(t):
            score += 0.75
        else:
            missing.append(t)
    return score / len(terms), missing


class RulesMatcher:
    """Same interface as the AI matchers, using keyword rules instead of a language model."""

    uses_images = False

    def plan(self, prompt: str, default_location: str, default_radius_miles: float | None) -> SearchPlan:
        return parse_prompt(prompt, default_location, default_radius_miles)

    def triage(self, prompt: str, plan: SearchPlan, listings: list[Listing]) -> dict[str, tuple[int, str]]:
        out = {}
        for l in listings:
            title = ListingText(l.title, *l.extras)
            fit, missing = _item_fit(plan, title, title)
            hits = sum(1 for m in plan.must_haves if check_requirement(m, title, l.title))
            failed = any(check_requirement(m, title, l.title) is False for m in plan.must_haves)
            score = 8 * fit + (2 * hits / len(plan.must_haves) if plan.must_haves else 1)
            if failed:
                score = min(score, 2)
            reason = "title matches your item" if not missing else f"title lacks: {', '.join(missing)}"
            out[l.id] = (round(score), reason)
        return out

    def evaluate(self, prompt: str, plan: SearchPlan, listing: Listing, images) -> ListingEvaluation:
        text = ListingText(listing.title, listing.description, listing.page_text, *listing.extras)
        title = ListingText(listing.title)
        fit, missing_terms = _item_fit(plan, text, title)

        matched, unclear, failed = [], [], []
        for req in plan.must_haves:
            result = check_requirement(req, text, listing.title)
            (matched if result else failed if result is False else unclear).append(req)
        must_score = (len(matched) + 0.4 * len(unclear)) / len(plan.must_haves) if plan.must_haves else 1.0
        nice_hits = [n for n in plan.nice_to_haves if check_requirement(n, text, listing.title)]
        nice_score = len(nice_hits) / len(plan.nice_to_haves) if plan.nice_to_haves else 0.5
        breakers = [d for d in plan.deal_breakers if text.has_unnegated(d)]

        in_budget = listing.price is not None and (plan.max_price is None or listing.price <= plan.max_price)
        nearby = listing.distance_miles is not None
        score = 45 * fit + 40 * must_score + 10 * nice_score + 2.5 * in_budget + 2.5 * nearby
        if fit < 0.5:
            score = min(score, 39)
        if failed:
            score = min(score, 39)
        if breakers:
            score = min(score, 30)
        score = round(score)
        verdict = "perfect" if score >= 90 else "strong" if score >= 70 else "possible" if score >= 40 else "poor"

        red_flags = [f"Mentions a deal-breaker: {d}" for d in breakers]
        for regex, message, pickup_only in SCAM_PATTERNS:
            m = regex.search(text.raw)
            if m and (plan.local_pickup_only or not pickup_only):
                red_flags.append(message.format(m.group(0).lower()))
        if len((listing.description or "").strip()) < 40:
            red_flags.append("Very little detail in the description")

        summary_bits = []
        if missing_terms:
            summary_bits.append(f"The listing doesn't mention {', '.join(missing_terms)}, so it may not be the right item.")
        if plan.must_haves:
            summary_bits.append(f"Confirms {len(matched)} of {len(plan.must_haves)} must-haves.")
        if failed:
            summary_bits.append(f"Doesn't meet: {'; '.join(failed)}.")
        if nice_hits:
            summary_bits.append(f"Bonus: {'; '.join(nice_hits)}.")
        if not summary_bits:
            summary_bits.append("Looks like the item you described.")

        return ListingEvaluation(
            match_score=score,
            verdict=verdict,
            summary=" ".join(summary_bits),
            matched_requirements=matched + nice_hits,
            missing_or_unclear=[f"Not met: {f}" for f in failed] + unclear,
            red_flags=red_flags,
            questions_for_seller=[f"Can you confirm: {u}?" for u in unclear[:4]]
            + ["Is it still available, and when could I come see it?"],
            price_assessment=_price_note(listing, plan),
        )

    def final_pick(self, prompt: str, plan: SearchPlan, listings: list[Listing]) -> FinalRanking:
        prices = [l.price for l in listings if l.price]
        typical = median(prices) if len(prices) >= 3 else None
        for l in listings:
            # Only now are other listings known, so flag prices that are suspiciously low compared to them.
            if typical and l.price and l.price < 0.35 * typical and l.evaluation:
                l.evaluation.red_flags.append(
                    f"Price is far below similar listings (around {typical:,.0f}): could be a scam or have a problem"
                )
        ranked = sorted(
            listings,
            key=lambda l: (
                -(l.evaluation.match_score if l.evaluation else 0),
                -len(l.evaluation.matched_requirements) if l.evaluation else 0,
                l.distance_miles if l.distance_miles is not None else 1e9,
                l.price if l.price is not None else 1e12,
            ),
        )
        best = ranked[0] if ranked and ranked[0].evaluation and ranked[0].evaluation.match_score >= 60 else None
        if best:
            ev = best.evaluation
            where = f"{best.distance_miles:.0f} mi away" if best.distance_miles is not None else "distance unknown"
            explanation = f"Best keyword match ({ev.match_score}/100), {best.price_text or 'no price listed'}, {where}. {ev.summary}"
        else:
            explanation = "No listing clearly matched everything you asked for."
        return FinalRanking(best_id=best.id if best else "", ranking=[l.id for l in ranked], explanation=explanation)


def _price_note(listing: Listing, plan: SearchPlan) -> str:
    if listing.price is None:
        return "No price listed: ask the seller."
    if listing.price == 0:
        return "Free."
    if plan.max_price is not None and listing.price > plan.max_price:
        return f"{listing.price_text} is over your budget of {plan.max_price:,}."
    if plan.max_price is not None:
        return f"{listing.price_text}, within your budget of {plan.max_price:,}."
    return f"Asking {listing.price_text}."
