import json
import os
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlparse

import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from openai import OpenAI


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

ARTICLES_SHEET = "Articles"
BRIEFINGS_SHEET = "Briefings"
SETTINGS_SHEET = "Settings"

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_MAX_CONTENT_CHARS = 50000
DEFAULT_LANGUAGE = "Vietnamese"

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; ReportMedicalNews/1.0)"
    )
}


def clean_text(value):
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


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
        Credentials
        .from_service_account_info(
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


def get_settings(spreadsheet):
    settings = {
        "briefing_model": DEFAULT_MODEL,
        "briefing_max_content_chars":
            DEFAULT_MAX_CONTENT_CHARS,
        "briefing_language":
            DEFAULT_LANGUAGE,
    }

    try:
        worksheet = spreadsheet.worksheet(
            SETTINGS_SHEET
        )

        records = worksheet.get_all_records()

        for row in records:
            key = clean_text(
                row.get("key")
            )

            value = clean_text(
                row.get("value")
            )

            if key in settings and value:
                settings[key] = value

    except Exception as exc:
        print(
            f"[SETTINGS] Warning: {exc}"
        )

    try:
        settings[
            "briefing_max_content_chars"
        ] = int(
            settings[
                "briefing_max_content_chars"
            ]
        )
    except (TypeError, ValueError):
        settings[
            "briefing_max_content_chars"
        ] = DEFAULT_MAX_CONTENT_CHARS

    return settings


def get_articles(worksheet):
    return worksheet.get_all_records()


def get_briefings(worksheet):
    return worksheet.get_all_records()


def extract_article_content(
    url,
    max_chars,
):
    print(
        f"[FETCH] {url}"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    # Remove obvious non-content elements.
    for tag in soup([
        "script",
        "style",
        "noscript",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "iframe",
        "svg",
    ]):
        tag.decompose()

    candidates = []

    # Prefer semantic article containers.
    selectors = [
        "article",
        '[role="main"]',
        "main",
        ".article-body",
        ".article-content",
        ".entry-content",
        ".post-content",
        ".story-body",
        ".content-body",
    ]

    for selector in selectors:
        for node in soup.select(
            selector
        ):
            text = clean_text(
                node.get_text(
                    " ",
                    strip=True,
                )
            )

            if len(text) > 300:
                candidates.append(text)

    if candidates:
        content = max(
            candidates,
            key=len,
        )
    else:
        paragraphs = []

        for paragraph in soup.find_all(
            "p"
        ):
            text = clean_text(
                paragraph.get_text(
                    " ",
                    strip=True,
                )
            )

            if len(text) >= 40:
                paragraphs.append(text)

        content = "\n\n".join(
            paragraphs
        )

    content = unescape(content)

    # Remove excessive whitespace.
    content = re.sub(
        r"\n{3,}",
        "\n\n",
        content,
    )

    content = clean_text(
        content
    )

    if not content:
        raise ValueError(
            "Could not extract article content."
        )

    if len(content) > max_chars:
        print(
            f"[FETCH] Content truncated: "
            f"{len(content)} -> "
            f"{max_chars} chars"
        )

        content = content[
            :max_chars
        ]

    return content


def build_prompt(
    article,
    content,
    language,
):
    title = clean_text(
        article.get("title")
    )

    source = clean_text(
        article.get("source")
    )

    topics = clean_text(
        article.get("matched_topics")
    )

    rule_score = clean_text(
        article.get("rule_score")
    )

    return f"""
You are producing a healthcare intelligence briefing.

Read the FULL ARTICLE CONTENT below.

Your task is to summarize and analyze ONLY what is supported
by the article.

Language: {language}

Article metadata:
Source: {source}
Title: {title}
Topics: {topics}
Rule score: {rule_score}

Requirements:

1. summary
Write a concise 2–4 sentence summary of what the article says.

2. key_points
Identify 3–5 important factual points from the article.

3. why_it_matters
Explain why this article may matter to a healthcare
management, hospital operations, healthcare business,
digital health, workforce, patient experience, quality,
or strategy reader.

4. implications
Identify practical implications for healthcare organizations
ONLY when they are reasonably supported by the article.
Do not invent facts, numbers, recommendations, or outcomes.

If no clear operational implication can be identified, return:
"No clear operational implication identified from the article."

Important:
- Do not fabricate information.
- Do not infer facts that are not in the article.
- Distinguish interpretation from facts.
- Do not mention that you are an AI.
- Do not include markdown headings inside individual fields.

FULL ARTICLE CONTENT:
{content}
""".strip()


def generate_briefing(
    client,
    model,
    article,
    content,
    language,
):
    prompt = build_prompt(
        article,
        content,
        language,
    )

    response = client.responses.create(
        model=model,
        input=prompt,
    )

    text = response.output_text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ValueError(
            "OpenAI returned invalid JSON."
        )


def ensure_briefing_columns(
    worksheet
):
    expected = [
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

    headers = worksheet.row_values(1)

    if not headers:
        worksheet.update(
            "A1",
            [expected],
        )
        return expected

    missing = [
        column
        for column in expected
        if column not in headers
    ]

    if missing:
        raise ValueError(
            "Briefings is missing columns: "
            + ", ".join(missing)
        )

    return headers


def briefing_exists(
    briefings,
    article_id,
):
    for row in briefings:
        if clean_text(
            row.get("article_id")
        ) == article_id:
            return True

    return False


def append_briefing(
    worksheet,
    briefing,
):
    headers = worksheet.row_values(1)

    row = []

    for column in headers:
        row.append(
            briefing.get(
                column,
                "",
            )
        )

    worksheet.append_row(
        row,
        value_input_option="USER_ENTERED",
    )


def update_article_status(
    worksheet,
    records,
    article_id,
    new_status,
):
    headers = worksheet.row_values(1)

    if "status" not in headers:
        raise ValueError(
            "Articles sheet has no status column."
        )

    status_column = (
        headers.index("status") + 1
    )

    for row_number, article in enumerate(
        records,
        start=2,
    ):
        current_id = clean_text(
            article.get("article_id")
        )

        if current_id != article_id:
            continue

        cell = gspread.utils.rowcol_to_a1(
            row_number,
            status_column,
        )

        worksheet.update(
            range_name=cell,
            values=[[new_status]],
        )

        return


def main():
    print(
        "============================================================"
    )
    print(
        "AI BRIEFING ENGINE v0.1"
    )
    print(
        "============================================================"
    )

    spreadsheet = get_spreadsheet()

    print(
        f"Connected to: "
        f"{spreadsheet.title}"
    )

    settings = get_settings(
        spreadsheet
    )

    model = settings[
        "briefing_model"
    ]

    max_chars = settings[
        "briefing_max_content_chars"
    ]

    language = settings[
        "briefing_language"
    ]

    print(
        f"[SETTINGS] model={model}"
    )

    print(
        f"[SETTINGS] "
        f"max_content_chars={max_chars}"
    )

    print(
        f"[SETTINGS] "
        f"language={language}"
    )

    articles_ws = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    briefings_ws = spreadsheet.worksheet(
        BRIEFINGS_SHEET
    )

    articles = get_articles(
        articles_ws
    )

    briefings = get_briefings(
        briefings_ws
    )

    ensure_briefing_columns(
        briefings_ws
    )

    selected = [
        article
        for article in articles
        if clean_text(
            article.get("status")
        ).upper()
        == "SELECTED"
    ]

    print(
        f"[BRIEFING] Selected articles: "
        f"{len(selected)}"
    )

    if not selected:
        print(
            "[BRIEFING] "
            "No selected articles."
        )
        return

    api_key = os.environ.get(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured."
        )

    client = OpenAI(
        api_key=api_key
    )

    successful = 0
    failed = 0

    for article in selected:
        article_id = clean_text(
            article.get("article_id")
        )

        title = clean_text(
            article.get("title")
        )

        print()
        print(
            f"[BRIEFING] {title}"
        )

        if briefing_exists(
            briefings,
            article_id,
        ):
            print(
                "[BRIEFING] "
                "Already exists. Skipping."
            )
            continue

        try:
            url = clean_text(
                article.get("url")
            )

            content = extract_article_content(
                url,
                max_chars,
            )

            print(
                f"[FETCH] "
                f"{len(content)} characters extracted."
            )

            result = generate_briefing(
                client,
                model,
                article,
                content,
                language,
            )

            created_at = (
                datetime.now(
                    timezone.utc
                )
                .isoformat()
            )

            briefing = {
                "article_id": article_id,
                "source": clean_text(
                    article.get("source")
                ),
                "title": title,
                "url": url,
                "published_at": clean_text(
                    article.get(
                        "published_at"
                    )
                ),
                "rule_score": clean_text(
                    article.get(
                        "rule_score"
                    )
                ),
                "topics": clean_text(
                    article.get(
                        "matched_topics"
                    )
                ),
                "summary": result.get(
                    "summary",
                    "",
                ),
                "key_points": result.get(
                    "key_points",
                    "",
                ),
                "why_it_matters": result.get(
                    "why_it_matters",
                    "",
                ),
                "implications": result.get(
                    "implications",
                    "",
                ),
                "model": model,
                "created_at": created_at,
                "status": "COMPLETED",
            }

            append_briefing(
                briefings_ws,
                briefing,
            )

            update_article_status(
                articles_ws,
                articles,
                article_id,
                "BRIEFED",
            )

            briefings.append(
                briefing
            )

            successful += 1

            print(
                "[BRIEFING] "
                "Completed."
            )

        except Exception as exc:
            failed += 1

            print(
                f"[ERROR] "
                f"{title}: {exc}"
            )

            update_article_status(
                articles_ws,
                articles,
                article_id,
                "BRIEFING_ERROR",
            )

    print()
    print(
        "============================================================"
    )
    print(
        "BRIEFING SUMMARY"
    )
    print(
        "============================================================"
    )
    print(
        f"Selected: {len(selected)}"
    )
    print(
        f"Successful: {successful}"
    )
    print(
        f"Failed: {failed}"
    )
    print(
        "============================================================"
    )


if __name__ == "__main__":
    main()
