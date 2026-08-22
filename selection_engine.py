import json
import os
import random
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

ARTICLES_SHEET = "Articles"
SETTINGS_SHEET = "Settings"

DEFAULT_MINIMUM_SCORE = 70
DEFAULT_MAX_ARTICLES = 5


def get_spreadsheet():
    credentials_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    spreadsheet_id = os.environ["GOOGLE_SPREADSHEET_ID"]

    credentials_info = json.loads(credentials_json)

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=SCOPES,
    )

    client = gspread.authorize(credentials)

    return client.open_by_key(spreadsheet_id)


def clean_text(value):
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def parse_int(value, default):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def get_settings(spreadsheet):
    try:
        worksheet = spreadsheet.worksheet(
            SETTINGS_SHEET
        )

        records = worksheet.get_all_records()

    except Exception as exc:
        print(
            f"[SETTINGS] Warning: {exc}"
        )

        return {
            "minimum_score":
                DEFAULT_MINIMUM_SCORE,
            "max_articles":
                DEFAULT_MAX_ARTICLES,
        }

    settings = {
        "minimum_score":
            DEFAULT_MINIMUM_SCORE,
        "max_articles":
            DEFAULT_MAX_ARTICLES,
    }

    for row in records:

        key = clean_text(
            row.get("key")
        )

        value = row.get("value")

        if key == "minimum_score":
            settings["minimum_score"] = parse_int(
                value,
                DEFAULT_MINIMUM_SCORE,
            )

        elif key == "max_articles":
            settings["max_articles"] = parse_int(
                value,
                DEFAULT_MAX_ARTICLES,
            )

    return settings


def get_articles(worksheet):
    records = worksheet.get_all_records()

    headers = worksheet.row_values(1)

    required_columns = [
        "article_id",
        "title",
        "rule_score",
        "status",
    ]

    missing = [
        column
        for column in required_columns
        if column not in headers
    ]

    if missing:
        raise ValueError(
            "Missing Articles columns: "
            + ", ".join(missing)
        )

    return records, headers


def select_random_articles(
    records,
    minimum_score,
    max_articles,
):
    candidates = []

    for article in records:

        score = parse_int(
            article.get(
                "rule_score",
                0,
            ),
            0,
        )

        status = clean_text(
            article.get(
                "status",
                ""
            )
        ).upper()

        # Only articles that passed the rule threshold.
        if score < minimum_score:
            continue

        # Do not select an article that is
        # already selected/processed.
        if status in {
            "SELECTED",
            "BRIEFED",
            "SENT",
        }:
            continue

        candidates.append(article)

    print(
        f"[SELECTION] Candidates: "
        f"{len(candidates)}"
    )

    if not candidates:
        return []

    count = min(
        max_articles,
        len(candidates),
    )

    selected = random.SystemRandom().sample(
        candidates,
        count,
    )

    return selected


def update_status(
    worksheet,
    headers,
    selected,
):
    status_column = headers.index(
        "status"
    ) + 1

    article_id_column = headers.index(
        "article_id"
    ) + 1

    records = worksheet.get_all_records()

    selected_ids = {
        clean_text(
            article.get(
                "article_id"
            )
        )
        for article in selected
    }

    updates = []

    for row_number, article in enumerate(
        records,
        start=2,
    ):
        article_id = clean_text(
            article.get(
                "article_id"
            )
        )

        if article_id not in selected_ids:
            continue

        status_cell = (
            gspread.utils.rowcol_to_a1(
                row_number,
                status_column,
            )
        )

        updates.append(
            {
                "range": status_cell,
                "values": [["SELECTED"]],
            }
        )

    if updates:
        worksheet.batch_update(
            updates
        )


def main():

    spreadsheet = get_spreadsheet()

    print(
        f"Connected to: "
        f"{spreadsheet.title}"
    )

    settings = get_settings(
        spreadsheet
    )

    minimum_score = settings[
        "minimum_score"
    ]

    max_articles = settings[
        "max_articles"
    ]

    print(
        f"Minimum score: "
        f"{minimum_score}"
    )

    print(
        f"Max articles: "
        f"{max_articles}"
    )

    worksheet = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    records, headers = get_articles(
        worksheet
    )

    selected = select_random_articles(
        records,
        minimum_score,
        max_articles,
    )

    if not selected:
        print(
            "[SELECTION] "
            "No eligible articles."
        )
        return

    update_status(
        worksheet,
        headers,
        selected,
    )

    print()
    print(
        "============================================================"
    )
    print(
        "SELECTION SUMMARY"
    )
    print(
        "============================================================"
    )

    print(
        f"Method: RANDOM"
    )

    print(
        f"Selected: "
        f"{len(selected)}"
    )

    for article in selected:

        title = clean_text(
            article.get("title")
        )

        score = parse_int(
            article.get("rule_score"),
            0,
        )

        print(
            f"[SELECTED] "
            f"{title} "
            f"(score={score})"
        )

    print(
        "============================================================"
    )


if __name__ == "__main__":
    main()
