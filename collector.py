import json
import os
import hashlib
from datetime import datetime, timezone

import feedparser
import gspread

from google.oauth2.service_account import Credentials


# ============================================================
# CONFIG
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SOURCES_SHEET = "Sources"
ARTICLES_SHEET = "Articles"


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

    spreadsheet = client.open_by_key(
        spreadsheet_id
    )

    return spreadsheet


# ============================================================
# HELPERS
# ============================================================

def make_article_id(url):

    return hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()[:16]


def utc_now():

    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# READ SOURCES
# ============================================================

def get_active_sources(spreadsheet):

    worksheet = spreadsheet.worksheet(
        SOURCES_SHEET
    )

    records = worksheet.get_all_records()

    sources = []

    for row in records:

        active = str(
            row.get("active", "")
        ).strip().lower()

        if active not in (
            "true",
            "1",
            "yes",
        ):
            continue

        feed_url = str(
            row.get("feed_url", "")
        ).strip()

        if not feed_url:
            print(
                f"Skipping {row.get('name')}: "
                "no feed_url"
            )
            continue

        sources.append(row)

    return sources


# ============================================================
# FETCH RSS
# ============================================================

def fetch_source(source):

    name = source.get(
        "name",
        "Unknown",
    )

    feed_url = source.get(
        "feed_url"
    )

    print(
        f"Fetching: {name}"
    )

    print(
        f"RSS: {feed_url}"
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
            f"Warning: RSS parser "
            f"reported an issue for {name}"
        )

    articles = []

    for entry in feed.entries:

        title = str(
            entry.get(
                "title",
                "",
            )
        ).strip()

        url = str(
            entry.get(
                "link",
                "",
            )
        ).strip()

        if not title or not url:
            continue

        published = (
            entry.get(
                "published",
                ""
            )
            or entry.get(
                "updated",
                ""
            )
        )

        articles.append(
            {
                "article_id": make_article_id(
                    url
                ),
                "source": name,
                "title": title,
                "url": url,
                "published_at": published,
                "topic": source.get(
                    "domain",
                    "",
                ),
                "status": "DISCOVERED",
                "discovered_at": utc_now(),
            }
        )

    print(
        f"Found {len(articles)} articles."
    )

    return articles


# ============================================================
# WRITE ARTICLES
# ============================================================

def save_articles(
    spreadsheet,
    articles,
):

    worksheet = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    existing_records = (
        worksheet.get_all_records()
    )

    existing_ids = {
        str(
            row.get(
                "article_id",
                "",
            )
        )
        for row in existing_records
    }

    new_articles = [
        article
        for article in articles
        if article["article_id"]
        not in existing_ids
    ]

    if not new_articles:

        print(
            "No new articles to save."
        )

        return 0

    rows = []

    for article in new_articles:

        rows.append(
            [
                article["article_id"],
                article["source"],
                article["title"],
                article["url"],
                article["published_at"],
                article["topic"],
                article["status"],
                article["discovered_at"],
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

    sources = get_active_sources(
        spreadsheet
    )

    print(
        f"Active sources: "
        f"{len(sources)}"
    )

    all_articles = []

    for source in sources:

        try:

            articles = fetch_source(
                source
            )

            all_articles.extend(
                articles
            )

        except Exception as e:

            print(
                f"ERROR: "
                f"{source.get('name')}: "
                f"{e}"
            )

    print(
        f"Total articles collected: "
        f"{len(all_articles)}"
    )

    save_articles(
        spreadsheet,
        all_articles,
    )


if __name__ == "__main__":
    main()
