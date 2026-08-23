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
                "context_terms": parse_keywords(
                    row.get(
                        "context_terms",
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


# ============================================================
# RULE ENGINE SETTINGS
# ============================================================

DEFAULT_RULE_SETTINGS = {
    "rule_engine_version": "1.3",
    "keyword_title_weight": 30,
    "keyword_excerpt_weight": 15,
    "related_title_weight": 10,
    "related_excerpt_weight": 5,
    "context_title_weight": 8,
    "context_excerpt_weight": 3,
    "priority_weight": 5,
    "max_rule_score": 100,
    "minimum_score": 70,
    "require_context_for_related_only": True,
}


def parse_bool_setting(value, default):
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    return default


def parse_number_setting(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_rule_settings(spreadsheet):
    try:
        worksheet = spreadsheet.worksheet("Settings")
        rows = worksheet.get_all_records()
    except Exception as exc:
        print(f"[SETTINGS] Warning: could not load Settings: {exc}")
        return DEFAULT_RULE_SETTINGS.copy()

    settings = DEFAULT_RULE_SETTINGS.copy()

    for row in rows:
        key = str(row.get("key", "")).strip()
        if key not in settings:
            continue

        raw_value = row.get("value")

        if key == "rule_engine_version":
            value = str(raw_value).strip()
            if value:
                settings[key] = value
        elif key == "require_context_for_related_only":
            settings[key] = parse_bool_setting(raw_value, settings[key])
        else:
            settings[key] = parse_number_setting(raw_value, settings[key])

    for key in (
        "keyword_title_weight",
        "keyword_excerpt_weight",
        "related_title_weight",
        "related_excerpt_weight",
        "context_title_weight",
        "context_excerpt_weight",
        "priority_weight",
        "max_rule_score",
        "minimum_score",
    ):
        settings[key] = int(settings[key])

    print(f"[SETTINGS] Rule Engine v{settings['rule_engine_version']}")
    print(
        "[SETTINGS] "
        f"keyword={settings['keyword_title_weight']}/"
        f"{settings['keyword_excerpt_weight']}, "
        f"related={settings['related_title_weight']}/"
        f"{settings['related_excerpt_weight']}, "
        f"context={settings['context_title_weight']}/"
        f"{settings['context_excerpt_weight']}, "
        f"priority={settings['priority_weight']}, "
        f"max={settings['max_rule_score']}, "
        f"minimum={settings['minimum_score']}, "
        f"require_context={settings['require_context_for_related_only']}"
    )
    return settings

def evaluate_article(
    article,
    topics,
    settings,
):
    """
    Rule Engine v1.2.

    Primary keyword: title +30, excerpt +15
    Related term:    title +10, excerpt +5
    Context term:    title +8, excerpt +3
    Priority bonus:  +5 * priority

    A topic matches when it has either:
      1) a primary keyword, OR
      2) a related term AND a context term.

    Exclusions override all positive matches.
    """
    title = normalize_text(article.get("title", ""))
    excerpt = normalize_text(article.get("excerpt", ""))
    text = title + " " + excerpt

    matched_topics = []
    all_keywords = []
    all_related_terms = []
    all_context_terms = []

    for topic in topics:
        keywords = [
            k for k in topic.get("keywords", [])
            if keyword_matches(text, k)
        ]
        related = [
            k for k in topic.get("related_terms", [])
            if keyword_matches(text, k)
        ]
        context = [
            k for k in topic.get("context_terms", [])
            if keyword_matches(text, k)
        ]
        excluded = [
            k for k in topic.get("exclusions", [])
            if keyword_matches(text, k)
        ]

        if excluded:
            continue

        if not keywords:
            if settings["require_context_for_related_only"]:
                if not (related and context):
                    continue
            elif not related:
                continue

        kt = sum(keyword_matches(title, k) for k in keywords)
        ke = len(keywords) - kt
        rt = sum(keyword_matches(title, k) for k in related)
        re_ = len(related) - rt
        ct = sum(keyword_matches(title, k) for k in context)
        ce = len(context) - ct

        score = min(
            kt * settings["keyword_title_weight"]
            + ke * settings["keyword_excerpt_weight"]
            + rt * settings["related_title_weight"]
            + re_ * settings["related_excerpt_weight"]
            + ct * settings["context_title_weight"]
            + ce * settings["context_excerpt_weight"]
            + topic.get("priority", 1) * settings["priority_weight"],
            settings["max_rule_score"],
        )

        matched_topics.append({
            "name": topic.get("name", ""),
            "score": score,
        })
        all_keywords.extend(keywords)
        all_related_terms.extend(related)
        all_context_terms.extend(context)

    if not matched_topics:
        return {
            "matched_topics": [],
            "rule_score": 0,
            "matched_keywords": [],
            "matched_related_terms": [],
            "matched_context_terms": [],
        }

    matched_topics.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    return {
        "matched_topics": [
            item["name"] for item in matched_topics
        ],
        "rule_score": matched_topics[0]["score"],
        "matched_keywords": list(dict.fromkeys(all_keywords)),
        "matched_related_terms": list(dict.fromkeys(all_related_terms)),
        "matched_context_terms": list(dict.fromkeys(all_context_terms)),
    }


# ============================================================
# PROCESS ARTICLES
# ============================================================

def process_articles(
    spreadsheet,
    topics,
    settings,
):
    """
    Evaluate Articles using header names rather than fixed column positions.

    Collector-owned and Rule Engine-owned columns may therefore be moved
    independently in Google Sheets without breaking the engine.
    """

    worksheet = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    records = worksheet.get_all_records()

    if not records:
        print("No articles found.")
        return

    headers = worksheet.row_values(1)

    # Column order is intentionally irrelevant.
    header_index = {
        clean_text(header): index + 1
        for index, header in enumerate(headers)
        if clean_text(header)
    }

    required_headers = [
        "article_id",
        "run_id",
        "title",
        "excerpt",
        "matched_topics",
        "rule_score",
        "matched_keywords",
        "matched_related_terms",
        "matched_context_terms",
    ]

    missing = [
        header
        for header in required_headers
        if header not in header_index
    ]

    if missing:
        raise ValueError(
            "Missing Articles column(s): "
            + ", ".join(missing)
        )

    print(
        "[RUN] Rule Engine preserves existing run_id values."
    )

    updates = []
    evaluated_count = 0
    skipped_count = 0

    for row_number, article in enumerate(
        records,
        start=2,
    ):
        article_id = clean_text(
            article.get("article_id", "")
        )
        run_id = clean_text(
            article.get("run_id", "")
        )
        current_score = clean_text(
            article.get("rule_score", "")
        )

        if not article_id:
            skipped_count += 1
            print(
                f"[SKIP] Row {row_number}: missing article_id."
            )
            continue

        if not run_id:
            skipped_count += 1
            print(
                f"[SKIP] {article.get('title', '')[:80]}: "
                "missing run_id."
            )
            continue

        # A non-empty score means this article has already been evaluated.
        # This also correctly treats 0 as an evaluated score.
        if current_score:
            skipped_count += 1
            continue

        result = evaluate_article(
            article,
            topics,
            settings,
        )

        values_by_header = {
            "matched_topics": ", ".join(
                result["matched_topics"]
            ),
            "rule_score": result["rule_score"],
            "matched_keywords": ", ".join(
                result["matched_keywords"]
            ),
            "matched_related_terms": ", ".join(
                result["matched_related_terms"]
            ),
            "matched_context_terms": ", ".join(
                result["matched_context_terms"]
            ),
        }

        for header, value in values_by_header.items():
            cell = gspread.utils.rowcol_to_a1(
                row_number,
                header_index[header],
            )
            updates.append(
                {
                    "range": cell,
                    "values": [[value]],
                }
            )

        evaluated_count += 1

        print(
            f"{article.get('title', '')[:80]}"
        )
        print(
            f"  Run ID: {run_id}"
        )
        print(
            f"  Topics: "
            f"{values_by_header['matched_topics'] or 'NONE'}"
        )
        print(
            f"  Rule score: "
            f"{values_by_header['rule_score']}"
        )
        print(
            f"  Keywords: "
            f"{values_by_header['matched_keywords'] or 'NONE'}"
        )
        print(
            f"  Related terms: "
            f"{values_by_header['matched_related_terms'] or 'NONE'}"
        )
        print(
            f"  Context terms: "
            f"{values_by_header['matched_context_terms'] or 'NONE'}"
        )

    if not updates:
        print("No unevaluated articles.")
        return

    worksheet.batch_update(
        updates
    )

    print(
        f"Updated {evaluated_count} articles."
    )

    if skipped_count:
        print(
            f"Skipped {skipped_count} articles."
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

    settings = load_rule_settings(
        spreadsheet
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
        settings,
    )


if __name__ == "__main__":
    main()
