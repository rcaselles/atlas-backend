import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = ("utm_", "mc_", "fbclid", "gclid", "ref", "cmpid", "mod", "__source")
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(text: str | None, max_len: int | None = None) -> str | None:
    if not text:
        return None
    t = _WS.sub(" ", html.unescape(_TAGS.sub(" ", text))).strip()
    if max_len and len(t) > max_len:
        t = t[: max_len - 1].rstrip() + "…"
    return t or None


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(_TRACKING)]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def item_id(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()[:12]


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def to_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
