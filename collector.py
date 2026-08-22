import json
import os
import hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import feedparser
import gspread
import requests
from bs4 import BeautifulSoup

from google.oauth2.service_account import Credentials

from parsers import himss


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SOURCES_SHEET = "Sources"
SETTINGS_SHEET = "Settings"
ARTICLES_SHEET = "Articles"

REQUEST_TIMEOUT = 30

USER_AGENT = (
    "ReportMedicalNews/0.1 "
    "(Healthcare Operations Intelligence)"
)


# ============================================================
# GOOGLE SHEETS
# ============================================================

def get_google_client():
    credentials_json = os.environ[
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    ]

    spreadsheet_id = os.environ[
        "GOOGLE_SPREADSHEET_ID"
    ]

    credentials_info = json.loads(
        credentials_json
    )

    credentials = (
        Credentials.from_service_account_info(
            credentials_info,
            scopes=SCOPES,
        )
    )

    client = gspread.authorize(
        credentials
    )

    return client.open_by_key(
        spreadsheet_id
    )


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def make_article_id(url):
    return hashlib.sha256(
        url.strip().encode("utf-8")
    ).hexdigest()[:16]


def normalize_url(
    url,
    base_url=None,
):
    url = clean_text(url)

    if not url:
        return ""

    if base_url:
        return urljoin(
            base_url,
            url,
        )

    return url


# ============================================================
# SETTINGS
# ============================================================

def get_setting(
    spreadsheet,
    key,
    default=None,
):
    worksheet = spreadsheet.worksheet(
        SETTINGS_SHEET
    )

    records = worksheet.get_all_records()

    for row in records:
        row_key = clean_text(
            row.get(
                "key",
                "",
            )
        )

        if row_key == key:
            value = clean_text(
                row.get(
                    "value",
                    "",
                )
            )

            return value

    return default


def get_lookback_days(
    spreadsheet,
):
    value = get_setting(
        spreadsheet,
        "lookback_days",
        7,
    )

    try:
        days = int(value)

        if days < 0:
            raise ValueError

        return days

    except (TypeError, ValueError):
        print(
            "Invalid lookback_days. "
            "Using default: 7"
        )

        return 7


# ============================================================
# FRESHNESS
# ============================================================

def parse_published_date(
    published_at,
):
    if not published_at:
        return None

    try:
        parsed = feedparser._parse_date(
            published_at
        )

        if not parsed:
            return None

        return datetime(
            parsed.tm_year,
            parsed.tm_mon,
            parsed.tm_mday,
            parsed.tm_hour,
            parsed.tm_min,
            parsed.tm_sec,
            tzinfo=timezone.utc,
        )

    except Exception:
        return None


def is_recent_article(
    published_at,
    lookback_days,
):
    if not published_at:
        return True

    published = parse_published_date(
        published_at
    )

    if published is None:
        return True

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(
            days=lookback_days
        )
    )

    return published >= cutoff


# ============================================================
# SOURCES
# ============================================================

def get_active_sources(
    spreadsheet,
):
    worksheet = spreadsheet.worksheet(
        SOURCES_SHEET
    )

    records = worksheet.get_all_records()

    sources = []

    for row in records:
        active = (
            str(
                row.get(
                    "active",
                    "",
                )
            )
            .strip()
            .lower()
        )

        if active not in (
            "true",
            "1",
            "yes",
        ):
            continue

        name = clean_text(
            row.get(
                "name",
                "",
            )
        )

        access_method = (
            clean_text(
                row.get(
                    "access_method",
                    "",
                )
            )
            .upper()
        )

        if not name:
            continue

        if access_method not in (
            "RSS",
            "WEB",
            "API",
        ):
            print(
                f"Skipping {name}: "
                f"unsupported access_method "
                f"'{access_method}'"
            )
            continue

        sources.append(
            row
        )

    return sources


# ============================================================
# RSS COLLECTOR
# ============================================================

def collect_rss(source):
    name = clean_text(
        source.get("name")
    )

    feed_url = clean_text(
        source.get("feed_url")
    )

    if not feed_url:
        raise ValueError(
            "RSS source has no feed_url"
        )

    print(
        f"[RSS] Fetching {name}: "
        f"{feed_url}"
    )

    feed = feedparser.parse(
        feed_url
    )

    if getattr(
        feed,
        "bozo",
        False,
    ):
        print(
            f"[RSS] Warning for {name}: "
            "feed parser reported an issue."
        )

    articles = []

    for entry in feed.entries:
        title = clean_text(
            entry.get(
                "title",
                "",
            )
        )

        url = normalize_url(
            entry.get(
                "link",
                "",
            )
        )

        if not title or not url:
            continue

        published_at = clean_text(
            entry.get(
                "published",
                "",
            )
            or entry.get(
                "updated",
                "",
            )
        )

        excerpt = clean_text(
            entry.get(
                "summary",
                "",
            )
        )

        articles.append(
            {
                "source": name,
                "title": title,
                "url": url,
                "published_at": published_at,
                "excerpt": excerpt,
                "topic": clean_text(
                    source.get(
                        "domain",
                        "",
                    )
                ),
            }
        )

    print(
        f"[RSS] {name}: "
        f"{len(articles)} articles found."
    )

    return articles


# ============================================================
# GENERIC WEB COLLECTOR
# ============================================================

def collect_web(source):
    name = clean_text(
        source.get("name")
    )

    listing_url = clean_text(
        source.get("listing_url")
    )

    if not listing_url:
        raise ValueError(
            "WEB source has no listing_url"
        )

    print(
        f"[WEB] Fetching {name}: "
        f"{listing_url}"
    )

    headers = {
        "User-Agent": USER_AGENT
    }

    response = requests.get(
        listing_url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    articles = []
    seen_urls = set()

    article_indicators = [
        "/news/",
        "/article/",
        "/articles/",
        "/news-center/",
        "/blog/",
        "/insights/",
        "/stories/",
    ]

    ignored_patterns = [
        "/search",
        "/login",
        "/contact",
        "/about",
        "/events",
        "/membership",
        "/privacy",
        "/terms",
        "javascript:",
        "#",
    ]

    for link in soup.find_all(
        "a",
        href=True,
    ):
        href = link.get("href")

        title = clean_text(
            link.get_text(
                " ",
                strip=True,
            )
        )

        if not href or not title:
            continue

        url = normalize_url(
            href,
            listing_url,
        )

        if not url:
            continue

        if url in seen_urls:
            continue

        lowered_url = url.lower()

        if any(
            pattern in lowered_url
            for pattern in ignored_patterns
        ):
            continue

        if not any(
            indicator in lowered_url
            for indicator in article_indicators
        ):
            continue

        if len(title) < 20:
            continue

        if len(title) > 300:
            continue

        seen_urls.add(url)

        articles.append(
            {
                "source": name,
                "title": title,
                "url": url,
                "published_at": "",
                "excerpt": "",
                "topic": clean_text(
                    source.get(
                        "domain",
                        "",
                    )
                ),
            }
        )

    print(
        f"[WEB] {name}: "
        f"{len(articles)} candidate articles found."
    )

    return articles


# ============================================================
# API PLACEHOLDER
# ============================================================

def collect_api(source):
    name = clean_text(
        source.get("name")
    )

    raise NotImplementedError(
        f"API collector is not implemented yet "
        f"for source: {name}"
    )


# ============================================================
# SOURCE DISPATCHER
# ============================================================

def collect_source(source):
    name = clean_text(
        source.get(
            "name",
            "",
        )
    )

    access_method = (
        clean_text(
            source.get(
                "access_method",
                "",
            )
        )
        .upper()
    )

    if name.lower() == "himss":
        return himss.collect(
            source
        )

    if access_method == "RSS":
        return collect_rss(
            source
        )

    if access_method == "WEB":
        return collect_web(
            source
        )

    if access_method == "API":
        return collect_api(
            source
        )

    raise ValueError(
        f"Unsupported access method: "
        f"{access_method}"
    )


# ============================================================
# NORMALIZE ARTICLES
# ============================================================

def normalize_articles(
    articles,
):
    normalized = []

    for article in articles:
        url = clean_text(
            article.get(
                "url",
                "",
            )
        )

        title = clean_text(
            article.get(
                "title",
                "",
            )
        )

        if not url or not title:
            continue

        normalized.append(
            {
                "article_id": make_article_id(
                    url
                ),
                "source": clean_text(
                    article.get(
                        "source",
                        "",
                    )
                ),
                "title": title,
                "url": url,
                "published_at": clean_text(
                    article.get(
                        "published_at",
                        "",
                    )
                ),
                "excerpt": clean_text(
                    article.get(
                        "excerpt",
                        "",
                    )
                ),
                "topic": clean_text(
                    article.get(
                        "topic",
                        "",
                    )
                ),
                "status": "DISCOVERED",
                "discovered_at": utc_now(),
            }
        )

    return normalized


# ============================================================
# SAVE ARTICLES
# ============================================================

def get_existing_article_ids(
    spreadsheet,
):
    worksheet = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    records = worksheet.get_all_records()

    return {
        clean_text(
            row.get(
                "article_id",
                "",
            )
        )
        for row in records
        if row.get(
            "article_id",
            "",
        )
    }


def save_articles(
    spreadsheet,
    articles,
):
    worksheet = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    existing_ids = (
        get_existing_article_ids(
            spreadsheet
        )
    )

    unique_articles = {}

    for article in articles:
        article_id = article[
            "article_id"
        ]

        if article_id in existing_ids:
            continue

        unique_articles[
            article_id
        ] = article

    new_articles = list(
        unique_articles.values()
    )

    if not new_articles:
        print(
            "No new articles to save."
        )
        return 0

    rows = []

    for article in new_articles:
        rows.append(
            [
                article[
                    "article_id"
                ],
                article[
                    "source"
                ],
                article[
                    "title"
                ],
                article[
                    "url"
                ],
                article[
                    "published_at"
                ],
                article[
                    "excerpt"
                ],
                "",
                "",
                "",
                article[
                    "status"
                ],
                article[
                    "discovered_at"
                ],
            ]
        )

    worksheet.append_rows(
        rows,
        value_input_option="USER_ENTERED",
    )

    print(
        f"Saved {len(rows)} new articles."
    )

    return len(rows)


# ============================================================
# MAIN
# ============================================================

def main():
    spreadsheet = get_google_client()

    print(
        f"Connected to: "
        f"{spreadsheet.title}"
    )

    lookback_days = get_lookback_days(
        spreadsheet
    )

    print(
        f"Lookback period: "
        f"{lookback_days} days"
    )

    sources = get_active_sources(
        spreadsheet
    )

    print(
        f"Active sources: "
        f"{len(sources)}"
    )

    all_articles = []
    source_results = []

    for source in sources:
        name = clean_text(
            source.get(
                "name",
                "Unknown",
            )
        )

        try:
            candidates = collect_source(
                source
            )

            normalized = normalize_articles(
                candidates
            )

            recent_articles = [
                article
                for article in normalized
                if is_recent_article(
                    article.get(
                        "published_at",
                        "",
                    ),
                    lookback_days,
                )
            ]

            print(
                f"[{name}] "
                f"{len(recent_articles)} recent "
                f"articles out of "
                f"{len(normalized)} candidates."
            )

            all_articles.extend(
                recent_articles
            )

            source_results.append(
                {
                    "name": name,
                    "status": "OK",
                    "count": len(
                        recent_articles
                    ),
                    "candidates": len(
                        normalized
                    ),
                    "error": "",
                }
            )

        except Exception as e:
            print(
                f"[ERROR] {name}: {e}"
            )

            source_results.append(
                {
                    "name": name,
                    "status": "ERROR",
                    "count": 0,
                    "candidates": 0,
                    "error": str(e),
                }
            )

    print("")
    print("=" * 60)
    print("COLLECTION SUMMARY")
    print("=" * 60)

    for result in source_results:
        print(
            f"{result['name']}: "
            f"{result['status']} "
            f"({result['count']} recent / "
            f"{result['candidates']} candidates)"
        )

        if result["error"]:
            print(
                f"  Error: "
                f"{result['error']}"
            )

    print("=" * 60)

    print(
        f"Total recent articles: "
        f"{len(all_articles)}"
    )

    saved = save_articles(
        spreadsheet,
        all_articles,
    )

    print(
        f"New articles saved: "
        f"{saved}"
    )


if __name__ == "__main__":
    main()
