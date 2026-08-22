import os
import re
import json
import time
import html
from datetime import datetime, timezone

import requests
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI


# ============================================================
# AI BRIEFING ENGINE
# ============================================================

VERSION = "0.4"

ARTICLES_SHEET = "Articles"
BRIEFINGS_SHEET = "Briefings"
SETTINGS_SHEET = "Settings"

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_LANGUAGE = "Vietnamese"
DEFAULT_MAX_CONTENT_CHARS = 50000

REQUEST_TIMEOUT = 30
OPENAI_RETRY_COUNT = 2
OPENAI_RETRY_DELAY = 3

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0 Safari/537.36"
)


# ============================================================
# GOOGLE SHEETS
# ============================================================

def get_spreadsheet():
    credentials_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    spreadsheet_id = os.environ["GOOGLE_SPREADSHEET_ID"]

    credentials = Credentials.from_service_account_info(
        json.loads(credentials_json),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )

    client = gspread.authorize(credentials)

    spreadsheet = client.open_by_key(spreadsheet_id)

    print(f"Connected to: {spreadsheet.title}")

    return spreadsheet


def get_worksheet(spreadsheet, name):
    return spreadsheet.worksheet(name)


def row_value(row, key, default=""):
    value = row.get(key, default)

    if value is None:
        return default

    return str(value).strip()


# ============================================================
# SETTINGS
# ============================================================

def load_settings(spreadsheet):
    worksheet = get_worksheet(
        spreadsheet,
        SETTINGS_SHEET,
    )

    records = worksheet.get_all_records()

    settings = {}

    for record in records:
        key = str(
            record.get("key", "")
        ).strip()

        if not key:
            continue

        value = record.get("value", "")

        settings[key] = value

    return settings


def setting(settings, key, default=None):
    value = settings.get(key)

    if value is None:
        return default

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return default

    return value


def setting_int(settings, key, default):
    value = setting(
        settings,
        key,
        default,
    )

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = html.unescape(text)

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def html_to_text(content):
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            content,
            "html.parser",
        )

        for tag in soup([
            "script",
            "style",
            "noscript",
            "svg",
            "nav",
            "footer",
            "header",
            "form",
        ]):
            tag.decompose()

        lines = []

        for line in soup.get_text("\n").splitlines():
            line = clean_text(line)

            if line:
                lines.append(line)

        return "\n".join(lines)

    except Exception:
        content = re.sub(
            r"<[^>]+>",
            " ",
            content,
        )

        return clean_text(content)


# ============================================================
# FETCH ARTICLE
# ============================================================

def fetch_article(url):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return html_to_text(
        response.text
    )


# ============================================================
# OPENAI
# ============================================================

def get_openai_client():
    api_key = os.environ.get(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "Missing OPENAI_API_KEY environment variable."
        )

    return OpenAI(
        api_key=api_key
    )


def extract_json(text):
    if not text:
        raise ValueError(
            "OpenAI returned empty response."
        )

    text = text.strip()

    # Remove markdown code fences
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = text.strip()

    # First attempt
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Attempt to extract JSON object
    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        candidate = text[
            start:end + 1
        ]

        try:
            return json.loads(
                candidate
            )
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "OpenAI returned invalid JSON."
    )


def call_openai(
    client,
    model,
    system_prompt,
    user_prompt,
):
    last_error = None

    for attempt in range(
        OPENAI_RETRY_COUNT + 1
    ):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0.2,
                response_format={
                    "type": "json_object"
                },
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
            )

            content = (
                response
                .choices[0]
                .message
                .content
            )

            return extract_json(
                content
            )

        except Exception as exc:
            last_error = exc

            if attempt < OPENAI_RETRY_COUNT:
                time.sleep(
                    OPENAI_RETRY_DELAY
                )
            else:
                raise last_error


# ============================================================
# DEFAULT PROMPT
# ============================================================

DEFAULT_SYSTEM_PROMPT = """
You are a senior healthcare management and
healthcare technology analyst.

Analyze the supplied healthcare article carefully.

Return ONLY valid JSON.

The analysis must be factual and grounded in
the supplied article.

Do not invent facts, numbers, quotes,
organizations, outcomes, or implications.

Write the output in Vietnamese.

Required JSON structure:

{
  "summary": "A concise executive summary.",
  "key_points": [
    "Key point 1",
    "Key point 2",
    "Key point 3"
  ],
  "why_it_matters": "Why this article matters to healthcare leaders.",
  "implications": [
    "Implication 1",
    "Implication 2",
    "Implication 3"
  ]
}

Keep the analysis practical and relevant to
healthcare management, hospital operations,
strategy, digital health, patient experience,
workforce, finance, and clinical operations
when applicable.

Do not force relevance if the article does
not support it.
"""


def build_prompt(
    settings,
    article,
    content,
):
    system_prompt = setting(
        settings,
        "briefing_system_prompt",
        DEFAULT_SYSTEM_PROMPT,
    )

    user_template = setting(
        settings,
        "briefing_user_prompt",
        "",
    )

    variables = {
        "source": article["source"],
        "title": article["title"],
        "url": article["url"],
        "published_at": article["published_at"],
        "rule_score": article["rule_score"],
        "topics": article["topics"],
        "content": content,
    }

    if user_template:
        try:
            user_prompt = user_template.format(
                **variables
            )

        except Exception:
            user_prompt = (
                f"Source: {article['source']}\n"
                f"Title: {article['title']}\n"
                f"URL: {article['url']}\n"
                f"Published at: {article['published_at']}\n"
                f"Rule score: {article['rule_score']}\n"
                f"Topics: {article['topics']}\n\n"
                f"ARTICLE CONTENT:\n{content}"
            )

    else:
        user_prompt = (
            f"Source: {article['source']}\n"
            f"Title: {article['title']}\n"
            f"URL: {article['url']}\n"
            f"Published at: {article['published_at']}\n"
            f"Rule score: {article['rule_score']}\n"
            f"Topics: {article['topics']}\n\n"
            f"ARTICLE CONTENT:\n{content}"
        )

    return system_prompt, user_prompt


# ============================================================
# SHEET SCHEMA
# ============================================================

ARTICLES_HEADERS = [
    "article_id",
    "source",
    "title",
    "url",
    "published_at",
    "excerpt",
    "matched_topics",
    "matched_related_terms",
    "matched_context_terms",
    "rule_score",
    "matched_keywords",
    "status",
    "discovered_at",
]


BRIEFINGS_HEADERS = [
    "article_id",
    "source",
    "title",
    "url",
    "published_at",
    "rule_score",
    "topics",
    "summary",
    "key_points",
    "why_it_matters",
    "implications",
    "model",
    "created_at",
    "status",
]


def validate_articles_schema(
    worksheet
):
    headers = worksheet.row_values(1)

    missing = [
        header
        for header in ARTICLES_HEADERS
        if header not in headers
    ]

    if missing:
        raise ValueError(
            "Articles sheet is missing columns: "
            + ", ".join(missing)
        )


def ensure_briefings_schema(
    worksheet
):
    headers = worksheet.row_values(1)

    if not headers:
        worksheet.update(
            range_name="A1",
            values=[
                BRIEFINGS_HEADERS
            ],
        )
        return

    missing = [
        header
        for header in BRIEFINGS_HEADERS
        if header not in headers
    ]

    if missing:
        raise ValueError(
            "Briefings sheet is missing columns: "
            + ", ".join(missing)
        )


# ============================================================
# ARTICLE LOADING
# ============================================================

def load_selected_articles(
    spreadsheet
):
    """
    Selection is represented directly
    in Articles.status.

    No Selection sheet is required.
    """

    worksheet = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    validate_articles_schema(
        worksheet
    )

    records = worksheet.get_all_records()

    selected = []

    for record in records:
        status = row_value(
            record,
            "status",
        ).upper()

        if status == "SELECTED":
            selected.append(
                record
            )

    return selected


# ============================================================
# ARTICLE NORMALIZATION
# ============================================================

def normalize_article(record):
    """
    Google Sheets:
        matched_topics

    Internal field:
        topics
    """

    return {
        "article_id": row_value(
            record,
            "article_id",
        ),

        "source": row_value(
            record,
            "source",
        ),

        "title": row_value(
            record,
            "title",
        ),

        "url": row_value(
            record,
            "url",
        ),

        "published_at": row_value(
            record,
            "published_at",
        ),

        "rule_score": row_value(
            record,
            "rule_score",
        ),

        "topics": row_value(
            record,
            "matched_topics",
        ),
    }


# ============================================================
# EXISTING BRIEFINGS
# ============================================================

def get_existing_briefing_ids(
    worksheet
):
    records = worksheet.get_all_records()

    ids = set()

    for record in records:
        article_id = row_value(
            record,
            "article_id",
        )

        if article_id:
            ids.add(article_id)

    return ids


# ============================================================
# OUTPUT HELPERS
# ============================================================

def normalize_list(value):
    if value is None:
        return []

    if isinstance(
        value,
        list,
    ):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    if isinstance(
        value,
        str,
    ):
        value = value.strip()

        if not value:
            return []

        return [value]

    return [
        str(value).strip()
    ]


def list_to_sheet_value(
    value
):
    items = normalize_list(
        value
    )

    return "\n".join(
        f"• {item}"
        for item in items
    )


def safe_result_value(
    result,
    key,
    default="",
):
    value = result.get(
        key,
        default,
    )

    if value is None:
        return default

    return value


def create_briefing_row(
    article,
    result,
    model,
):
    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    return [
        article["article_id"],
        article["source"],
        article["title"],
        article["url"],
        article["published_at"],
        article["rule_score"],
        article["topics"],

        str(
            safe_result_value(
                result,
                "summary",
                "",
            )
        ).strip(),

        list_to_sheet_value(
            safe_result_value(
                result,
                "key_points",
                [],
            )
        ),

        str(
            safe_result_value(
                result,
                "why_it_matters",
                "",
            )
        ).strip(),

        list_to_sheet_value(
            safe_result_value(
                result,
                "implications",
                [],
            )
        ),

        model,
        created_at,
        "COMPLETED",
    ]


# ============================================================
# ARTICLE STATUS UPDATE
# ============================================================

def update_article_status(
    worksheet,
    article_id,
    new_status,
):
    headers = worksheet.row_values(1)

    try:
        article_id_col = (
            headers.index("article_id")
            + 1
        )

        status_col = (
            headers.index("status")
            + 1
        )

    except ValueError as exc:
        raise ValueError(
            "Articles sheet is missing "
            "article_id or status column."
        ) from exc

    records = worksheet.get_all_values()

    for row_number, row in enumerate(
        records[1:],
        start=2,
    ):
        if (
            len(row)
            >= article_id_col
            and row[
                article_id_col - 1
            ].strip()
            == article_id
        ):
            cell = gspread.utils.rowcol_to_a1(
                row_number,
                status_col,
            )

            worksheet.update(
                range_name=cell,
                values=[
                    [new_status]
                ],
            )

            return True

    return False


# ============================================================
# PROCESS ARTICLES
# ============================================================

def process_articles(
    spreadsheet,
    settings,
    articles,
):
    articles_ws = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    briefings_ws = spreadsheet.worksheet(
        BRIEFINGS_SHEET
    )

    ensure_briefings_schema(
        briefings_ws
    )

    existing_ids = get_existing_briefing_ids(
        briefings_ws
    )

    model = setting(
        settings,
        "briefing_model",
        DEFAULT_MODEL,
    )

    language = setting(
        settings,
        "briefing_language",
        DEFAULT_LANGUAGE,
    )

    max_content_chars = setting_int(
        settings,
        "briefing_max_content_chars",
        DEFAULT_MAX_CONTENT_CHARS,
    )

    print(
        f"[SETTINGS] model={model}"
    )

    print(
        f"[SETTINGS] "
        f"max_content_chars={max_content_chars}"
    )

    print(
        f"[SETTINGS] language={language}"
    )

    print(
        "[SETTINGS] "
        "prompt=loaded from Settings"
    )

    client = get_openai_client()

    successful = 0
    failed = 0
    skipped = 0

    for record in articles:

        article = normalize_article(
            record
        )

        article_id = article[
            "article_id"
        ]

        if not article_id:
            print(
                "[SKIP] Article without article_id."
            )

            skipped += 1
            continue

        if article_id in existing_ids:
            print(
                f"[SKIP] Already briefed: "
                f"{article['title']}"
            )

            # Keep state consistent
            try:
                update_article_status(
                    articles_ws,
                    article_id,
                    "BRIEFED",
                )
            except Exception as exc:
                print(
                    f"[WARN] Could not update "
                    f"article status: {exc}"
                )

            skipped += 1
            continue

        print(
            f"[BRIEFING] "
            f"{article['title']}"
        )

        try:

            print(
                f"[FETCH] "
                f"{article['url']}"
            )

            content = fetch_article(
                article["url"]
            )

            print(
                f"[FETCH] "
                f"{len(content)} "
                f"characters extracted."
            )

            if not content:
                raise ValueError(
                    "No article content extracted."
                )

            content = content[
                :max_content_chars
            ]

            system_prompt, user_prompt = (
                build_prompt(
                    settings,
                    article,
                    content,
                )
            )

            result = call_openai(
                client=client,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            row = create_briefing_row(
                article,
                result,
                model,
            )

            briefings_ws.append_row(
                row,
                value_input_option="USER_ENTERED",
            )

            # IMPORTANT:
            # Only mark BRIEFED after the briefing
            # has successfully been written.
            update_article_status(
                articles_ws,
                article_id,
                "BRIEFED",
            )

            existing_ids.add(
                article_id
            )

            successful += 1

            print(
                f"[SUCCESS] "
                f"{article['title']}"
            )

        except Exception as exc:

            failed += 1

            print(
                f"[ERROR] "
                f"{article['title']}: "
                f"{exc}"
            )

            # Keep SELECTED so the article
            # can be retried later.

            continue

    return (
        successful,
        failed,
        skipped,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 60
    )

    print(
        f"AI BRIEFING ENGINE v{VERSION}"
    )

    print(
        "=" * 60
    )

    spreadsheet = get_spreadsheet()

    settings = load_settings(
        spreadsheet
    )

    articles = load_selected_articles(
        spreadsheet
    )

    print(
        f"[BRIEFING] "
        f"Selected articles: "
        f"{len(articles)}"
    )

    if not articles:

        print(
            "[BRIEFING] "
            "No selected articles."
        )

        return

    successful, failed, skipped = (
        process_articles(
            spreadsheet=spreadsheet,
            settings=settings,
            articles=articles,
        )
    )

    print(
        "\n"
        + "=" * 60
    )

    print(
        "BRIEFING SUMMARY"
    )

    print(
        "=" * 60
    )

    print(
        f"Selected: {len(articles)}"
    )

    print(
        f"Successful: {successful}"
    )

    print(
        f"Failed: {failed}"
    )

    print(
        f"Skipped: {skipped}"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":
    main()
