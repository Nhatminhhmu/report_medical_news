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
                "related_terms": parse_keywords(
                    row.get(
                        "related_terms",
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
    """
    Evaluate an article against active topics.

    Scoring model:
      - Exact keyword in title:       30 points
      - Exact keyword in excerpt:     15 points
      - Related term in title:       18 points
      - Related term in excerpt:      8 points
      - Topic priority bonus:        5 * priority

    The highest topic score becomes the article's rule_score.

    Exclusions override both keywords and related terms.
    """

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

    matched_topics = []
    all_keywords = []
    all_related_terms = []

    for topic in topics:

        keyword_matches_found = []
        related_matches_found = []

        # Primary/high-confidence signals.
        for keyword in topic.get(
            "keywords",
            [],
        ):
            if keyword_matches(
                text,
                keyword,
            ):
                keyword_matches_found.append(
                    keyword
                )

        # Secondary/contextual signals.
        for term in topic.get(
            "related_terms",
            [],
        ):
            if keyword_matches(
                text,
                term,
            ):
                related_matches_found.append(
                    term
                )

        excluded = []

        for keyword in topic.get(
            "exclusions",
            [],
        ):
            if keyword_matches(
                text,
                keyword,
            ):
                excluded.append(
                    keyword
                )

        # Exclusion overrides all positive signals.
        if excluded:
            keyword_matches_found = []
            related_matches_found = []

        if (
            not keyword_matches_found
            and not related_matches_found
        ):
            continue

        keyword_title_matches = sum(
            1
            for keyword
            in keyword_matches_found
            if keyword_matches(
                title,
                keyword,
            )
        )

        keyword_excerpt_matches = (
            len(keyword_matches_found)
            - keyword_title_matches
        )

        related_title_matches = sum(
            1
            for term
            in related_matches_found
            if keyword_matches(
                title,
                term,
            )
        )

        related_excerpt_matches = (
            len(related_matches_found)
            - related_title_matches
        )

        topic_score = (
            keyword_title_matches * 30
            + keyword_excerpt_matches * 15
            + related_title_matches * 18
            + related_excerpt_matches * 8
            + topic.get(
                "priority",
                1,
            ) * 5
        )

        topic_score = min(
            topic_score,
            100,
        )

        matched_topics.append(
            {
                "name": topic.get(
                    "name",
                    "",
                ),
                "score": topic_score,
            }
        )

        all_keywords.extend(
            keyword_matches_found
        )

        all_related_terms.extend(
            related_matches_found
        )

    if not matched_topics:
        return {
            "matched_topics": [],
            "rule_score": 0,
            "matched_keywords": [],
            "matched_related_terms": [],
        }

    # Highest topic score determines overall article score.
    matched_topics.sort(
        key=lambda item: item[
            "score"
        ],
        reverse=True,
    )

    best_score = matched_topics[0][
        "score"
    ]

    all_keywords = list(
        dict.fromkeys(
            all_keywords
        )
    )

    all_related_terms = list(
        dict.fromkeys(
            all_related_terms
        )
    )

    return {
        "matched_topics": [
            item["name"]
            for item in matched_topics
        ],
        "rule_score": best_score,
        "matched_keywords": all_keywords,
        "matched_related_terms": all_related_terms,
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
        "matched_topics",
        "rule_score",
        "matched_keywords",
        "matched_related_terms",
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
                "rule_score",
                "",
            )
        )
        
        if current_score:
            continue

        result = evaluate_article(
            article,
            topics,
        )

        matched_topics = ", ".join(
            result[
                "matched_topics"
            ]
        )
        
        score = result[
            "rule_score"
        ]
        
        matched_keywords = ", ".join(
            result[
                "matched_keywords"
            ]
        )

        matched_related_terms = ", ".join(
            result[
                "matched_related_terms"
            ]
        )

        updates.append(
            {
                "range": (
                    f"{gspread.utils.rowcol_to_a1(
                        row_number,
                        header_index[
                            "matched_topics"
                        ]
                    )}"
                ),
                "values": [
                    [matched_topics]
                ],
            }
        )

        updates.append(
            {
                "range": (
                    f"{gspread.utils.rowcol_to_a1(
                        row_number,
                        header_index[
                            "rule_score"
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

        updates.append(
            {
                "range": (
                    f"{gspread.utils.rowcol_to_a1(
                        row_number,
                        header_index[
                            "matched_related_terms"
                        ]
                    )}"
                ),
                "values": [
                    [matched_related_terms]
                ],
            }
        )

        print(
            f"{article.get('title', '')[:80]}"
        )

        print(
            f"  Topics: "
            f"{matched_topics or 'NONE'}"
        )
        
        print(
            f"  Rule score: "
            f"{score}"
        )
        
        print(
            f"  Keywords: "
            f"{matched_keywords or 'NONE'}"
        )

        print(
            f"  Related terms: "
            f"{matched_related_terms or 'NONE'}"
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
        f"{len(updates) // 4} articles."
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
