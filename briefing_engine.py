from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from openai import OpenAI


# ============================================================
# CONFIG
# ============================================================

VERSION = "0.7.2"

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_LANGUAGE = "Vietnamese"
DEFAULT_MAX_CONTENT_CHARS = 30000
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_MAX_OUTPUT_TOKENS = 1400

BRIEFINGS_SHEET = "Briefings"
ARTICLES_SHEET = "Articles"
SELECTION_SHEET = "Selection"
METRICS_SHEET = "Reading Metrics"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0 Safari/537.36"
)

REQUEST_TIMEOUT = 20


# ============================================================
# BRIEFINGS SCHEMA
# ============================================================

BRIEFING_HEADERS = [
    "article_id",
    "run_id",
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

    "metric_1_score",
    "metric_1_reason",
    "metric_2_score",
    "metric_2_reason",
    "metric_3_score",
    "metric_3_reason",
    "metric_4_score",
    "metric_4_reason",
    "metric_5_score",
    "metric_5_reason",

    "reading_score",
    "report_selected",
    "report_rank",
    "report_selection_method",
    "model",
    "created_at",
    "status",
]


# ============================================================
# HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_bool(value: Any) -> bool:
    return str(value).strip().lower() in {
        "true",
        "1",
        "yes",
        "y",
        "active",
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\x00", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def truncate_text(text: str, max_chars: int) -> str:
    text = clean_text(text)

    if len(text) <= max_chars:
        return text

    # Keep a little room for a clear truncation marker.
    return text[: max_chars - 80].rstrip() + "\n\n[CONTENT TRUNCATED]"


def parse_json_safely(text: str) -> dict:
    """
    Structured Outputs should already return valid JSON.
    This fallback exists only for defensive robustness.
    """

    if not text:
        raise ValueError("OpenAI returned empty output.")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Defensive extraction if SDK returns surrounding text.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)

    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError("OpenAI returned invalid JSON.")


# ============================================================
# GOOGLE SHEETS
# ============================================================

def get_spreadsheet():
    credentials_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    spreadsheet_id = os.environ["GOOGLE_SPREADSHEET_ID"]

    credentials_info = json.loads(credentials_json)

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=GOOGLE_SCOPES,
    )

    client = gspread.authorize(credentials)

    spreadsheet = client.open_by_key(spreadsheet_id)

    print(f"Connected to: {spreadsheet.title}")

    return spreadsheet


def get_worksheet(spreadsheet, name: str):
    try:
        return spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        return None


def get_records(worksheet):
    if worksheet is None:
        return []

    return worksheet.get_all_records()


# ============================================================
# SETTINGS
# ============================================================

def load_settings(spreadsheet) -> dict[str, str]:
    """
    Supports common Settings layouts.

    Preferred:
        setting_key | setting_value

    Also accepts:
        key | value
        parameter | value

    The function intentionally does not hard-code the rest of
    the application configuration.
    """

    worksheet = get_worksheet(spreadsheet, "Settings")

    if worksheet is None:
        print("[SETTINGS] Settings sheet not found; using defaults.")
        return {}

    records = worksheet.get_all_records()

    if not records:
        return {}

    headers = [
        clean_text(h).lower()
        for h in records[0].keys()
    ]

    key_candidates = [
        "setting_key",
        "key",
        "parameter",
        "setting",
        "name",
    ]

    value_candidates = [
        "setting_value",
        "value",
        "parameter_value",
    ]

    key_column = next(
        (x for x in key_candidates if x in headers),
        None,
    )

    value_column = next(
        (x for x in value_candidates if x in headers),
        None,
    )

    if not key_column or not value_column:
        print(
            "[SETTINGS] Could not identify key/value columns; "
            "using defaults."
        )
        return {}

    # Map original headers back to exact names.
    original_headers = list(records[0].keys())

    key_header = next(
        h for h in original_headers
        if clean_text(h).lower() == key_column
    )

    value_header = next(
        h for h in original_headers
        if clean_text(h).lower() == value_column
    )

    settings = {}

    for row in records:
        key = clean_text(row.get(key_header)).lower()

        if not key:
            continue

        settings[key] = clean_text(row.get(value_header))

    return settings


def setting(settings: dict, key: str, default: Any) -> Any:
    value = settings.get(key.lower())

    if value in (None, ""):
        return default

    return value


# ============================================================
# READING METRICS
# ============================================================

def load_reading_metrics(spreadsheet) -> list[dict]:
    worksheet = get_worksheet(spreadsheet, METRICS_SHEET)

    if worksheet is None:
        raise ValueError(
            f"'{METRICS_SHEET}' sheet was not found."
        )

    records = worksheet.get_all_records()

    metrics = []

    for row in records:
        if not normalize_bool(row.get("active")):
            continue

        metric_id = clean_text(row.get("metric_id"))

        if not metric_id:
            continue

        metric_name = clean_text(row.get("metric_name"))
        description = clean_text(row.get("metric_description"))
        guidance = clean_text(row.get("evaluation_guidance"))
        weight = safe_float(row.get("weight"))

        if not metric_name:
            continue

        if weight <= 0:
            continue

        metrics.append(
            {
                "metric_id": metric_id,
                "metric_name": metric_name,
                "metric_description": description,
                "weight": weight,
                "evaluation_guidance": guidance,
            }
        )

    if not metrics:
        raise ValueError(
            "No active Reading Metrics were found."
        )

    total_weight = sum(m["weight"] for m in metrics)

    if total_weight <= 0:
        raise ValueError(
            "Reading Metrics total weight must be greater than 0."
        )

    # We do not require total weight = 100.
    # Normalization makes the system tolerant of future changes.
    for metric in metrics:
        metric["normalized_weight"] = (
            metric["weight"] / total_weight
        )

    return metrics


# ============================================================
# OPENAI
# ============================================================

def get_openai_client():
    api_key = os.environ["OPENAI_API_KEY"]

    return OpenAI(api_key=api_key)


def build_metric_schema(metrics: list[dict]) -> dict:
    """
    Dynamic Structured Output schema.

    Every active metric becomes:
        metric_X_score
        metric_X_reason
    """

    properties = {
        "summary": {
            "type": "string",
        },
        "key_points": {
            "type": "array",
            "items": {
                "type": "string",
            },
            "minItems": 2,
            "maxItems": 5,
        },
        "why_it_matters": {
            "type": "string",
        },
        "implications": {
            "type": "string",
        },
    }

    required = [
        "summary",
        "key_points",
        "why_it_matters",
        "implications",
    ]

    for metric in metrics:
        metric_id = metric["metric_id"]

        properties[f"{metric_id}_score"] = {
            "type": "number",
            "minimum": 0,
            "maximum": 100,
        }

        properties[f"{metric_id}_reason"] = {
            "type": "string",
        }

        required.extend(
            [
                f"{metric_id}_score",
                f"{metric_id}_reason",
            ]
        )

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def build_system_prompt(
    metrics: list[dict],
    custom_prompt: str,
    language: str,
) -> str:

    metric_instructions = []

    for metric in metrics:
        metric_instructions.append(
            f"""
METRIC ID: {metric['metric_id']}
NAME: {metric['metric_name']}
DESCRIPTION: {metric['metric_description']}
EVALUATION GUIDANCE: {metric['evaluation_guidance']}
WEIGHT: {metric['weight']}
""".strip()
        )

    metrics_text = "\n\n".join(metric_instructions)

    base_prompt = f"""
You are the medical and healthcare management intelligence layer
of a personal Medical News Report system.

Your task is to read ONE article and produce:
1. A concise Vietnamese briefing.
2. Key points.
3. Why the article matters.
4. Practical implications.
5. A 0-100 score for every active Reading Metric.

LANGUAGE:
{language}

READING METRICS:
{metrics_text}

SCORING RULES:
- Score every metric independently from 0 to 100.
- Use the metric description and evaluation guidance exactly.
- Do not infer a high score merely because the article is recent.
- Do not reward generic healthcare content automatically.
- Evaluate the actual content of the article.
- Keep metric reasons concise: preferably one or two sentences.
- Do not calculate weighted score. The application calculates it.
- Do not invent facts that are absent from the article.
- Distinguish facts from reasonable implications.
- If the article is weak or mostly promotional, score accordingly.

BRIEFING RULES:
- summary: concise, factual.
- key_points: 2-5 important points.
- why_it_matters: explain the significance for the reader.
- implications: focus on hospital / healthcare management implications
  when relevant.
- Avoid generic filler.
- Do not repeat the article title.
- Do not mention that you are an AI.
"""

    custom_prompt = clean_text(custom_prompt)

    if custom_prompt:
        base_prompt += (
            "\n\nADDITIONAL CONFIGURED INSTRUCTIONS:\n"
            + custom_prompt
        )

    return base_prompt.strip()


def build_article_input(
    title: str,
    source: str,
    topics: str,
    content: str,
) -> str:

    return f"""
ARTICLE TITLE:
{title}

SOURCE:
{source}

TOPICS IDENTIFIED BY RULE ENGINE:
{topics or "None"}

ARTICLE CONTENT:
{content}
""".strip()


def call_openai(
    client: OpenAI,
    model: str,
    reasoning_effort: str,
    system_prompt: str,
    article_input: str,
    schema: dict,
    max_output_tokens: int,
):

    response = client.responses.create(
        model=model,
        reasoning={
            "effort": reasoning_effort,
        },
        instructions=system_prompt,
        input=article_input,
        text={
            "format": {
                "type": "json_schema",
                "name": "medical_news_briefing",
                "strict": True,
                "schema": schema,
            }
        },
        max_output_tokens=max_output_tokens,
        store=False,
        prompt_cache_key="medical-news-briefing-v072",
    )

    return parse_json_safely(response.output_text)


# ============================================================
# ARTICLE FETCHING
# ============================================================

def extract_article_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements.
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "nav",
            "footer",
            "header",
            "aside",
            "form",
        ]
    ):
        tag.decompose()

    candidates = []

    selectors = [
        "article",
        "[itemprop='articleBody']",
        ".article-body",
        ".article-content",
        ".entry-content",
        ".post-content",
        "main",
    ]

    for selector in selectors:
        for node in soup.select(selector):
            text = node.get_text(" ", strip=True)

            if len(text) > 500:
                candidates.append(text)

    if candidates:
        return max(candidates, key=len)

    return soup.get_text(" ", strip=True)


def fetch_article_content(
    url: str,
    excerpt: str,
    max_chars: int,
) -> tuple[str, str]:

    url = clean_text(url)
    excerpt = clean_text(excerpt)

    if not url:
        if excerpt:
            return truncate_text(excerpt, max_chars), "excerpt_fallback"

        return "", "none"

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        response.raise_for_status()

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        if "text/html" not in content_type:
            raise ValueError(
                f"Unsupported content type: {content_type}"
            )

        text = extract_article_text(response.text)
        text = clean_text(text)

        if len(text) < 300:
            raise ValueError(
                "Extracted article content is too short."
            )

        text = truncate_text(text, max_chars)

        return text, "full_content"

    except Exception as exc:
        print(
            f"[FETCH] blocked/unavailable: "
            f"{type(exc).__name__}: {exc}"
        )

        if excerpt:
            excerpt = truncate_text(
                excerpt,
                max_chars,
            )

            print(
                f"[FALLBACK] Using Articles.excerpt "
                f"({len(excerpt)} characters)."
            )

            return excerpt, "excerpt_fallback"

        return "", "none"


# ============================================================
# SELECTION
# ============================================================

def get_selected_articles(spreadsheet) -> list[dict]:

    worksheet = get_worksheet(
        spreadsheet,
        SELECTION_SHEET,
    )

    if worksheet is not None:
        records = worksheet.get_all_records()

        if records:
            return records

    # Fallback:
    # allow the engine to work without a Selection sheet.
    articles_sheet = get_worksheet(
        spreadsheet,
        ARTICLES_SHEET,
    )

    if articles_sheet is None:
        raise ValueError(
            "Neither Selection nor Articles sheet was found."
        )

    records = articles_sheet.get_all_records()

    selected = []

    for row in records:
        selected_value = row.get("selected")

        status = clean_text(
            row.get("status")
        ).lower()

        if normalize_bool(selected_value):
            selected.append(row)
        elif status in {
            "selected",
            "briefing",
            "ready",
        }:
            selected.append(row)

    return selected


# ============================================================
# EXISTING BRIEFINGS
# ============================================================

def load_existing_briefings(
    worksheet,
) -> dict[tuple[str, str], tuple[int, dict]]:
    """Load briefing rows keyed by (article_id, run_id)."""
    if worksheet is None:
        return {}
    values = worksheet.get_all_values()
    if not values:
        return {}
    headers = values[0]
    try:
        article_index = headers.index("article_id")
        run_id_index = headers.index("run_id")
    except ValueError:
        raise ValueError("Briefings sheet must contain both 'article_id' and 'run_id'.")
    result = {}
    for row_number, row in enumerate(values[1:], start=2):
        if article_index >= len(row) or run_id_index >= len(row):
            continue
        article_id = clean_text(row[article_index])
        run_id = clean_text(row[run_id_index])
        if not article_id or not run_id:
            continue
        row_dict = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
        result[(article_id, run_id)] = (row_number, row_dict)
    return result


def is_successful_briefing(row: dict) -> bool:
    status = clean_text(
        row.get("status")
    ).lower()

    return status in {
        "success",
        "successful",
        "completed",
    }


# ============================================================
# SCORE CALCULATION
# ============================================================

def calculate_reading_score(
    result: dict,
    metrics: list[dict],
) -> float:

    weighted_total = 0.0
    total_weight = 0.0

    for metric in metrics:
        metric_id = metric["metric_id"]

        score = safe_float(
            result.get(
                f"{metric_id}_score"
            )
        )

        score = max(
            0.0,
            min(100.0, score),
        )

        weighted_total += (
            score * metric["weight"]
        )

        total_weight += metric["weight"]

    if total_weight == 0:
        return 0.0

    return round(
        weighted_total / total_weight,
        2,
    )


# ============================================================
# BRIEFING ROW
# ============================================================

def build_briefing_row(
    article: dict,
    result: dict,
    metrics: list[dict],
    model: str,
) -> list:

    metric_map = {
        m["metric_id"]: m
        for m in metrics
    }

    reading_score = calculate_reading_score(
        result,
        metrics,
    )

    row = {
        "article_id": clean_text(
            article.get("article_id")
        ),
        "run_id": clean_text(
            article.get("run_id")
        ),
        "source": clean_text(
            article.get("source")
        ),
        "title": clean_text(
            article.get("title")
        ),
        "url": clean_text(
            article.get("url")
        ),
        "published_at": clean_text(
            article.get("published_at")
        ),
        "rule_score": clean_text(
            article.get("rule_score")
        ),
        "topics": clean_text(
            article.get("matched_topics")
            or article.get("topics")
        ),
        "summary": clean_text(
            result.get("summary")
        ),
        "key_points": "\n".join(
            f"• {clean_text(x)}"
            for x in result.get(
                "key_points",
                [],
            )
            if clean_text(x)
        ),
        "why_it_matters": clean_text(
            result.get("why_it_matters")
        ),
        "implications": clean_text(
            result.get("implications")
        ),
        "reading_score": reading_score,
        # Report selection is intentionally left blank here.
        # The downstream reading/report selection engine owns these fields.
        "report_selected": "",
        "report_rank": "",
        "report_selection_method": "",
        "model": model,
        "created_at": now_iso(),
        "status": "success",
    }

    # Always populate all five schema slots.
    #
    # Active metrics are dynamic, but Briefings is fixed to
    # metric_1 ... metric_5 as previously agreed.
    for i in range(1, 6):
        metric_id = f"metric_{i}"

        if metric_id in metric_map:
            row[
                f"{metric_id}_score"
            ] = safe_float(
                result.get(
                    f"{metric_id}_score"
                )
            )

            row[
                f"{metric_id}_reason"
            ] = clean_text(
                result.get(
                    f"{metric_id}_reason"
                )
            )
        else:
            row[
                f"{metric_id}_score"
            ] = ""

            row[
                f"{metric_id}_reason"
            ] = ""

    return [
        row.get(header, "")
        for header in BRIEFING_HEADERS
    ]


# ============================================================
# SHEET WRITING
# ============================================================

def ensure_briefings_header(worksheet):

    if worksheet is None:
        raise ValueError(
            f"'{BRIEFINGS_SHEET}' sheet was not found."
        )

    existing = worksheet.get_all_values()

    if not existing:
        worksheet.append_row(
            BRIEFING_HEADERS,
            value_input_option="USER_ENTERED",
        )
        return

    existing_headers = existing[0]

    if len(existing_headers) != len(set(existing_headers)):
        raise ValueError(
            f"{BRIEFINGS_SHEET} contains duplicate header names. "
            f"Expected one column per field.\n"
            f"Found:\n{existing_headers}"
        )

    if existing_headers != BRIEFING_HEADERS:
        raise ValueError(
            f"{BRIEFINGS_SHEET} header mismatch.\n"
            f"Expected:\n{BRIEFING_HEADERS}\n"
            f"Found:\n{existing_headers}"
        )


def write_briefing_row(
    worksheet,
    row_number: int | None,
    row_values: list,
):

    if row_number:
        end_col = len(BRIEFING_HEADERS)

        # Convert column number to A1 notation.
        def col_letter(n):
            result = ""
            while n:
                n, remainder = divmod(
                    n - 1,
                    26,
                )
                result = chr(
                    65 + remainder
                ) + result

            return result

        range_name = (
            f"A{row_number}:"
            f"{col_letter(end_col)}{row_number}"
        )

        worksheet.update(
            range_name=range_name,
            values=[row_values],
        )

    else:
        worksheet.append_row(
            row_values,
            value_input_option="USER_ENTERED",
        )


# ============================================================
# MAIN PROCESS
# ============================================================

def process_articles(
    spreadsheet,
    client,
    settings,
    metrics,
):

    articles = get_selected_articles(
        spreadsheet
    )

    run_ids = {clean_text(article.get("run_id")) for article in articles if clean_text(article.get("run_id"))}
    if len(run_ids) > 1:
        raise ValueError("Selected articles contain multiple run_id values: " + ", ".join(sorted(run_ids)))
    current_run_id = next(iter(run_ids), "")
    if current_run_id:
        print(f"[RUN] run_id={current_run_id}")

    print(
        f"[BRIEFING] Selected articles: "
        f"{len(articles)}"
    )

    if not articles:
        print(
            "[BRIEFING] No selected articles."
        )
        return

    briefings_sheet = get_worksheet(
        spreadsheet,
        BRIEFINGS_SHEET,
    )

    ensure_briefings_header(
        briefings_sheet
    )

    existing = load_existing_briefings(
        briefings_sheet
    )

    model = setting(
        settings,
        "model",
        DEFAULT_MODEL,
    )

    language = setting(
        settings,
        "language",
        DEFAULT_LANGUAGE,
    )

    max_content_chars = int(
        safe_float(
            setting(
                settings,
                "max_content_chars",
                DEFAULT_MAX_CONTENT_CHARS,
            ),
            DEFAULT_MAX_CONTENT_CHARS,
        )
    )

    reasoning_effort = setting(
        settings,
        "reasoning_effort",
        DEFAULT_REASONING_EFFORT,
    )

    max_output_tokens = int(
        safe_float(
            setting(
                settings,
                "max_output_tokens",
                DEFAULT_MAX_OUTPUT_TOKENS,
            ),
            DEFAULT_MAX_OUTPUT_TOKENS,
        )
    )

    custom_prompt = setting(
        settings,
        "briefing_prompt",
        setting(
            settings,
            "prompt",
            "",
        ),
    )

    system_prompt = build_system_prompt(
        metrics=metrics,
        custom_prompt=custom_prompt,
        language=language,
    )

    schema = build_metric_schema(
        metrics
    )

    print(
        f"[SETTINGS] model={model}"
    )
    print(
        f"[SETTINGS] max_content_chars="
        f"{max_content_chars}"
    )
    print(
        f"[SETTINGS] reasoning_effort="
        f"{reasoning_effort}"
    )
    print(
        f"[SETTINGS] language={language}"
    )
    print(
        f"[SETTINGS] active metrics="
        f"{len(metrics)}"
    )
    print(
        "[SETTINGS] prompt="
        + (
            "loaded from Settings"
            if custom_prompt
            else "default"
        )
    )

    successful = 0
    failed = 0
    skipped = 0
    fallback_count = 0
    ai_calls = 0
    ai_calls_avoided = 0

    for article in articles:

        article_id = clean_text(
            article.get("article_id")
        )

        title = clean_text(
            article.get("title")
        )

        run_id = clean_text(article.get("run_id"))

        if not article_id:
            print("[SKIP] Article without article_id.")
            skipped += 1
            continue

        if not run_id:
            print(f"[ERROR] {title}: missing run_id.")
            failed += 1
            continue

        print(f"\n[BRIEFING] {title}")

        # Skip only when the same article was successfully briefed in the same run.
        existing_item = existing.get((article_id, run_id))

        if existing_item:
            row_number, old_row = existing_item

            if is_successful_briefing(
                old_row
            ):
                print("[SKIP] Successful briefing already exists for this run.")
                skipped += 1
                ai_calls_avoided += 1
                continue

        # ----------------------------------------------------
        # Fetch content
        # ----------------------------------------------------

        url = clean_text(
            article.get("url")
        )

        excerpt = clean_text(
            article.get("excerpt")
        )

        print(
            f"[FETCH] {url}"
        )

        content, content_source = (
            fetch_article_content(
                url=url,
                excerpt=excerpt,
                max_chars=max_content_chars,
            )
        )

        if not content:
            print(
                "[ERROR] No article content available."
            )
            failed += 1
            continue

        if content_source == "excerpt_fallback":
            fallback_count += 1

        print(
            f"[CONTENT] source={content_source}, "
            f"chars={len(content)}"
        )

        # ----------------------------------------------------
        # One OpenAI request
        # ----------------------------------------------------

        article_input = build_article_input(
            title=title,
            source=clean_text(
                article.get("source")
            ),
            topics=clean_text(
                article.get(
                    "matched_topics"
                )
                or article.get("topics")
            ),
            content=content,
        )

        try:

            ai_calls += 1

            result = call_openai(
                client=client,
                model=model,
                reasoning_effort=reasoning_effort,
                system_prompt=system_prompt,
                article_input=article_input,
                schema=schema,
                max_output_tokens=max_output_tokens,
            )

            row_values = build_briefing_row(
                article=article,
                result=result,
                metrics=metrics,
                model=model,
            )

            row_number = (
                existing_item[0]
                if existing_item
                else None
            )

            write_briefing_row(
                worksheet=briefings_sheet,
                row_number=row_number,
                row_values=row_values,
            )

            successful += 1

            score_index = (
                BRIEFING_HEADERS.index(
                    "reading_score"
                )
            )

            reading_score = row_values[
                score_index
            ]

            print(
                f"[SUCCESS] {title} "
                f"(reading_score={reading_score}, "
                f"content={content_source})"
            )

            # Avoid hammering Sheets/API too quickly.
            time.sleep(0.15)

        except Exception as exc:

            print(
                f"[ERROR] {title}: "
                f"{type(exc).__name__}: {exc}"
            )

            failed += 1

    print(
        "\n"
        + "=" * 60
    )
    print("BRIEFING SUMMARY")
    print("=" * 60)

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
    print(f"Excerpt fallback: {fallback_count}")
    print(f"New AI calls: {ai_calls}")
    print(f"AI calls avoided: {ai_calls_avoided}")

    print("=" * 60)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print(
        f"AI BRIEFING ENGINE v{VERSION}"
    )
    print("=" * 60)

    spreadsheet = get_spreadsheet()

    settings = load_settings(
        spreadsheet
    )

    metrics = load_reading_metrics(
        spreadsheet
    )

    print(
        "[METRICS] Active Reading Metrics:"
    )

    for metric in metrics:
        print(
            f"  - {metric['metric_id']}: "
            f"{metric['metric_name']} "
            f"(weight={metric['weight']})"
        )

    client = get_openai_client()

    process_articles(
        spreadsheet=spreadsheet,
        client=client,
        settings=settings,
        metrics=metrics,
    )


if __name__ == "__main__":
    main()
