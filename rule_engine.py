import json
import os
import re

import gspread

from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TOPICS_SHEET = "Topics"
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

def clean_text(value):

    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def normalize_text(value):

    value = clean_text(
        value
    )

    value = value.lower()

    return value


def parse_keywords(value):

    if not value:
        return []

    return [
        normalize_text(
            item
        )
        for item in str(
            value
        ).split(",")
        if clean_text(item)
    ]


def is_active(value):

    return (
        str(value)
        .strip()
        .lower()
        in (
            "true",
            "1",
            "yes",
        )
    )


# ============================================================
# READ TOPICS
# ============================================================

def get_active_topics(
    spreadsheet,
):

    worksheet = spreadsheet.worksheet(
        TOPICS_SHEET
    )

    records = worksheet.get_all_records()

    topics = []

    for row in records:

        if not is_active(
            row.get(
                "active",
                "",
            )
        ):
            continue

        topics.append(
            {
                "name": clean_text(
                    row.get(
                        "name",
                        "",
                    )
                ),
                "keywords": parse_keywords(
                    row.get(
                        "keywords",
                        "",
                    )
                ),
                "exclusions": parse_keywords(
                    row.get(
                        "exclusions",
                        "",
                    )
                ),
                "priority": float(
                    row.get(
                        "priority",
                        1,
                    )
                    or 1
                ),
            }
        )

    return topics


# ============================================================
# KEYWORD MATCHING
# ============================================================

def keyword_matches(
    text,
    keyword,
):

    text = normalize_text(
        text
    )

    keyword = normalize_text(
        keyword
    )

    if not keyword:
        return False

    # Phrase matching
    if " " in keyword:

        return keyword in text

    # Word-boundary matching
    return bool(
        re.search(
            r"\b"
            + re.escape(keyword)
            + r"\b",
            text,
        )
    )


# ============================================================
# SCORE ARTICLE
# ============================================================

def evaluate_article(
    article,
    topics,
):

    title = normalize_text(
        article.get(
            "title",
            "",
        )
    )

    excerpt = normalize_text(
        article.get(
            "excerpt",
            "",
        )
    )

    text = (
        title
        + " "
        + excerpt
    )

    results = []

    for topic in topics:

        matched = []

        for keyword in topic[
            "keywords"
        ]:

            if keyword_matches(
                text,
                keyword,
            ):
                matched.append(
                    keyword
                )

        excluded = []

        for keyword in topic[
            "exclusions"
        ]:

            if keyword_matches(
                text,
                keyword,
            ):
                excluded.append(
                    keyword
                )

        # Exclusion overrides positive match
        if excluded:
            matched = []

        if not matched:
            continue

        # ----------------------------------------------------
        # Basic relevance score
        #
        # Title matches are weighted more heavily.
        # ----------------------------------------------------

        title_matches = sum(
            1
            for keyword in matched
            if keyword_matches(
                title,
                keyword,
            )
        )

        excerpt_matches = (
            len(matched)
            - title_matches
        )

        score = (
            title_matches * 25
            + excerpt_matches * 10
            + topic["priority"] * 5
        )

        score = min(
            score,
            100,
        )

        results.append(
            {
                "topic": topic[
                    "name"
                ],
                "score": score,
                "matched_keywords": matched,
            }
        )

    if not results:

        return {
            "topic": "",
            "score": 0,
            "matched_keywords": [],
        }

    # Highest score wins
    results.sort(
        key=lambda item: item[
            "score"
        ],
        reverse=True,
    )

    best = results[0]

    return {
        "topic": best[
            "topic"
        ],
        "score": best[
            "score"
        ],
        "matched_keywords": best[
            "matched_keywords"
        ],
    }


# ============================================================
# PROCESS ARTICLES
# ============================================================

def process_articles(
    spreadsheet,
    topics,
):

    worksheet = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    records = worksheet.get_all_records()

    if not records:

        print(
            "No articles found."
        )

        return

    headers = worksheet.row_values(
        1
    )

    header_index = {
        header: index + 1
        for index, header
        in enumerate(headers)
    }

    required_headers = [
        "article_id",
        "title",
        "excerpt",
        "topic",
        "relevance_score",
        "matched_keywords",
    ]

    for header in required_headers:

        if header not in header_index:

            raise ValueError(
                f"Missing Articles column: "
                f"{header}"
            )

    updates = []

    for row_number, article in enumerate(
        records,
        start=2,
    ):

        # Skip already evaluated articles
        current_score = clean_text(
            article.get(
                "relevance_score",
                "",
            )
        )

        if current_score:
            continue

        result = evaluate_article(
            article,
            topics,
        )

        topic = result[
            "topic"
        ]

        score = result[
            "score"
        ]

        matched_keywords = ", ".join(
            result[
                "matched_keywords"
            ]
        )

        updates.append(
            {
                "range": (
                    f"{gspread.utils.rowcol_to_a1(
                        row_number,
                        header_index["topic"]
                    )}"
                ),
                "values": [
                    [topic]
                ],
            }
        )

        updates.append(
            {
                "range": (
                    f"{gspread.utils.rowcol_to_a1(
                        row_number,
                        header_index[
                            "relevance_score"
                        ]
                    )}"
                ),
                "values": [
                    [score]
                ],
            }
        )

        updates.append(
            {
                "range": (
                    f"{gspread.utils.rowcol_to_a1(
                        row_number,
                        header_index[
                            "matched_keywords"
                        ]
                    )}"
                ),
                "values": [
                    [matched_keywords]
                ],
            }
        )

        print(
            f"{article.get('title', '')[:80]}"
        )

        print(
            f"  Topic: {topic or 'NONE'}"
        )

        print(
            f"  Score: {score}"
        )

        print(
            f"  Keywords: "
            f"{matched_keywords or 'NONE'}"
        )

    if not updates:

        print(
            "No unevaluated articles."
        )

        return

    worksheet.batch_update(
        updates
    )

    print(
        f"Updated "
        f"{len(updates) // 3} articles."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    spreadsheet = get_google_client()

    print(
        f"Connected to: "
        f"{spreadsheet.title}"
    )

    topics = get_active_topics(
        spreadsheet
    )

    print(
        f"Active topics: "
        f"{len(topics)}"
    )

    process_articles(
        spreadsheet,
        topics,
    )


if __name__ == "__main__":
    main()
