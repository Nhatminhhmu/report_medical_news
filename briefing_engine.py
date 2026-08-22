import os
import json
import re
import time
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
import requests
from bs4 import BeautifulSoup


# ============================================================
# AI BRIEFING ENGINE
# Version: 0.2
# ============================================================

APP_VERSION = "0.2"

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_LANGUAGE = "Vietnamese"
DEFAULT_MAX_CONTENT_CHARS = 50000

ARTICLES_SHEET = "Articles"
BRIEFINGS_SHEET = "Briefings"
SETTINGS_SHEET = "Settings"

ARTICLE_STATUS_SELECTED = "SELECTED"
ARTICLE_STATUS_BRIEFED = "BRIEFED"
ARTICLE_STATUS_BRIEFING_ERROR = "BRIEFING_ERROR"

BRIEFING_FIELDS = [
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


# ============================================================
# DEFAULT PROMPT
# Used only when Settings.briefing_prompt is missing.
# ============================================================

DEFAULT_BRIEFING_PROMPT = """
Bạn là một chuyên gia phân tích thông tin y tế và vận hành bệnh viện.

Hãy đọc TOÀN BỘ nội dung bài viết bên dưới và tạo một briefing
ngắn gọn, chính xác, có giá trị cho một người làm:

- quản trị bệnh viện;
- chiến lược y tế;
- vận hành bệnh viện;
- chuyển đổi số y tế;
- marketing/truyền thông y tế;
- phát triển kinh doanh trong lĩnh vực healthcare.

Ngôn ngữ output: {language}.

NGUYÊN TẮC:

1. Chỉ sử dụng thông tin có trong bài viết.
2. Không bịa số liệu, tên người, tổ chức, kết quả hoặc nguyên nhân.
3. Không biến suy luận thành fact.
4. Nếu bài viết không đủ thông tin, hãy nói rõ thay vì đoán.
5. Phân biệt nội dung bài viết với phân tích của bạn.
6. Không viết lại toàn bộ bài.
7. Giúp người đọc nhanh chóng hiểu:
   - chuyện gì xảy ra;
   - điều gì quan trọng;
   - ý nghĩa đối với healthcare/hospital management.
8. Không cố ép các bài tin nhân sự hoặc tin doanh nghiệp
   thành phân tích chiến lược nếu nội dung không hỗ trợ.
9. Không sử dụng markdown heading trong các field output.
10. key_points và implications phải là các ý độc lập, ngắn gọn.

SOURCE:
{source}

TITLE:
{title}

MATCHED TOPICS:
{topics}

ARTICLE:
{article_text}
""".strip()


# ============================================================
# GOOGLE SHEETS
# ============================================================

def get_spreadsheet():
    credentials_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    spreadsheet_id = os.environ["GOOGLE_SPREADSHEET_ID"]

    credentials_info = json.loads(credentials_json)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=scopes,
    )

    client = gspread.authorize(credentials)

    spreadsheet = client.open_by_key(spreadsheet_id)

    print(f"Connected to: {spreadsheet.title}")

    return spreadsheet


# ============================================================
# SETTINGS
# ============================================================

def load_settings(spreadsheet):
    try:
        worksheet = spreadsheet.worksheet(SETTINGS_SHEET)
    except gspread.WorksheetNotFound:
        return {}

    values = worksheet.get_all_values()

    if not values:
        return {}

    headers = [
        str(value).strip()
        for value in values[0]
    ]

    settings = {}

    for row in values[1:]:
        if not row:
            continue

        row_data = {}

        for index, header in enumerate(headers):
            if index < len(row):
                row_data[header] = row[index]

        key = (
            row_data.get("key")
            or row_data.get("name")
            or row_data.get("setting")
            or ""
        ).strip()

        value = row_data.get("value", "").strip()

        if key:
            settings[key] = value

    return settings


def get_setting(settings, key, default=None):
    value = settings.get(key)

    if value is None:
        return default

    if str(value).strip() == "":
        return default

    return value


def get_int_setting(settings, key, default):
    try:
        return int(
            get_setting(
                settings,
                key,
                default,
            )
        )
    except (ValueError, TypeError):
        return default


# ============================================================
# OPENAI
# ============================================================

def get_openai_client():
    api_key = os.environ["OPENAI_API_KEY"]

    return OpenAI(
        api_key=api_key
    )


# ============================================================
# HTTP / ARTICLE EXTRACTION
# ============================================================

def fetch_article(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=30,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    for element in soup([
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
    ]):
        element.decompose()

    article = (
        soup.find("article")
        or soup.find(
            "div",
            class_=re.compile(
                r"(article|post|entry|content|story)",
                re.I,
            ),
        )
        or soup.body
    )

    if article is None:
        raise ValueError(
            "Could not locate article content."
        )

    text = article.get_text(
        "\n",
        strip=True,
    )

    lines = []

    for line in text.splitlines():
        line = re.sub(
            r"\s+",
            " ",
            line,
        ).strip()

        if line:
            lines.append(line)

    return "\n".join(lines)


# ============================================================
# CELL NORMALIZATION
# ============================================================

def normalize_cell(value):
    """
    Convert AI output into a Google Sheets-safe scalar.
    """

    if value is None:
        return ""

    if isinstance(value, list):
        cleaned = []

        for item in value:
            if item is None:
                continue

            if isinstance(item, dict):
                item = json.dumps(
                    item,
                    ensure_ascii=False,
                )

            item = str(item).strip()

            if item:
                cleaned.append(
                    f"• {item}"
                )

        return "\n".join(cleaned)

    if isinstance(value, dict):
        return json.dumps(
            value,
            ensure_ascii=False,
        )

    if isinstance(value, bool):
        return (
            "TRUE"
            if value
            else "FALSE"
        )

    return str(value).strip()


# ============================================================
# STRUCTURED OUTPUT SCHEMA
# ============================================================

BRIEFING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "string",
        },
        "key_points": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "why_it_matters": {
            "type": "string",
        },
        "implications": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
    },
    "required": [
        "summary",
        "key_points",
        "why_it_matters",
        "implications",
    ],
}


# ============================================================
# PROMPT BUILDING
# ============================================================

def build_prompt(
    prompt_template,
    title,
    source,
    topics,
    article_text,
    language,
):
    values = {
        "language": language,
        "source": source,
        "title": title,
        "topics": normalize_cell(topics),
        "article_text": article_text,
    }

    try:
        prompt = prompt_template.format(
            **values
        )
    except KeyError as exc:
        raise ValueError(
            "Unknown placeholder in "
            f"briefing_prompt: {exc}"
        )

    return prompt.strip()


# ============================================================
# AI BRIEFING
# ============================================================

def generate_briefing(
    client,
    model,
    prompt_template,
    title,
    source,
    topics,
    article_text,
    language,
):
    prompt = build_prompt(
        prompt_template=prompt_template,
        title=title,
        source=source,
        topics=topics,
        article_text=article_text,
        language=language,
    )

    response = client.responses.create(
        model=model,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "medical_news_briefing",
                "strict": True,
                "schema": BRIEFING_SCHEMA,
            }
        },
    )

    output_text = response.output_text

    if not output_text:
        raise ValueError(
            "OpenAI returned an empty response."
        )

    try:
        data = json.loads(
            output_text
        )
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Structured output could not "
            "be parsed as JSON."
        ) from exc

    validate_briefing(data)

    return data


def validate_briefing(data):
    if not isinstance(data, dict):
        raise ValueError(
            "Briefing output is not an object."
        )

    required = [
        "summary",
        "key_points",
        "why_it_matters",
        "implications",
    ]

    for field in required:
        if field not in data:
            raise ValueError(
                f"Missing briefing field: {field}"
            )

    if not isinstance(
        data["summary"],
        str,
    ):
        raise ValueError(
            "summary must be a string."
        )

    if not isinstance(
        data["why_it_matters"],
        str,
    ):
        raise ValueError(
            "why_it_matters must be a string."
        )

    if not isinstance(
        data["key_points"],
        list,
    ):
        raise ValueError(
            "key_points must be a list."
        )

    if not isinstance(
        data["implications"],
        list,
    ):
        raise ValueError(
            "implications must be a list."
        )

    for item in data["key_points"]:
        if not isinstance(item, str):
            raise ValueError(
                "Every key_points item "
                "must be a string."
            )

    for item in data["implications"]:
        if not isinstance(item, str):
            raise ValueError(
                "Every implications item "
                "must be a string."
            )


# ============================================================
# SHEET HELPERS
# ============================================================

def header_map(headers):
    return {
        str(header).strip(): index
        for index, header in enumerate(headers)
    }


def row_value(
    row,
    mapping,
    field,
):
    index = mapping.get(field)

    if index is None:
        return ""

    if index >= len(row):
        return ""

    return row[index]


def update_article_status(
    worksheet,
    row_number,
    mapping,
    status,
):
    status_index = mapping.get("status")

    if status_index is None:
        raise ValueError(
            "Articles sheet is missing "
            "status column."
        )

    cell = gspread.utils.rowcol_to_a1(
        row_number,
        status_index + 1,
    )

    worksheet.update(
        range_name=cell,
        values=[[status]],
    )


# ============================================================
# BRIEFING DUPLICATE CHECK
# ============================================================

def find_briefing_by_article_id(
    worksheet,
    article_id,
):
    values = worksheet.get_all_values()

    if not values:
        return None

    headers = values[0]
    mapping = header_map(headers)

    article_id_index = mapping.get(
        "article_id"
    )

    if article_id_index is None:
        raise ValueError(
            "Briefings sheet is missing "
            "article_id column."
        )

    for row_number, row in enumerate(
        values[1:],
        start=2,
    ):
        if (
            article_id_index < len(row)
            and str(
                row[article_id_index]
            ).strip()
            == str(article_id).strip()
        ):
            return {
                "row_number": row_number,
                "row": row,
                "mapping": mapping,
            }

    return None


# ============================================================
# SAVE BRIEFING
# ============================================================

def save_briefing(
    worksheet,
    article,
    briefing,
    model,
):
    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    row = [
        normalize_cell(
            article["article_id"]
        ),
        normalize_cell(
            article["source"]
        ),
        normalize_cell(
            article["title"]
        ),
        normalize_cell(
            article["url"]
        ),
        normalize_cell(
            article["published_at"]
        ),
        normalize_cell(
            article["rule_score"]
        ),
        normalize_cell(
            article["topics"]
        ),
        normalize_cell(
            briefing["summary"]
        ),
        normalize_cell(
            briefing["key_points"]
        ),
        normalize_cell(
            briefing["why_it_matters"]
        ),
        normalize_cell(
            briefing["implications"]
        ),
        normalize_cell(
            model
        ),
        normalize_cell(
            created_at
        ),
        "BRIEFED",
    ]

    if len(row) != len(
        BRIEFING_FIELDS
    ):
        raise ValueError(
            "Briefing row has "
            f"{len(row)} columns, "
            f"expected "
            f"{len(BRIEFING_FIELDS)}."
        )

    worksheet.append_row(
        row,
        value_input_option="USER_ENTERED",
    )


# ============================================================
# PROCESS ARTICLES
# ============================================================

def process_articles(
    spreadsheet,
    client,
    model,
    prompt_template,
    max_content_chars,
    language,
):
    articles_worksheet = (
        spreadsheet.worksheet(
            ARTICLES_SHEET
        )
    )

    briefings_worksheet = (
        spreadsheet.worksheet(
            BRIEFINGS_SHEET
        )
    )

    article_values = (
        articles_worksheet.get_all_values()
    )

    if not article_values:
        print(
            "[BRIEFING] "
            "Articles sheet is empty."
        )
        return

    article_headers = article_values[0]

    article_mapping = header_map(
        article_headers
    )

    required_fields = [
        "article_id",
        "source",
        "title",
        "url",
        "published_at",
        "rule_score",
        "topics",
        "status",
    ]

    for field in required_fields:
        if field not in article_mapping:
            raise ValueError(
                "Articles sheet is missing "
                f"column: {field}"
            )

    selected = []

    for row_number, row in enumerate(
        article_values[1:],
        start=2,
    ):
        status = row_value(
            row,
            article_mapping,
            "status",
        ).strip().upper()

        if status != ARTICLE_STATUS_SELECTED:
            continue

        selected.append({
            "row_number": row_number,
            "article_id": row_value(
                row,
                article_mapping,
                "article_id",
            ),
            "source": row_value(
                row,
                article_mapping,
                "source",
            ),
            "title": row_value(
                row,
                article_mapping,
                "title",
            ),
            "url": row_value(
                row,
                article_mapping,
                "url",
            ),
            "published_at": row_value(
                row,
                article_mapping,
                "published_at",
            ),
            "rule_score": row_value(
                row,
                article_mapping,
                "rule_score",
            ),
            "topics": row_value(
                row,
                article_mapping,
                "topics",
            ),
        })

    print(
        f"[BRIEFING] "
        f"Selected articles: {len(selected)}"
    )

    if not selected:
        print(
            "[BRIEFING] "
            "No selected articles."
        )
        return

    successful = 0
    failed = 0
    skipped = 0

    for article in selected:
        article_id = article[
            "article_id"
        ]

        title = article[
            "title"
        ]

        url = article[
            "url"
        ]

        print()
        print(
            f"[BRIEFING] {title}"
        )

        # ----------------------------------------------------
        # DUPLICATE CHECK
        # ----------------------------------------------------

        try:
            existing = (
                find_briefing_by_article_id(
                    briefings_worksheet,
                    article_id,
                )
            )
        except Exception as exc:
            print(
                "[WARNING] Could not check "
                f"existing briefing: {exc}"
            )
            existing = None

        if existing:
            mapping = existing[
                "mapping"
            ]

            status_index = mapping.get(
                "status"
            )

            existing_status = ""

            if (
                status_index is not None
                and status_index
                < len(existing["row"])
            ):
                existing_status = (
                    existing["row"][
                        status_index
                    ]
                    .strip()
                    .upper()
                )

            if existing_status == "BRIEFED":
                print(
                    "[SKIP] "
                    "Briefing already exists."
                )

                update_article_status(
                    articles_worksheet,
                    article["row_number"],
                    article_mapping,
                    ARTICLE_STATUS_BRIEFED,
                )

                skipped += 1
                continue

        try:
            # ------------------------------------------------
            # FETCH
            # ------------------------------------------------

            print(
                f"[FETCH] {url}"
            )

            article_text = fetch_article(
                url
            )

            if not article_text:
                raise ValueError(
                    "No article text extracted."
                )

            if len(article_text) > (
                max_content_chars
            ):
                article_text = (
                    article_text[
                        :max_content_chars
                    ]
                )

                print(
                    "[FETCH] Content truncated "
                    f"to {max_content_chars} "
                    "characters."
                )

            print(
                "[FETCH] "
                f"{len(article_text)} "
                "characters extracted."
            )

            # ------------------------------------------------
            # AI
            # ------------------------------------------------

            print(
                "[AI] Generating "
                "structured briefing..."
            )

            briefing = generate_briefing(
                client=client,
                model=model,
                prompt_template=prompt_template,
                title=title,
                source=article["source"],
                topics=article["topics"],
                article_text=article_text,
                language=language,
            )

            print(
                "[AI] Structured briefing "
                "generated."
            )

            # ------------------------------------------------
            # VALIDATE
            # ------------------------------------------------

            validate_briefing(
                briefing
            )

            print(
                "[VALIDATE] "
                "Briefing schema OK."
            )

            # ------------------------------------------------
            # SAVE
            # ------------------------------------------------

            save_briefing(
                worksheet=briefings_worksheet,
                article=article,
                briefing=briefing,
                model=model,
            )

            update_article_status(
                articles_worksheet,
                article["row_number"],
                article_mapping,
                ARTICLE_STATUS_BRIEFED,
            )

            print(
                "[SAVE] "
                "Briefing saved."
            )

            successful += 1

        except Exception as exc:
            failed += 1

            print(
                f"[ERROR] {title}: "
                f"{type(exc).__name__}: {exc}"
            )

            try:
                update_article_status(
                    articles_worksheet,
                    article["row_number"],
                    article_mapping,
                    ARTICLE_STATUS_BRIEFING_ERROR,
                )
            except Exception as status_exc:
                print(
                    "[ERROR] Could not update "
                    "article status: "
                    f"{status_exc}"
                )

        time.sleep(0.5)

    print()
    print("=" * 60)
    print(
        "BRIEFING SUMMARY"
    )
    print("=" * 60)
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
        f"Skipped: {skipped}"
    )
    print("=" * 60)


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print(
        f"AI BRIEFING ENGINE v{APP_VERSION}"
    )
    print("=" * 60)

    spreadsheet = get_spreadsheet()

    settings = load_settings(
        spreadsheet
    )

    # --------------------------------------------------------
    # MODEL
    # --------------------------------------------------------

    model = get_setting(
        settings,
        "briefing_model",
        get_setting(
            settings,
            "model",
            DEFAULT_MODEL,
        ),
    )

    # --------------------------------------------------------
    # LANGUAGE
    # --------------------------------------------------------

    language = get_setting(
        settings,
        "briefing_language",
        get_setting(
            settings,
            "language",
            DEFAULT_LANGUAGE,
        ),
    )

    # --------------------------------------------------------
    # CONTENT LIMIT
    # --------------------------------------------------------

    max_content_chars = (
        get_int_setting(
            settings,
            "briefing_max_content_chars",
            get_int_setting(
                settings,
                "max_content_chars",
                DEFAULT_MAX_CONTENT_CHARS,
            ),
        )
    )

    # --------------------------------------------------------
    # PROMPT
    # --------------------------------------------------------

    prompt_template = get_setting(
        settings,
        "briefing_prompt",
        DEFAULT_BRIEFING_PROMPT,
    )

    if not prompt_template.strip():
        raise ValueError(
            "Settings.briefing_prompt "
            "is empty."
        )

    # --------------------------------------------------------
    # LOG SETTINGS
    # --------------------------------------------------------

    print(
        f"[SETTINGS] model={model}"
    )

    print(
        "[SETTINGS] "
        f"max_content_chars="
        f"{max_content_chars}"
    )

    print(
        f"[SETTINGS] language={language}"
    )

    print(
        "[SETTINGS] "
        "prompt=loaded from Settings"
    )

    # --------------------------------------------------------
    # OPENAI
    # --------------------------------------------------------

    client = get_openai_client()

    # --------------------------------------------------------
    # PROCESS
    # --------------------------------------------------------

    process_articles(
        spreadsheet=spreadsheet,
        client=client,
        model=model,
        prompt_template=prompt_template,
        max_content_chars=max_content_chars,
        language=language,
    )


if __name__ == "__main__":
    main()
