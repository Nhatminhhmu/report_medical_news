from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

import gspread
import requests
from google.oauth2.service_account import Credentials
from openai import OpenAI


# ============================================================
# CONFIG
# ============================================================

VERSION = "1.0.0"

BRIEFINGS_SHEET = "Briefings"

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_LANGUAGE = "Vietnamese"
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_MAX_OUTPUT_TOKENS = 1800

DEFAULT_SELECTION_METHOD = "elbow"
DEFAULT_MIN_REPORT_ARTICLES = 3
DEFAULT_MAX_REPORT_ARTICLES = 7

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

OPENAI_PROMPT_CACHE_KEY = "medical-news-report-v1"

TELEGRAM_API = "https://api.telegram.org"


# ============================================================
# HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).replace("\x00", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def normalize_bool(value: Any) -> bool:
    return str(value).strip().lower() in {
        "true",
        "1",
        "yes",
        "y",
        "active",
        "enabled",
    }


def normalize_status(value: Any) -> str:
    return re.sub(r"[\s\-]+", "_", clean_text(value).lower())


def get_worksheet(spreadsheet, name: str):
    try:
        return spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        return None


# ============================================================
# GOOGLE SHEETS
# ============================================================

def get_spreadsheet():
    credentials_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    spreadsheet_id = os.environ["GOOGLE_SPREADSHEET_ID"]

    credentials = Credentials.from_service_account_info(
        json.loads(credentials_json),
        scopes=GOOGLE_SCOPES,
    )

    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(spreadsheet_id)

    print(f"Connected to: {spreadsheet.title}")

    return spreadsheet


def load_settings(spreadsheet) -> dict[str, str]:
    worksheet = get_worksheet(spreadsheet, "Settings")

    if worksheet is None:
        print("[SETTINGS] Settings sheet not found; using defaults.")
        return {}

    records = worksheet.get_all_records()

    if not records:
        return {}

    headers = list(records[0].keys())
    normalized = {
        clean_text(h).lower(): h
        for h in headers
    }

    key_header = next(
        (
            normalized[k]
            for k in (
                "setting_key",
                "key",
                "parameter",
                "setting",
                "name",
            )
            if k in normalized
        ),
        None,
    )

    value_header = next(
        (
            normalized[k]
            for k in (
                "setting_value",
                "value",
                "parameter_value",
            )
            if k in normalized
        ),
        None,
    )

    if not key_header or not value_header:
        raise ValueError(
            "Settings sheet must contain a key column and a value column."
        )

    settings = {}

    for row in records:
        key = clean_text(row.get(key_header)).lower()

        if key:
            settings[key] = clean_text(row.get(value_header))

    return settings


def setting(settings: dict[str, str], key: str, default: Any) -> Any:
    value = settings.get(key.lower())

    if value in (None, ""):
        return default

    return value


# ============================================================
# RUN ID
# ============================================================

def get_current_run_id(spreadsheet) -> str:
    runs_sheet = get_worksheet(spreadsheet, "Runs")

    if runs_sheet is not None:
        records = runs_sheet.get_all_records()
        candidates = []

        for row in records:
            run_id = clean_text(row.get("run_id"))

            if not run_id:
                continue

            status = normalize_status(row.get("status"))

            if status in {
                "failed",
                "error",
                "cancelled",
                "canceled",
            }:
                continue

            candidates.append(run_id)

        if candidates:
            return max(candidates)

    # Fallback: latest run_id actually present in Briefings.
    worksheet = get_worksheet(spreadsheet, BRIEFINGS_SHEET)

    if worksheet is not None:
        records = worksheet.get_all_records()

        candidates = [
            clean_text(row.get("run_id"))
            for row in records
            if clean_text(row.get("run_id"))
        ]

        if candidates:
            return max(candidates)

    return ""


# ============================================================
# BRIEFINGS
# ============================================================

def load_successful_briefings(
    spreadsheet,
    run_id: str,
) -> list[dict]:
    worksheet = get_worksheet(spreadsheet, BRIEFINGS_SHEET)

    if worksheet is None:
        raise ValueError(
            f"'{BRIEFINGS_SHEET}' sheet was not found."
        )

    records = worksheet.get_all_records()

    result = []

    for row in records:
        if clean_text(row.get("run_id")) != run_id:
            continue

        if normalize_status(row.get("status")) not in {
            "success",
            "successful",
            "completed",
        }:
            continue

        article_id = clean_text(row.get("article_id"))

        if not article_id:
            continue

        score = safe_float(row.get("reading_score"), -1)

        if score < 0:
            continue

        result.append(row)

    # Highest Reading Score first.
    result.sort(
        key=lambda row: (
            -safe_float(row.get("reading_score")),
            clean_text(row.get("title")).lower(),
        )
    )

    return result


# ============================================================
# REPORT SELECTION
# ============================================================

def elbow_select_count(
    articles: list[dict],
    min_count: int,
    max_count: int,
) -> int:
    """
    Simple deterministic elbow selection.

    For sorted scores s1 >= s2 ...:
        gap_i = s_i - s_(i+1)

    We select immediately before the largest meaningful drop.

    Safeguards:
    - Always respect min_count.
    - Never exceed max_count.
    - Never exceed available articles.
    - With too few articles, use all available.
    """

    n = len(articles)

    if n == 0:
        return 0

    min_count = max(1, min(min_count, n))
    max_count = max(min_count, min(max_count, n))

    if n <= min_count:
        return n

    if min_count >= max_count:
        return max_count

    scores = [
        safe_float(row.get("reading_score"))
        for row in articles
    ]

    gaps = []

    for i in range(n - 1):
        gaps.append(
            max(0.0, scores[i] - scores[i + 1])
        )

    # Only consider cut points inside the configured range.
    candidate_indices = [
        i
        for i in range(
            min_count - 1,
            min(max_count - 1, n - 2) + 1,
        )
    ]

    if not candidate_indices:
        return min_count

    # Robust threshold:
    # a gap is "meaningful" if it is materially larger than
    # the median gap. This prevents a random tiny score difference
    # from producing a cut.
    sorted_gaps = sorted(gaps)
    median_gap = (
        sorted_gaps[len(sorted_gaps) // 2]
        if sorted_gaps
        else 0.0
    )

    best_index = max(
        candidate_indices,
        key=lambda i: gaps[i],
    )

    best_gap = gaps[best_index]

    # If the largest candidate gap is not meaningfully above
    # the normal spacing, prefer max_count. This avoids aggressive
    # under-selection when scores are relatively flat.
    meaningful = (
        best_gap >= 5.0
        and (
            median_gap == 0
            or best_gap >= median_gap * 1.5
        )
    )

    if meaningful:
        return best_index + 1

    return max_count


def select_report_articles(
    articles: list[dict],
    method: str,
    min_count: int,
    max_count: int,
) -> list[dict]:

    method = clean_text(method).lower()

    if method == "elbow":
        count = elbow_select_count(
            articles,
            min_count,
            max_count,
        )
    else:
        # Safe deterministic fallback.
        count = min(
            max_count,
            max(min_count, len(articles)),
        )

    selected = articles[:count]

    return selected


# ============================================================
# BRIEFINGS UPDATE
# ============================================================

def column_letter(number: int) -> str:
    result = ""

    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result

    return result


def update_report_selection(
    worksheet,
    run_id: str,
    selected: list[dict],
    method: str,
) -> None:

    values = worksheet.get_all_values()

    if not values:
        raise ValueError("Briefings sheet is empty.")

    headers = values[0]

    required = [
        "article_id",
        "run_id",
        "report_selected",
        "report_rank",
        "report_selection_method",
    ]

    missing = [
        h for h in required
        if h not in headers
    ]

    if missing:
        raise ValueError(
            "Briefings is missing report-selection columns: "
            + ", ".join(missing)
        )

    article_index = headers.index("article_id")
    run_index = headers.index("run_id")
    selected_index = headers.index("report_selected")
    rank_index = headers.index("report_rank")
    method_index = headers.index("report_selection_method")

    selected_ids = {
        clean_text(row.get("article_id"))
        for row in selected
    }

    rank_map = {
        clean_text(row.get("article_id")): index
        for index, row in enumerate(selected, start=1)
    }

    # Batch update only the three selection columns.
    updates = []

    for row_number, row in enumerate(values[1:], start=2):
        if run_index >= len(row):
            continue

        if clean_text(row[run_index]) != run_id:
            continue

        article_id = clean_text(
            row[article_index]
            if article_index < len(row)
            else ""
        )

        if not article_id:
            continue

        is_selected = article_id in selected_ids
        rank = rank_map.get(article_id, "")

        updates.append(
            {
                "range": (
                    f"{column_letter(selected_index + 1)}{row_number}:"
                    f"{column_letter(method_index + 1)}{row_number}"
                ),
                "values": [
                    [
                        "TRUE" if is_selected else "FALSE",
                        rank,
                        method if is_selected else "",
                    ]
                ],
            }
        )

    if updates:
        worksheet.batch_update(
            updates,
            value_input_option="USER_ENTERED",
        )


# ============================================================
# REPORT PROMPT
# ============================================================

DEFAULT_REPORT_PROMPT = """
Bạn là biên tập viên của một hệ thống Medical News Report.

Hãy tổng hợp các briefing được cung cấp thành một bản báo cáo ngắn gọn,
có tính biên tập và có giá trị cho người làm:
- quản trị bệnh viện;
- chiến lược y tế;
- vận hành bệnh viện;
- chuyển đổi số y tế;
- marketing/truyền thông y tế;
- phát triển kinh doanh healthcare.

NGUYÊN TẮC:
1. Chỉ sử dụng thông tin có trong các briefing được cung cấp.
2. Không bịa số liệu, sự kiện, tên người, tổ chức hoặc nguyên nhân.
3. Không biến suy luận thành fact.
4. Không tóm tắt tuần tự một cách máy móc từng bài.
5. Hãy tìm ra những điểm đáng chú ý chung giữa các bài.
6. Nếu các bài không có một xu hướng chung rõ ràng, hãy nói rõ thay vì cố tạo ra một xu hướng.
7. Ưu tiên insight có giá trị đối với quản trị và vận hành healthcare.
8. Khi nói về Việt Nam, chỉ sử dụng những implications đã được nêu trong briefing.
9. Giữ report ngắn, dễ đọc trên Telegram.
10. Mỗi bài được chọn phải xuất hiện trong phần "selected_articles".
11. Không đưa thêm bài ngoài danh sách được cung cấp.
12. Không sử dụng markdown heading trong các field JSON.
""".strip()


def build_report_prompt(
    custom_prompt: str,
    language: str,
) -> str:

    prompt = clean_text(custom_prompt)

    if not prompt:
        prompt = DEFAULT_REPORT_PROMPT

    return (
        prompt
        + f"\n\nNgôn ngữ output: {language}."
    )


def build_report_input(
    selected: list[dict],
) -> str:

    blocks = []

    for index, row in enumerate(selected, start=1):
        blocks.append(
            f"""
ARTICLE {index}

article_id: {clean_text(row.get("article_id"))}
source: {clean_text(row.get("source"))}
title: {clean_text(row.get("title"))}
url: {clean_text(row.get("url"))}
published_at: {clean_text(row.get("published_at"))}
topics: {clean_text(row.get("topics"))}
reading_score: {clean_text(row.get("reading_score"))}

SUMMARY:
{clean_text(row.get("summary"))}

KEY POINTS:
{clean_text(row.get("key_points"))}

WHY IT MATTERS:
{clean_text(row.get("why_it_matters"))}

IMPLICATIONS:
{clean_text(row.get("implications"))}
""".strip()
        )

    return "\n\n====================\n\n".join(blocks)


# ============================================================
# OPENAI
# ============================================================

def get_openai_client():
    return OpenAI(
        api_key=os.environ["OPENAI_API_KEY"]
    )


def call_report_ai(
    client: OpenAI,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
    language: str,
    system_prompt: str,
    report_input: str,
) -> dict:

    schema = {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
            },
            "executive_summary": {
                "type": "string",
            },
            "key_themes": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "minItems": 1,
                "maxItems": 5,
            },
            "strategic_takeaways": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "minItems": 1,
                "maxItems": 5,
            },
            "vietnam_implications": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "minItems": 0,
                "maxItems": 5,
            },
            "selected_articles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "article_id": {
                            "type": "string",
                        },
                        "editorial_note": {
                            "type": "string",
                        },
                    },
                    "required": [
                        "article_id",
                        "editorial_note",
                    ],
                    "additionalProperties": False,
                },
                "minItems": 1,
                "maxItems": 7,
            },
        },
        "required": [
            "headline",
            "executive_summary",
            "key_themes",
            "strategic_takeaways",
            "vietnam_implications",
            "selected_articles",
        ],
        "additionalProperties": False,
    }

    response = client.responses.create(
        model=model,
        reasoning={
            "effort": reasoning_effort,
        },
        instructions=system_prompt,
        input=report_input,
        text={
            "format": {
                "type": "json_schema",
                "name": "medical_news_report",
                "strict": True,
                "schema": schema,
            }
        },
        max_output_tokens=max_output_tokens,
        store=False,
        prompt_cache_key=OPENAI_PROMPT_CACHE_KEY,
    )

    raw = response.output_text

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"OpenAI returned invalid JSON: {exc}"
        ) from exc


# ============================================================
# TELEGRAM
# ============================================================

def escape_telegram(text: str) -> str:
    # We intentionally use plain Telegram HTML instead of MarkdownV2.
    return (
        clean_text(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_report_for_telegram(
    report: dict,
    selected: list[dict],
    run_id: str,
    language: str,
) -> str:

    lines = []

    lines.append("🏥 <b>MEDICAL NEWS REPORT</b>")
    lines.append(
        escape_telegram(
            datetime.now().strftime("%d/%m/%Y")
        )
    )
    lines.append(
        f"<i>{len(selected)} bài đáng đọc</i>"
    )
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("")

    headline = escape_telegram(
        report.get("headline")
        or "Medical News Report"
    )

    lines.append(f"<b>{headline}</b>")
    lines.append("")

    lines.append("<b>EXECUTIVE SUMMARY</b>")
    lines.append(
        escape_telegram(
            report.get("executive_summary", "")
        )
    )
    lines.append("")

    themes = report.get("key_themes") or []

    if themes:
        lines.append("<b>KEY THEMES</b>")

        for theme in themes:
            lines.append(
                "• " + escape_telegram(theme)
            )

        lines.append("")

    takeaways = report.get("strategic_takeaways") or []

    if takeaways:
        lines.append("<b>STRATEGIC TAKEAWAYS</b>")

        for takeaway in takeaways:
            lines.append(
                "• " + escape_telegram(takeaway)
            )

        lines.append("")

    implications = report.get("vietnam_implications") or []

    if implications:
        lines.append("<b>IMPLICATIONS FOR VIETNAM</b>")

        for item in implications:
            lines.append(
                "• " + escape_telegram(item)
            )

        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("<b>SELECTED ARTICLES</b>")
    lines.append("")

    selected_by_id = {
        clean_text(row.get("article_id")): row
        for row in selected
    }

    notes = {
        clean_text(item.get("article_id")): clean_text(
            item.get("editorial_note")
        )
        for item in (
            report.get("selected_articles")
            or []
        )
    }

    for rank, row in enumerate(selected, start=1):
        title = escape_telegram(
            row.get("title")
        )

        source = escape_telegram(
            row.get("source")
        )

        score = safe_float(
            row.get("reading_score")
        )

        url = clean_text(row.get("url"))

        lines.append(
            f"<b>{rank}. {title}</b>"
        )

        lines.append(
            f"{source} · Reading Score {score:.1f}"
        )

        note = notes.get(
            clean_text(row.get("article_id")),
            "",
        )

        if note:
            lines.append(
                "→ " + escape_telegram(note)
            )

        if url:
            lines.append(
                f'🔗 <a href="{escape_telegram(url)}">Đọc bài gốc</a>'
            )

        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"<i>Run: {escape_telegram(run_id)}</i>"
    )

    return "\n".join(lines)


def split_telegram_message(
    text: str,
    max_chars: int = 3900,
) -> list[str]:

    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = ""

    for block in text.split("\n\n"):
        candidate = (
            block
            if not current
            else current + "\n\n" + block
        )

        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)

        # Hard split only when one block itself is too long.
        while len(block) > max_chars:
            chunks.append(block[:max_chars])
            block = block[max_chars:]

        current = block

    if current:
        chunks.append(current)

    return chunks


def send_telegram(
    message: str,
) -> None:

    enabled = normalize_bool(
        os.getenv(
            "TELEGRAM_ENABLED",
            "false",
        )
    )

    if not enabled:
        print(
            "[TELEGRAM] Disabled. "
            "Report printed locally."
        )
        print("\n" + message)
        return

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        raise ValueError(
            "TELEGRAM_ENABLED=true but "
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing."
        )

    url = (
        f"{TELEGRAM_API}/bot"
        f"{bot_token}/sendMessage"
    )

    chunks = split_telegram_message(message)

    for chunk in chunks:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )

        response.raise_for_status()

    print(
        f"[TELEGRAM] Sent {len(chunks)} message(s)."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print(f"MEDICAL NEWS REPORT ENGINE v{VERSION}")
    print("=" * 60)

    spreadsheet = get_spreadsheet()

    # IMPORTANT:
    # Settings are reloaded on EVERY run.
    settings = load_settings(spreadsheet)

    run_id = get_current_run_id(spreadsheet)

    if not run_id:
        print("[REPORT] No current run_id.")
        return

    model = clean_text(
        setting(
            settings,
            "report_model",
            setting(
                settings,
                "briefing_model",
                DEFAULT_MODEL,
            ),
        )
    )

    language = clean_text(
        setting(
            settings,
            "report_language",
            setting(
                settings,
                "briefing_language",
                DEFAULT_LANGUAGE,
            ),
        )
    )

    reasoning_effort = clean_text(
        setting(
            settings,
            "report_reasoning_effort",
            setting(
                settings,
                "reasoning_effort",
                DEFAULT_REASONING_EFFORT,
            ),
        )
    )

    max_output_tokens = int(
        safe_float(
            setting(
                settings,
                "report_max_output_tokens",
                setting(
                    settings,
                    "max_output_tokens",
                    DEFAULT_MAX_OUTPUT_TOKENS,
                ),
            ),
            DEFAULT_MAX_OUTPUT_TOKENS,
        )
    )

    selection_method = clean_text(
        setting(
            settings,
            "reading_selection_method",
            DEFAULT_SELECTION_METHOD,
        )
    )

    min_articles = int(
        safe_float(
            setting(
                settings,
                "min_report_articles",
                DEFAULT_MIN_REPORT_ARTICLES,
            ),
            DEFAULT_MIN_REPORT_ARTICLES,
        )
    )

    max_articles = int(
        safe_float(
            setting(
                settings,
                "max_report_articles",
                DEFAULT_MAX_REPORT_ARTICLES,
            ),
            DEFAULT_MAX_REPORT_ARTICLES,
        )
    )

    custom_prompt = clean_text(
        setting(
            settings,
            "report_prompt",
            "",
        )
    )

    print(f"[RUN] run_id={run_id}")
    print(f"[SETTINGS] model={model}")
    print(f"[SETTINGS] language={language}")
    print(f"[SETTINGS] reasoning_effort={reasoning_effort}")
    print(f"[SETTINGS] max_output_tokens={max_output_tokens}")
    print(f"[SETTINGS] selection_method={selection_method}")
    print(f"[SETTINGS] min_report_articles={min_articles}")
    print(f"[SETTINGS] max_report_articles={max_articles}")
    print(
        "[SETTINGS] report_prompt="
        + ("loaded from Settings" if custom_prompt else "default")
    )

    briefings = load_successful_briefings(
        spreadsheet,
        run_id,
    )

    print(
        f"[REPORT] Successful briefings: {len(briefings)}"
    )

    if not briefings:
        print(
            "[REPORT] No successful briefings for current run."
        )
        return

    selected = select_report_articles(
        briefings,
        selection_method,
        min_articles,
        max_articles,
    )

    print(
        f"[REPORT] Selected articles: {len(selected)}"
    )

    for rank, row in enumerate(selected, start=1):
        print(
            f"  {rank}. "
            f"{clean_text(row.get('title'))} "
            f"(reading_score="
            f"{safe_float(row.get('reading_score')):.2f})"
        )

    briefings_sheet = get_worksheet(
        spreadsheet,
        BRIEFINGS_SHEET,
    )

    update_report_selection(
        briefings_sheet,
        run_id,
        selected,
        selection_method,
    )

    print("[REPORT] Briefings selection fields updated.")

    client = get_openai_client()

    system_prompt = build_report_prompt(
        custom_prompt,
        language,
    )

    report_input = build_report_input(
        selected,
    )

    print("[AI] Generating Medical News Report...")

    report = call_report_ai(
        client=client,
        model=model,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        language=language,
        system_prompt=system_prompt,
        report_input=report_input,
    )

    message = format_report_for_telegram(
        report=report,
        selected=selected,
        run_id=run_id,
        language=language,
    )

    send_telegram(message)

    print("=" * 60)
    print("REPORT SUMMARY")
    print("=" * 60)
    print(f"Run ID: {run_id}")
    print(f"Briefings available: {len(briefings)}")
    print(f"Report articles: {len(selected)}")
    print("AI calls: 1")
    print("Status: success")
    print("=" * 60)


if __name__ == "__main__":
    main()
