"""
Reading Selection Engine v0.1

Purpose:
    Select the most valuable articles from completed AI briefings.

Pipeline:
    Briefings
        ↓
    Reading Selection Engine
        ↓
    Reading Selection
        ↓
    Medical News Report

Important:
    - NO OpenAI call
    - NO additional token cost
    - Selection is based on Reading Score distribution
    - Default method = ELBOW / LARGEST GAP

Selection logic:
    1. Load successful Briefings.
    2. Sort by reading_score DESC.
    3. Calculate adjacent score gaps.
    4. Find the strongest "elbow".
    5. Constrain result to min/max article count.
    6. Write selected articles to Reading Selection.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import gspread
from google.oauth2.service_account import Credentials


# ============================================================
# CONFIG
# ============================================================

VERSION = "0.1"

BRIEFINGS_SHEET = "Briefings"
SELECTION_SHEET = "Reading Selection"
SETTINGS_SHEET = "Settings"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DEFAULT_METHOD = "elbow"
DEFAULT_MIN_ARTICLES = 3
DEFAULT_MAX_ARTICLES = 7
DEFAULT_FALLBACK_ARTICLES = 5

SELECTION_HEADERS = [
    "article_id",
    "source",
    "title",
    "url",
    "published_at",
    "rule_score",
    "reading_score",
    "rank",
    "selected",
    "selection_method",
    "selection_reason",
    "status",
    "created_at",
]


# ============================================================
# HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:

    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def normalize_bool(value: Any) -> bool:
    return clean_text(value).lower() in {
        "true",
        "1",
        "yes",
        "y",
        "active",
    }


# ============================================================
# GOOGLE SHEETS
# ============================================================

def get_spreadsheet():

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
            scopes=GOOGLE_SCOPES,
        )
    )

    client = gspread.authorize(
        credentials
    )

    spreadsheet = client.open_by_key(
        spreadsheet_id
    )

    print(
        f"Connected to: {spreadsheet.title}"
    )

    return spreadsheet


def get_worksheet(
    spreadsheet,
    name: str,
):

    try:
        return spreadsheet.worksheet(name)

    except gspread.WorksheetNotFound:
        return None


# ============================================================
# SETTINGS
# ============================================================

def load_settings(
    spreadsheet,
) -> dict[str, str]:

    worksheet = get_worksheet(
        spreadsheet,
        SETTINGS_SHEET,
    )

    if worksheet is None:
        return {}

    records = worksheet.get_all_records()

    if not records:
        return {}

    first = records[0]

    headers = {
        clean_text(key).lower(): key
        for key in first.keys()
    }

    key_header = None
    value_header = None

    for candidate in [
        "setting_key",
        "key",
        "parameter",
        "setting",
        "name",
    ]:
        if candidate in headers:
            key_header = headers[candidate]
            break

    for candidate in [
        "setting_value",
        "value",
        "parameter_value",
    ]:
        if candidate in headers:
            value_header = headers[candidate]
            break

    if not key_header or not value_header:
        return {}

    settings = {}

    for row in records:

        key = clean_text(
            row.get(key_header)
        ).lower()

        value = clean_text(
            row.get(value_header)
        )

        if key:
            settings[key] = value

    return settings


def get_setting(
    settings: dict,
    key: str,
    default: Any,
):

    value = settings.get(
        key.lower()
    )

    if value in (
        None,
        "",
    ):
        return default

    return value


# ============================================================
# LOAD BRIEFINGS
# ============================================================

def load_briefings(
    spreadsheet,
) -> list[dict]:

    worksheet = get_worksheet(
        spreadsheet,
        BRIEFINGS_SHEET,
    )

    if worksheet is None:
        raise ValueError(
            "Briefings sheet was not found."
        )

    records = worksheet.get_all_records()

    result = []

    for row in records:

        status = clean_text(
            row.get("status")
        ).lower()

        if status != "success":
            continue

        article_id = clean_text(
            row.get("article_id")
        )

        if not article_id:
            continue

        score = safe_float(
            row.get("reading_score")
        )

        result.append(
            {
                "article_id": article_id,
                "source": clean_text(
                    row.get("source")
                ),
                "title": clean_text(
                    row.get("title")
                ),
                "url": clean_text(
                    row.get("url")
                ),
                "published_at": clean_text(
                    row.get("published_at")
                ),
                "rule_score": safe_float(
                    row.get("rule_score")
                ),
                "reading_score": score,
            }
        )

    return result


# ============================================================
# STATISTICAL SELECTION
# ============================================================

def calculate_gaps(
    articles: list[dict],
) -> list[float]:

    gaps = []

    for i in range(
        len(articles) - 1
    ):

        current_score = articles[
            i
        ]["reading_score"]

        next_score = articles[
            i + 1
        ]["reading_score"]

        gap = (
            current_score
            - next_score
        )

        gaps.append(
            round(gap, 4)
        )

    return gaps


def choose_by_elbow(
    articles: list[dict],
    min_articles: int,
    max_articles: int,
) -> tuple[int, dict]:

    count = len(articles)

    if count <= min_articles:
        return (
            count,
            {
                "reason": (
                    f"Only {count} eligible articles; "
                    "all are selected."
                ),
                "elbow_gap": None,
                "elbow_position": None,
            },
        )

    upper = min(
        max_articles,
        count,
    )

    lower = min(
        min_articles,
        upper,
    )

    gaps = calculate_gaps(
        articles
    )

    # --------------------------------------------------------
    # Only evaluate possible cutoffs between min and max.
    #
    # Example:
    # min=3, max=7
    #
    # We can cut after:
    # 3rd, 4th, 5th, 6th, 7th article.
    # --------------------------------------------------------

    candidate_positions = list(
        range(
            lower,
            upper + 1,
        )
    )

    best_position = None
    best_gap = -1.0

    for position in candidate_positions:

        # Gap after Nth article.
        #
        # Python index:
        # position=4 → gap index 3
        gap_index = position - 1

        if gap_index >= len(gaps):
            continue

        gap = gaps[gap_index]

        if gap > best_gap:

            best_gap = gap
            best_position = position

    # --------------------------------------------------------
    # If there is no usable elbow, fallback.
    # --------------------------------------------------------

    if best_position is None:

        fallback = min(
            DEFAULT_FALLBACK_ARTICLES,
            upper,
        )

        fallback = max(
            fallback,
            lower,
        )

        return (
            fallback,
            {
                "reason": (
                    "No valid elbow was found; "
                    f"fallback to {fallback} articles."
                ),
                "elbow_gap": None,
                "elbow_position": None,
            },
        )

    return (
        best_position,
        {
            "reason": (
                f"Selected {best_position} articles "
                f"because the largest score gap "
                f"({best_gap:.2f}) occurs after rank "
                f"{best_position}."
            ),
            "elbow_gap": best_gap,
            "elbow_position": best_position,
        },
    )


# ============================================================
# BUILD SELECTION
# ============================================================

def build_selection(
    articles: list[dict],
    selected_count: int,
    method: str,
    selection_meta: dict,
) -> list[dict]:

    result = []

    for index, article in enumerate(
        articles,
        start=1,
    ):

        selected = (
            index <= selected_count
        )

        if selected:

            reason = (
                f"Rank {index}; "
                f"reading score "
                f"{article['reading_score']:.2f}. "
                f"{selection_meta['reason']}"
            )

        else:

            reason = (
                f"Rank {index}; "
                f"reading score "
                f"{article['reading_score']:.2f}. "
                "Below the statistical selection cutoff."
            )

        result.append(
            {
                **article,
                "rank": index,
                "selected": selected,
                "selection_method": method,
                "selection_reason": reason,
                "status": (
                    "selected"
                    if selected
                    else "not_selected"
                ),
                "created_at": now_iso(),
            }
        )

    return result


# ============================================================
# SHEET OUTPUT
# ============================================================

def ensure_selection_sheet(
    spreadsheet,
):

    worksheet = get_worksheet(
        spreadsheet,
        SELECTION_SHEET,
    )

    if worksheet is None:

        worksheet = spreadsheet.add_worksheet(
            title=SELECTION_SHEET,
            rows=1000,
            cols=len(
                SELECTION_HEADERS
            ),
        )

        worksheet.append_row(
            SELECTION_HEADERS,
            value_input_option="USER_ENTERED",
        )

        return worksheet

    values = worksheet.get_all_values()

    if not values:

        worksheet.append_row(
            SELECTION_HEADERS,
            value_input_option="USER_ENTERED",
        )

        return worksheet

    existing_headers = values[0]

    if existing_headers != SELECTION_HEADERS:

        raise ValueError(
            "Reading Selection header mismatch.\n"
            f"Expected: {SELECTION_HEADERS}\n"
            f"Found: {existing_headers}"
        )

    return worksheet


def write_selection(
    worksheet,
    rows: list[dict],
):

    # Keep the header and replace all old results.
    worksheet.clear()

    worksheet.update(
        range_name="A1",
        values=[
            SELECTION_HEADERS
        ],
    )

    if not rows:
        return

    values = []

    for row in rows:

        values.append(
            [
                row["article_id"],
                row["source"],
                row["title"],
                row["url"],
                row["published_at"],
                row["rule_score"],
                row["reading_score"],
                row["rank"],
                row["selected"],
                row["selection_method"],
                row["selection_reason"],
                row["status"],
                row["created_at"],
            ]
        )

    worksheet.update(
        range_name=f"A2:M{len(values) + 1}",
        values=values,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print(
        f"READING SELECTION ENGINE v{VERSION}"
    )
    print("=" * 60)

    spreadsheet = get_spreadsheet()

    settings = load_settings(
        spreadsheet
    )

    method = clean_text(
        get_setting(
            settings,
            "reading_selection_method",
            DEFAULT_METHOD,
        )
    ).lower()

    min_articles = int(
        safe_float(
            get_setting(
                settings,
                "min_report_articles",
                DEFAULT_MIN_ARTICLES,
            ),
            DEFAULT_MIN_ARTICLES,
        )
    )

    max_articles = int(
        safe_float(
            get_setting(
                settings,
                "max_report_articles",
                DEFAULT_MAX_ARTICLES,
            ),
            DEFAULT_MAX_ARTICLES,
        )
    )

    # Safety normalization.
    min_articles = max(
        1,
        min_articles,
    )

    max_articles = max(
        min_articles,
        max_articles,
    )

    print(
        f"[SETTINGS] method={method}"
    )

    print(
        f"[SETTINGS] min_articles="
        f"{min_articles}"
    )

    print(
        f"[SETTINGS] max_articles="
        f"{max_articles}"
    )

    articles = load_briefings(
        spreadsheet
    )

    print(
        f"[INPUT] Successful briefings: "
        f"{len(articles)}"
    )

    if not articles:

        print(
            "[SELECTION] No successful briefings."
        )

        return

    # Highest score first.
    articles.sort(
        key=lambda x: x[
            "reading_score"
        ],
        reverse=True,
    )

    # --------------------------------------------------------
    # Select
    # --------------------------------------------------------

    if method == "elbow":

        selected_count, meta = (
            choose_by_elbow(
                articles=articles,
                min_articles=min_articles,
                max_articles=max_articles,
            )
        )

    else:

        # Simple deterministic fallback.
        selected_count = min(
            max(
                min_articles,
                DEFAULT_FALLBACK_ARTICLES,
            ),
            max_articles,
            len(articles),
        )

        meta = {
            "reason": (
                f"Method '{method}' is not implemented; "
                f"fallback to top {selected_count}."
            ),
            "elbow_gap": None,
            "elbow_position": None,
        }

    rows = build_selection(
        articles=articles,
        selected_count=selected_count,
        method=method,
        selection_meta=meta,
    )

    worksheet = ensure_selection_sheet(
        spreadsheet
    )

    write_selection(
        worksheet,
        rows,
    )

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    print()
    print("=" * 60)
    print("READING SELECTION SUMMARY")
    print("=" * 60)

    print(
        f"Method: {method.upper()}"
    )

    print(
        f"Eligible: {len(articles)}"
    )

    print(
        f"Selected: {selected_count}"
    )

    if meta.get("elbow_gap") is not None:

        print(
            f"Elbow gap: "
            f"{meta['elbow_gap']:.2f}"
        )

        print(
            f"Elbow position: "
            f"{meta['elbow_position']}"
        )

    print()

    for row in rows:

        marker = (
            "[SELECTED]"
            if row["selected"]
            else "[SKIPPED]"
        )

        print(
            f"{marker} "
            f"#{row['rank']} "
            f"{row['title']} "
            f"(score={row['reading_score']:.2f})"
        )

    print("=" * 60)


if __name__ == "__main__":
    main()
