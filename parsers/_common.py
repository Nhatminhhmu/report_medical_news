import json
import re
from urllib.parse import urljoin, urlparse, urldefrag
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import requests
from bs4 import BeautifulSoup

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36 MedicalNewsIntelligence/1.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
})
TIMEOUT = 25

def clean(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()

def abs_url(href, base):
    if not href:
        return ""
    u = urljoin(base, href)
    u, _ = urldefrag(u)
    return u

def same_domain(a, b):
    return (
        urlparse(a).netloc.lower().removeprefix("www.")
        == urlparse(b).netloc.lower().removeprefix("www.")
    )

def pdate(v):
    v = clean(v)
    if not v:
        return ""
    try:
        d = parsedate_to_datetime(v)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        x = v.replace("Z", "+00:00")
        d = datetime.fromisoformat(x)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ):
        try:
            d = datetime.strptime(v, fmt).replace(tzinfo=timezone.utc)
            return d.isoformat()
        except Exception:
            pass
    return ""

def node_date(node):
    if not node:
        return ""
    t = node.find("time")
    if t:
        d = pdate(t.get("datetime") or t.get_text(" ", strip=True))
        if d:
            return d
    for a in ("data-date", "data-published", "data-published-at", "data-timestamp"):
        d = pdate(node.get(a))
        if d:
            return d
    return ""

def excerpt(node, title):
    if not node:
        return ""
    for s in (".excerpt", ".summary", ".dek", ".description", "p"):
        x = node.select_one(s)
        if x:
            t = clean(x.get_text(" ", strip=True))
            if len(t) >= 40 and t.lower() != title.lower():
                return t[:1200]
    return ""

def jsonld(soup):
    out = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text())
        except Exception:
            continue

        def walk(x):
            if isinstance(x, dict):
                typ = x.get("@type")
                if "@graph" in x:
                    for y in x["@graph"]:
                        walk(y)
                elif typ in ("NewsArticle", "Article", "Report") or (
                    isinstance(typ, list) and any(t in ("NewsArticle", "Article", "Report") for t in typ)
                ):
                    out.append(x)
                elif "itemListElement" in x:
                    for y in x["itemListElement"]:
                        walk(y.get("item", y) if isinstance(y, dict) else y)
            elif isinstance(x, list):
                for y in x:
                    walk(y)

        walk(data)
    return out

def collect_listing(source, allowed=(), blocked=(), max_items=35):
    name = clean(source.get("name"))
    listing = clean(source.get("listing_url"))
    if not listing:
        raise ValueError(f"{name}: listing_url is required")

    print(f"[PARSER] {name}: fetching {listing}")
    r = SESSION.get(listing, timeout=TIMEOUT)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    base = clean(source.get("website_url")) or listing
    items, seen = [], set()

    for x in jsonld(soup):
        title = clean(x.get("headline") or x.get("name"))
        u = x.get("url") or ""
        me = x.get("mainEntityOfPage")
        if isinstance(me, dict):
            u = me.get("@id") or me.get("url") or u
        u = abs_url(u, listing)
        lo = u.lower()

        if not title or len(title) < 30 or not u or not same_domain(u, base):
            continue
        if allowed and not any(p in lo for p in allowed):
            continue
        if any(p in lo for p in blocked) or u in seen:
            continue

        items.append({
            "source": name,
            "title": title,
            "url": u,
            "published_at": pdate(
                x.get("datePublished")
                or x.get("dateCreated")
                or x.get("dateModified")
            ),
            "excerpt": clean(x.get("description"))[:1200],
        })
        seen.add(u)
        if len(items) >= max_items:
            return items

    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        u = abs_url(a.get("href"), listing)
        lo = u.lower()

        if not title or len(title) < 30 or not u or not same_domain(u, base):
            continue
        if allowed and not any(p in lo for p in allowed):
            continue
        if any(p in lo for p in blocked) or u in seen:
            continue
        if any(k in title.lower() for k in (
            "subscribe", "sign in", "log in", "newsletter",
            "read more", "view all", "load more"
        )):
            continue

        node = a
        for _ in range(4):
            if node.parent is None:
                break
            node = node.parent
            if node.name in ("article", "li"):
                break

        items.append({
            "source": name,
            "title": title,
            "url": u,
            "published_at": node_date(node),
            "excerpt": excerpt(node, title),
        })
        seen.add(u)
        if len(items) >= max_items:
            break

    print(f"[PARSER] {name}: {len(items)} candidate articles found.")
    return items
