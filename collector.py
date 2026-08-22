

Đã dán markdown (1).md
Tệp
Bạn xem nhé


Văn bản đã dán (1).txt
Tài liệu


Văn bản đã dán (1)(1).txt
Tài liệu


Đã dán markdown (1)(1).md
Tệp
Tôi thấy ko có score và topics

Tốt. Parser AHRQ đã chạy thành công. Đây là kết quả chúng ta cần ở bước này.

Có một lỗi nhỏ cần sửa trước khi tích hợp:

1. View the Weekly Issue
https://psnet.ahrq.gov#view-weekly-issue
Đây không phải bài viết. Parser đang coi anchor điều hướng này là article. 15 bài còn lại đều là các entry thực sự trong Featured Articles.

Tôi sẽ sửa parser để loại các link dạng này
Cụ thể loại:

#view-weekly-issue
và các anchor điều hướng tương tự.

Kết quả mong muốn:

[AHRQ PSNet] 15 featured articles found.

1. Medical large language models...
2. A framework for the analysis...
3. Adverse events involving telehealth...
...
15. Lessons from Event Reports.
Có một phát hiện rất quan trọng
Parser xác nhận AHRQ Weekly Resource hiện đang có issue date 12/03/2025.

Do đó:

Settings
lookback_days = 7
sẽ đúng khi loại toàn bộ 15 bài này khỏi daily collection.

Đây không phải lỗi parser.

Nó cho chúng ta thấy AHRQ hiện tại không phù hợp để sử dụng theo mô hình:

"tin mới trong 7 ngày"

nhưng lại rất phù hợp với mô hình knowledge source.

Và đây là lý do tôi muốn giữ kiến trúc source-specific parser: sau này chúng ta có thể quyết định:

NHS RSS
→ published_at thực tế

AHRQ
→ issue_date / publication date
→ chỉ lấy khi issue mới xuất hiện
Tiếp theo: tích hợp AHRQ vào Collector
Bây giờ chúng ta có thể làm bước quan trọng tiếp theo:

collector.py
       │
       ├── RSS
       │    ├── NHS Transformation
       │    └── NHS Tech Talk
       │
       └── WEB parser
            └── AHRQ PSNet
Sau đó thêm Runs:

Run #1
22/08 22:30
SUCCESS
0 articles

       ↓

Run #2
23/08 22:30
       ↓
last successful run
       ↓
chỉ lấy bài mới
Tôi đề xuất chưa dùng lookback_days làm cơ chế chính
Ta sẽ dùng:

last successful run = checkpoint chính

và:

lookback_days = 7 = safety window

Ví dụ:

last successful run
23/08 22:30

lookback_days
7 days

        ↓

Collector kiểm tra khoảng:
16/08 → hiện tại

        ↓

chỉ giữ:
published_at > checkpoint
Như vậy nếu GitHub Actions bị lỗi vài ngày, hệ thống vẫn có cơ hội bắt lại bài trong 7 ngày gần nhất.

Trạng thái hiện tại của project
✅ Google Sheets connection
✅ Sources configuration
✅ Topics configuration
✅ Settings
✅ RSS collector
✅ Date parser
✅ 7-day freshness filter
✅ AHRQ web parser
⬜ Runs / checkpoint
⬜ Multi-source Collector V0.3
⬜ Rule Engine V0.2
⬜ AI evaluation
⬜ Translation + summary
⬜ Telegram briefing
Bước tiếp theo tôi sẽ làm là tích hợp ahrq_psnet.py + Runs vào collector.py. Sau bước đó chúng ta sẽ có một Collector V0.3 hoàn chỉnh trước khi đụng tới Rule Engine.


Oke làm đi

Được. Ta tích hợp AHRQ parser + Runs checkpoint vào Collector V0.3.

Nhưng tôi muốn làm theo cách an toàn: không thay toàn bộ collector.py bằng một file mới, vì file hiện tại của bạn đã chạy được. Ta chỉ thay các phần cần thiết.

1. Sửa parsers/ahrq_psnet.py trước
Trong hàm is_valid_article_link(), thêm loại link:

if href.startswith("#"):
    return False
hoặc đơn giản hơn, ngay sau khi lấy href:

href = current.get(
    "href",
    "",
)

if href.startswith("#"):
    continue
Như vậy:

View the Weekly Issue
sẽ bị loại.

Commit thay đổi này trước.

2. Sửa collector.py
A. Import parser
Ở đầu collector.py, phần:

from parsers import himss
đổi thành:

from parsers import himss
from parsers import ahrq_psnet
3. Thêm cấu hình Runs
Sau:

ARTICLES_SHEET = "Articles"
thêm:

RUNS_SHEET = "Runs"
4. Thêm hàm lấy checkpoint
Đặt trước phần SOURCES:

def get_last_successful_run(
    spreadsheet,
):
    worksheet = spreadsheet.worksheet(
        RUNS_SHEET
    )

    records = worksheet.get_all_records()

    successful_runs = []

    for row in records:
        status = clean_text(
            row.get(
                "status",
                "",
            )
        ).upper()

        started_at = clean_text(
            row.get(
                "started_at",
                "",
            )
        )

        if (
            status == "SUCCESS"
            and started_at
        ):
            successful_runs.append(
                started_at
            )

    if not successful_runs:
        return None

    successful_runs.sort(
        reverse=True
    )

    return successful_runs[0]
5. Thêm hàm ghi Run
Đặt ngay sau hàm trên:

def create_run(
    spreadsheet,
):
    worksheet = spreadsheet.worksheet(
        RUNS_SHEET
    )

    started_at = utc_now()

    run_id = (
        started_at
        .replace(
            "-",
            "",
        )
        .replace(
            ":",
            "",
        )
        .replace(
            ".",
            "",
        )
        .replace(
            "+00:00",
            "",
        )
    )

    worksheet.append_row(
        [
            run_id,
            started_at,
            "",
            "RUNNING",
            0,
        ],
        value_input_option="USER_ENTERED",
    )

    return run_id, started_at
6. Thêm hàm hoàn thành Run
Ngay sau create_run():

def complete_run(
    spreadsheet,
    run_id,
    started_at,
    status,
    articles_found,
):
    worksheet = spreadsheet.worksheet(
        RUNS_SHEET
    )

    records = worksheet.get_all_records()

    for index, row in enumerate(
        records,
        start=2,
    ):
        if clean_text(
            row.get(
                "run_id",
                "",
            )
        ) != run_id:
            continue

        worksheet.update(
            f"C{index}:E{index}",
            [
                [
                    utc_now(),
                    status,
                    articles_found,
                ]
            ],
        )

        return
7. Sửa collect_source()
Hiện tại bạn có:

if name.lower() == "himss":
    return himss.collect(
        source
    )
thêm AHRQ ngay trước đó:

if name.lower() == "ahrq psnet":
    return ahrq_psnet.collect(
        source
    )
Thành:

if name.lower() == "ahrq psnet":
    return ahrq_psnet.collect(
        source
    )

if name.lower() == "himss":
    return himss.collect(
        source
    )
8. Sửa logic main()
Đây là phần quan trọng nhất.

Sau:

spreadsheet = get_google_client()
và trước khi bắt đầu lấy sources, thêm:

run_id, started_at = create_run(
    spreadsheet
)

last_successful_run = (
    get_last_successful_run(
        spreadsheet
    )
)

print(
    f"Run ID: {run_id}"
)

print(
    "Last successful run: "
    f"{last_successful_run or 'NONE'}"
)
9. Sửa freshness filter
Hiện tại bạn có:

recent_articles = [
    article
    for article in normalized
    if is_recent_article(
        article.get(
            "published_at",
            "",
        ),
        lookback_days,
    )
]
Thay bằng:

recent_articles = []

for article in normalized:

    published_at = article.get(
        "published_at",
        "",
    )

    if not is_recent_article(
        published_at,
        lookback_days,
    ):
        continue

    published = parse_published_date(
        published_at
    )

    if (
        last_successful_run
        and published
    ):
        checkpoint = (
            parse_published_date(
                last_successful_run
            )
        )

        if (
            checkpoint
            and published <= checkpoint
        ):
            continue

    recent_articles.append(
        article
    )
Điểm quan trọng ở đây:

lookback_days
là safety window.

Còn:

published_at > last_successful_run
là checkpoint chính.

10. Có một trường hợp đặc biệt với AHRQ
AHRQ hiện trả:

published_at =
2025-03-12
Vì vậy nó sẽ bị lookback_days = 7 loại ra.

Điều này đúng.

Sau này khi AHRQ có Weekly Issue mới, ví dụ:

2026-08-28
parser sẽ tự lấy:

published_at = 2026-08-28
và Collector sẽ nhận nó.

Không cần logic riêng cho AHRQ.

11. Quan trọng: nếu Collector lỗi thì Run phải thành FAILED
Hiện main() của bạn đang bắt lỗi theo từng source, nhưng nếu lỗi nghiêm trọng xảy ra bên ngoài vòng source thì Run có thể bị kẹt ở RUNNING.

Vì vậy bọc phần chính trong:

try:
    ...
except Exception:
    complete_run(
        spreadsheet,
        run_id,
        started_at,
        "FAILED",
        0,
    )
    raise
Còn khi Collector chạy xong:

complete_run(
    spreadsheet,
    run_id,
    started_at,
    "SUCCESS",
    saved,
)
Quan trọng
Chỉ SUCCESS mới trở thành checkpoint.

SUCCESS
   ↓
được dùng làm last_successful_run

FAILED
   ↓
bỏ qua
Đây là nguyên tắc rất quan trọng để không mất bài.

12. Sau khi sửa xong, test
Bạn xóa dữ liệu trong Articles, nhưng KHÔNG xóa Runs nếu đã có run thành công mà bạn muốn giữ.

Tuy nhiên vì đây là lần đầu triển khai checkpoint, tôi khuyên:

Runs
Xóa dữ liệu test cũ nếu có, giữ header:

run_id
started_at
completed_at
status
articles_found
Sau đó chạy Collector.

Lần chạy đầu tiên
Log sẽ giống:

Run ID: 20260822...
Last successful run: NONE

Lookback period: 7 days

NHS England — Transformation:
0 recent articles

NHS England — Tech Talk:
0 recent articles

AHRQ PSNet:
0 recent articles

Total recent articles: 0
Runs:

run_id	started_at	completed_at	status	articles_found
...	...	...	SUCCESS	0
Lần chạy thứ hai
Giả sử có bài mới:

Last successful run: 2026-08-22T22:30:xx
Collector sẽ chỉ nhận:

published_at > 2026-08-22T22:30:xx
và vẫn không vượt quá safety window 7 ngày.

Một điểm tôi muốn giữ nguyên
Chưa chạy Rule Engine.

Sau khi Collector V0.3 chạy thành công, chúng ta kiểm tra 3 thứ:

Runs có ghi SUCCESS.

Articles chỉ có bài mới.

Chạy lần thứ hai không tạo duplicate.

Nếu cả ba đúng, Collector coi như hoàn thành V0.3. Sau đó mới quay lại Rule Engine và xử lý matched_topics + rule_score.


Cập nhật cho tôi file collector.py final đi

Đã cập nhật collector.py V0.3 hoàn chỉnh và kiểm tra syntax trước khi gửi.

Download collector.py

Bản này đã tích hợp:

Settings → lookback_days

Runs checkpoint

last_successful_run

RSS collector

AHRQ PSNet parser riêng

HIMSS parser hiện có

deduplication theo article_id

RUNNING → SUCCESS / FAILED

chỉ dùng run SUCCESS làm checkpoint

published_at > last_successful_run

safety window lookback_days

schema Articles mới với matched_topics, rule_score, matched_keywords

date parser cho RSS, ISO 8601 và format như March 12, 2025

Bây giờ bạn làm
Thay toàn bộ file hiện tại trên GitHub bằng file này:

report_medical_news/
├── collector.py        ← thay file này
├── parsers/
│   ├── __init__.py
│   ├── ahrq_psnet.py
│   └── himss.py
Sau đó commit.

Không cần sửa rule_engine.py lúc này.

Trước khi chạy Collector
Kiểm tra Runs có đúng header:

run_id | started_at | completed_at | status | articles_found
và Articles:

article_id
source
title
url
published_at
excerpt
matched_topics
rule_score
matched_keywords
status
discovered_at
Sau đó chạy workflow Collector.
Lần chạy đầu tiên, log quan trọng nhất là:

Last successful run: NONE
sau đó:

AHRQ PSNet: ...
NHS England — Transformation: ...
NHS England — Tech Talk: ...
và cuối cùng:

Run XXXXX completed successfully.
Lưu ý: Với dữ liệu hiện tại, việc AHRQ trả 0 recent/new là bình thường vì issue gần nhất parser tìm thấy là 12/03/2025. Ta đang kiểm tra pipeline, không kỳ vọng AHRQ có bài mới ngay.

Sau khi chạy xong, gửi tôi toàn bộ phần COLLECTION SUMMARY + nội dung tab Runs. Tôi sẽ kiểm tra checkpoint V0.3 trước khi chúng ta quay lại Rule Engine. 


collector.py
Code


Thư viện
/
collector.py


import json
import os
import hashlib
from datetime import datetime, timezone, timedelta

import feedparser
import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials

from parsers import himss
from parsers import ahrq_psnet


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SOURCES_SHEET = "Sources"
SETTINGS_SHEET = "Settings"
ARTICLES_SHEET = "Articles"
RUNS_SHEET = "Runs"

REQUEST_TIMEOUT = 30

USER_AGENT = (
    "ReportMedicalNews/0.3 "
    "(Healthcare Operations Intelligence)"
)


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


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def make_article_id(url):
    return hashlib.sha256(
        url.strip().encode("utf-8")
    ).hexdigest()[:16]


def normalize_url(
    url,
    base_url=None,
):
    url = clean_text(url)

    if not url:
        return ""

    if base_url:
        from urllib.parse import urljoin

        return urljoin(
            base_url,
            url,
        )

    return url


# ============================================================
# SETTINGS
# ============================================================

def get_setting(
    spreadsheet,
    key,
    default=None,
):
    worksheet = spreadsheet.worksheet(
        SETTINGS_SHEET
    )

    records = worksheet.get_all_records()

    for row in records:
        row_key = clean_text(
            row.get(
                "key",
                "",
            )
        )

        if row_key == key:
            value = clean_text(
                row.get(
                    "value",
                    "",
                )
            )

            return value

    return default


def get_lookback_days(
    spreadsheet,
):
    value = get_setting(
        spreadsheet,
        "lookback_days",
        7,
    )

    try:
        days = int(value)

        if days < 0:
            raise ValueError

        return days

    except (TypeError, ValueError):
        print(
            "Invalid lookback_days. "
            "Using default: 7"
        )

        return 7


# ============================================================
# DATE PARSING
# ============================================================

def parse_published_date(
    published_at,
):
    """
    Parse common publication-date formats.

    Supported examples:
    - Wed, 13 May 2026 15:37:53 GMT
    - Wed, 13 May 2026 15:37:53 +0000
    - March 12, 2025
    - March 12, 2025 10:30 AM
    - 2026-08-22T10:30:00Z
    - 2026-08-22T10:30:00+07:00
    """

    if not published_at:
        return None

    value = clean_text(
        published_at
    )

    if not value:
        return None

    # RFC 2822 / RSS
    try:
        from email.utils import (
            parsedate_to_datetime
        )

        parsed = parsedate_to_datetime(
            value
        )

        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(
                    tzinfo=timezone.utc
                )

            return parsed.astimezone(
                timezone.utc
            )

    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        pass

    # ISO 8601
    try:
        iso_value = value

        if iso_value.endswith("Z"):
            iso_value = (
                iso_value[:-1]
                + "+00:00"
            )

        parsed = datetime.fromisoformat(
            iso_value
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(
            timezone.utc
        )

    except (
        TypeError,
        ValueError,
    ):
        pass

    # Month-name formats
    date_formats = [
        "%B %d, %Y",
        "%B %d, %Y %I:%M %p",
        "%b %d, %Y",
        "%b %d, %Y %I:%M %p",
    ]

    for date_format in date_formats:
        try:
            parsed = datetime.strptime(
                value,
                date_format,
            )

            return parsed.replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            pass

    # Date-only formats
    date_only_formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
    ]

    for date_format in date_only_formats:
        try:
            parsed = datetime.strptime(
                value,
                date_format,
            )

            return parsed.replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            pass

    print(
        f"[DATE] Unable to parse: "
        f"{value}"
    )

    return None


def is_recent_article(
    published_at,
    lookback_days,
):
    if not published_at:
        print(
            "[DATE] Missing published_at. "
            "Article will be excluded."
        )
        return False

    published = parse_published_date(
        published_at
    )

    if published is None:
        print(
            f"[DATE] Could not parse "
            f"published_at: {published_at}"
        )
        return False

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(
            days=lookback_days
        )
    )

    is_recent = (
        published >= cutoff
    )

    if not is_recent:
        print(
            f"[DATE] Excluding old article: "
            f"{published_at}"
        )

    return is_recent


# ============================================================
# RUN CHECKPOINT
# ============================================================

def get_last_successful_run(
    spreadsheet,
):
    worksheet = spreadsheet.worksheet(
        RUNS_SHEET
    )

    records = worksheet.get_all_records()

    successful_runs = []

    for row in records:
        status = clean_text(
            row.get(
                "status",
                "",
            )
        ).upper()

        started_at = clean_text(
            row.get(
                "started_at",
                "",
            )
        )

        if (
            status == "SUCCESS"
            and started_at
        ):
            successful_runs.append(
                started_at
            )

    if not successful_runs:
        return None

    # ISO timestamps sort correctly
    # when normalized consistently.
    successful_runs.sort(
        reverse=True
    )

    return successful_runs[0]


def create_run(
    spreadsheet,
):
    worksheet = spreadsheet.worksheet(
        RUNS_SHEET
    )

    started_at = utc_now()

    run_id = (
        started_at
        .replace("-", "")
        .replace(":", "")
        .replace(".", "")
        .replace("+00:00", "")
    )

    worksheet.append_row(
        [
            run_id,
            started_at,
            "",
            "RUNNING",
            0,
        ],
        value_input_option="USER_ENTERED",
    )

    return run_id, started_at


def complete_run(
    spreadsheet,
    run_id,
    status,
    articles_found,
):
    worksheet = spreadsheet.worksheet(
        RUNS_SHEET
    )

    records = worksheet.get_all_records()

    for index, row in enumerate(
        records,
        start=2,
    ):
        if clean_text(
            row.get(
                "run_id",
                "",
            )
        ) != run_id:
            continue

        worksheet.update(
            f"C{index}:E{index}",
            [
                [
                    utc_now(),
                    status,
                    articles_found,
                ]
            ],
        )

        return

    raise RuntimeError(
        f"Could not find run_id "
        f"{run_id} in Runs."
    )


# ============================================================
# SOURCES
# ============================================================

def get_active_sources(
    spreadsheet,
):
    worksheet = spreadsheet.worksheet(
        SOURCES_SHEET
    )

    records = worksheet.get_all_records()

    sources = []

    for row in records:
        active = (
            str(
                row.get(
                    "active",
                    "",
                )
            )
            .strip()
            .lower()
        )

        if active not in (
            "true",
            "1",
            "yes",
        ):
            continue

        name = clean_text(
            row.get(
                "name",
                "",
            )
        )

        access_method = (
            clean_text(
                row.get(
                    "access_method",
                    "",
                )
            )
            .upper()
        )

        if not name:
            continue

        if access_method not in (
            "RSS",
            "WEB",
            "API",
        ):
            print(
                f"Skipping {name}: "
                f"unsupported access_method "
                f"'{access_method}'"
            )
            continue

        sources.append(
            row
        )

    return sources


# ============================================================
# RSS COLLECTOR
# ============================================================

def collect_rss(source):
    name = clean_text(
        source.get("name")
    )

    feed_url = clean_text(
        source.get("feed_url")
    )

    if not feed_url:
        raise ValueError(
            "RSS source has no feed_url"
        )

    print(
        f"[RSS] Fetching {name}: "
        f"{feed_url}"
    )

    feed = feedparser.parse(
        feed_url
    )

    if getattr(
        feed,
        "bozo",
        False,
    ):
        print(
            f"[RSS] Warning for {name}: "
            "feed parser reported an issue."
        )

    articles = []

    for entry in feed.entries:
        title = clean_text(
            entry.get(
                "title",
                "",
            )
        )

        url = normalize_url(
            entry.get(
                "link",
                "",
            )
        )

        if not title or not url:
            continue

        published_at = clean_text(
            entry.get(
                "published",
                "",
            )
            or entry.get(
                "updated",
                "",
            )
        )

        excerpt = clean_text(
            entry.get(
                "summary",
                "",
            )
        )

        articles.append(
            {
                "source": name,
                "title": title,
                "url": url,
                "published_at": published_at,
                "excerpt": excerpt,
            }
        )

    print(
        f"[RSS] {name}: "
        f"{len(articles)} articles found."
    )

    return articles


# ============================================================
# GENERIC WEB COLLECTOR
# ============================================================

def collect_web(source):
    name = clean_text(
        source.get("name")
    )

    listing_url = clean_text(
        source.get("listing_url")
    )

    if not listing_url:
        raise ValueError(
            "WEB source has no listing_url"
        )

    print(
        f"[WEB] Fetching {name}: "
        f"{listing_url}"
    )

    headers = {
        "User-Agent": USER_AGENT
    }

    response = requests.get(
        listing_url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    articles = []
    seen_urls = set()

    for link in soup.find_all(
        "a",
        href=True,
    ):
        href = link.get("href")
        title = clean_text(
            link.get_text(
                " ",
                strip=True,
            )
        )

        if not href or not title:
            continue

        url = normalize_url(
            href,
            listing_url,
        )

        if not url or url in seen_urls:
            continue

        if href.startswith("#"):
            continue

        lowered_url = url.lower()

        ignored_patterns = [
            "/search",
            "/login",
            "/contact",
            "/about",
            "/events",
            "/membership",
            "/privacy",
            "/terms",
            "javascript:",
            "#",
        ]

        if any(
            pattern in lowered_url
            for pattern in ignored_patterns
        ):
            continue

        if len(title) < 20:
            continue

        if len(title) > 300:
            continue

        seen_urls.add(url)

        articles.append(
            {
                "source": name,
                "title": title,
                "url": url,
                "published_at": "",
                "excerpt": "",
            }
        )

    print(
        f"[WEB] {name}: "
        f"{len(articles)} candidate articles found."
    )

    return articles


# ============================================================
# API PLACEHOLDER
# ============================================================

def collect_api(source):
    name = clean_text(
        source.get("name")
    )

    raise NotImplementedError(
        f"API collector is not implemented yet "
        f"for source: {name}"
    )


# ============================================================
# SOURCE DISPATCHER
# ============================================================

def collect_source(source):
    name = clean_text(
        source.get(
            "name",
            "",
        )
    )

    access_method = (
        clean_text(
            source.get(
                "access_method",
                "",
            )
        )
        .upper()
    )

    if name.lower() == "ahrq psnet":
        return ahrq_psnet.collect(
            source
        )

    if name.lower() == "himss":
        return himss.collect(
            source
        )

    if access_method == "RSS":
        return collect_rss(
            source
        )

    if access_method == "WEB":
        return collect_web(
            source
        )

    if access_method == "API":
        return collect_api(
            source
        )

    raise ValueError(
        f"Unsupported access method: "
        f"{access_method}"
    )


# ============================================================
# NORMALIZE ARTICLES
# ============================================================

def normalize_articles(
    articles,
):
    normalized = []

    for article in articles:
        url = clean_text(
            article.get(
                "url",
                "",
            )
        )

        title = clean_text(
            article.get(
                "title",
                "",
            )
        )

        if not url or not title:
            continue

        normalized.append(
            {
                "article_id": make_article_id(
                    url
                ),
                "source": clean_text(
                    article.get(
                        "source",
                        "",
                    )
                ),
                "title": title,
                "url": url,
                "published_at": clean_text(
                    article.get(
                        "published_at",
                        "",
                    )
                ),
                "excerpt": clean_text(
                    article.get(
                        "excerpt",
                        "",
                    )
                ),
                "status": "DISCOVERED",
                "discovered_at": utc_now(),
            }
        )

    return normalized


# ============================================================
# SAVE ARTICLES
# ============================================================

def get_existing_article_ids(
    spreadsheet,
):
    worksheet = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    records = worksheet.get_all_records()

    return {
        clean_text(
            row.get(
                "article_id",
                "",
            )
        )
        for row in records
        if row.get(
            "article_id",
            "",
        )
    }


def save_articles(
    spreadsheet,
    articles,
):
    worksheet = spreadsheet.worksheet(
        ARTICLES_SHEET
    )

    existing_ids = (
        get_existing_article_ids(
            spreadsheet
        )
    )

    unique_articles = {}

    for article in articles:
        article_id = article[
            "article_id"
        ]

        if article_id in existing_ids:
            continue

        unique_articles[
            article_id
        ] = article

    new_articles = list(
        unique_articles.values()
    )

    if not new_articles:
        print(
            "No new articles to save."
        )
        return 0

    rows = []

    for article in new_articles:
        rows.append(
            [
                article[
                    "article_id"
                ],
                article[
                    "source"
                ],
                article[
                    "title"
                ],
                article[
                    "url"
                ],
                article[
                    "published_at"
                ],
                article[
                    "excerpt"
                ],
                "",
                "",
                "",
                article[
                    "status"
                ],
                article[
                    "discovered_at"
                ],
            ]
        )

    worksheet.append_rows(
        rows,
        value_input_option="USER_ENTERED",
    )

    print(
        f"Saved {len(rows)} new articles."
    )

    return len(rows)


# ============================================================
# MAIN
# ============================================================

def main():
    spreadsheet = get_google_client()

    print(
        f"Connected to: "
        f"{spreadsheet.title}"
    )

    lookback_days = get_lookback_days(
        spreadsheet
    )

    print(
        f"Lookback period: "
        f"{lookback_days} days"
    )

    run_id, started_at = create_run(
        spreadsheet
    )

    last_successful_run = (
        get_last_successful_run(
            spreadsheet
        )
    )

    print(
        f"Run ID: {run_id}"
    )

    print(
        "Last successful run: "
        f"{last_successful_run or 'NONE'}"
    )

    try:
        sources = get_active_sources(
            spreadsheet
        )

        print(
            f"Active sources: "
            f"{len(sources)}"
        )

        all_articles = []
        source_results = []

        checkpoint = None

        if last_successful_run:
            checkpoint = parse_published_date(
                last_successful_run
            )

        for source in sources:
            name = clean_text(
                source.get(
                    "name",
                    "Unknown",
                )
            )

            try:
                candidates = collect_source(
                    source
                )

                normalized = normalize_articles(
                    candidates
                )

                recent_articles = []

                for article in normalized:
                    published_at = article.get(
                        "published_at",
                        "",
                    )

                    if not is_recent_article(
                        published_at,
                        lookback_days,
                    ):
                        continue

                    published = (
                        parse_published_date(
                            published_at
                        )
                    )

                    if (
                        checkpoint is not None
                        and published is not None
                        and published <= checkpoint
                    ):
                        print(
                            "[CHECKPOINT] "
                            f"Skipping article at "
                            f"or before last successful run: "
                            f"{published_at}"
                        )
                        continue

                    recent_articles.append(
                        article
                    )

                print(
                    f"[{name}] "
                    f"{len(recent_articles)} recent/new "
                    f"articles out of "
                    f"{len(normalized)} candidates."
                )

                all_articles.extend(
                    recent_articles
                )

                source_results.append(
                    {
                        "name": name,
                        "status": "OK",
                        "count": len(
                            recent_articles
                        ),
                        "candidates": len(
                            normalized
                        ),
                        "error": "",
                    }
                )

            except Exception as e:
                print(
                    f"[ERROR] {name}: {e}"
                )

                source_results.append(
                    {
                        "name": name,
                        "status": "ERROR",
                        "count": 0,
                        "candidates": 0,
                        "error": str(e),
                    }
                )

        print("")
        print("=" * 60)
        print("COLLECTION SUMMARY")
        print("=" * 60)

        for result in source_results:
            print(
                f"{result['name']}: "
                f"{result['status']} "
                f"({result['count']} recent/new / "
                f"{result['candidates']} candidates)"
            )

            if result["error"]:
                print(
                    f"  Error: "
                    f"{result['error']}"
                )

        print("=" * 60)

        print(
            f"Total recent/new articles: "
            f"{len(all_articles)}"
        )

        saved = save_articles(
            spreadsheet,
            all_articles,
        )

        print(
            f"New articles saved: "
            f"{saved}"
        )

        complete_run(
            spreadsheet,
            run_id,
            "SUCCESS",
            saved,
        )

        print(
            f"Run {run_id} completed "
            f"successfully."
        )

    except Exception:
        try:
            complete_run(
                spreadsheet,
                run_id,
                "FAILED",
                0,
            )
        except Exception as run_error:
            print(
                "[RUN] Could not mark run "
                f"as FAILED: {run_error}"
            )

        raise


if __name__ == "__main__":
    main()
