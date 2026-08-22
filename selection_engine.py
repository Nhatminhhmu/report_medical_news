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

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=SCOPES,
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


def parse_int(value, default):

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def generate_run_id():

    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%f%z"
    )


# ============================================================
# SETTINGS
# ============================================================

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

            settings["minimum_score"] = (
                parse_int(
                    value,
                    DEFAULT_MINIMUM_SCORE,
                )
            )

        elif key == "max_articles":

            settings["max_articles"] = (
                parse_int(
                    value,
                    DEFAULT_MAX_ARTICLES,
                )
            )

    return settings


# ============================================================
# ARTICLE SHEET
# ============================================================

def ensure_selection_columns(
    worksheet,
    headers,
):

    required = [
        "selection_status",
        "selection_method",
        "selection_run_id",
    ]

    missing = [
        column
        for column in required
        if column not in headers
    ]

    if not missing:

        return headers

    current_column_count = len(
        headers
    )

    worksheet.add_cols(
        len(missing)
    )

    new_headers = (
        headers
        + missing
    )

    start_column = (
        current_column_count + 1
    )

    end_column = (
        current_column_count
        + len(missing)
    )

    range_name = (
        f"{gspread.utils.rowcol_to_a1(
            1,
            start_column
        )}:"
        f"{gspread.utils.rowcol_to_a1(
            1,
            end_column
        )}"
    )

    worksheet.update(
        range_name=range_name,
        values=[missing],
    )

    print(
        "[SHEET] Added columns: "
        + ", ".join(missing)
    )

    return new_headers


# ============================================================
# SELECTION
# ============================================================

def select_articles(
    spreadsheet,
    minimum_score,
    max_articles,
    run_id,
):

    worksheet = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    records = worksheet.get_all_records()

    if not records:

        print(
            "[SELECTION] No articles found."
        )

        return []

    headers = worksheet.row_values(
        1
    )

    headers = ensure_selection_columns(
        worksheet,
        headers,
    )

    header_index = {
        header: index + 1
        for index, header
        in enumerate(headers)
    }

    required = [
        "article_id",
        "title",
        "rule_score",
    ]

    for column in required:

        if column not in header_index:

            raise ValueError(
                f"Missing Articles column: "
                f"{column}"
            )

    candidates = []

    for row_number, article in enumerate(
        records,
        start=2,
    ):

        score = parse_int(
            article.get(
                "rule_score",
                0,
            ),
            0,
        )

        if score < minimum_score:

            continue

        candidates.append(
            {
                "row_number":
                    row_number,
                "article_id":
                    clean_text(
                        article.get(
                            "article_id"
                        )
                    ),
                "title":
                    clean_text(
                        article.get(
                            "title"
                        )
                    ),
                "score":
                    score,
            }
        )

    print(
        f"[SELECTION] Candidates: "
        f"{len(candidates)}"
    )

    if not candidates:

        print(
            "[SELECTION] No articles "
            "above minimum score."
        )

        return []

    selection_count = min(
        max_articles,
        len(candidates),
    )

    selected = random.SystemRandom().sample(
        candidates,
        selection_count,
    )

    print(
        f"[SELECTION] Randomly selected: "
        f"{selection_count}"
    )

    updates = []

    for article in selected:

        row = article[
            "row_number"
        ]

        status_cell = (
            gspread.utils.rowcol_to_a1(
                row,
                header_index[
                    "selection_status"
                ],
            )
        )

        method_cell = (
            gspread.utils.rowcol_to_a1(
                row,
                header_index[
                    "selection_method"
                ],
            )
        )

        run_cell = (
            gspread.utils.rowcol_to_a1(
                row,
                header_index[
                    "selection_run_id"
                ],
            )
        )

        updates.extend(
            [
                {
                    "range":
                        status_cell,
                    "values":
                        [["SELECTED"]],
                },
                {
                    "range":
                        method_cell,
                    "values":
                        [["RANDOM"]],
                },
                {
                    "range":
                        run_cell,
                    "values":
                        [[run_id]],
                },
            ]
        )

    if updates:

        worksheet.batch_update(
            updates
        )

    print(
        ""
    )

    for article in selected:

        print(
            f"[SELECTED] "
            f"{article['title']} "
            f"(score={article['score']})"
        )

    return selected


# ============================================================
# MAIN
# ============================================================

def main():

    spreadsheet = get_google_client()

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

    run_id = generate_run_id()

    print(
        f"Minimum score: "
        f"{minimum_score}"
    )

    print(
        f"Max articles: "
        f"{max_articles}"
    )

    print(
        f"Selection Run ID: "
        f"{run_id}"
    )

    selected = select_articles(
        spreadsheet,
        minimum_score,
        max_articles,
        run_id,
    )

    print(
        ""
    )

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
        f"Candidates: "
        f"{'N/A' if not selected else 'selected'}"
    )

    print(
        f"Selected: "
        f"{len(selected)}"
    )

    print(
        f"Method: RANDOM"
    )

    print(
        f"Run ID: "
        f"{run_id}"
    )

    print(
        "============================================================"
    )


if __name__ == "__main__":

    main()
