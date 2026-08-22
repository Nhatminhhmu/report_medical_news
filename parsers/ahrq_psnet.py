"""
AHRQ PSNet web parser.

Purpose:
    Collect featured articles from the PSNet Weekly Resource page.

The PSNet page currently exposes a weekly issue containing:
    - issue date
    - article type (Study, Commentary, Review, etc.)
    - article title
    - article URL
    - citation / metadata
    - short description

The parser deliberately does NOT put article_type into the common
Articles schema. It is used only internally while extracting the page.
The common collector receives title, URL, published_at and excerpt.
"""

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://psnet.ahrq.gov"
DEFAULT_LISTING_URL = (
    "https://psnet.ahrq.gov/periodic-issue/weekly-resource"
)

REQUEST_TIMEOUT = 30

USER_AGENT = (
    "ReportMedicalNews/0.3 "
    "(Healthcare Operations Intelligence)"
)


def clean_text(value):
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def parse_issue_date(text):
    """
    Parse:
        March 12, 2025 Weekly Issue
    """
    text = clean_text(text)

    match = re.search(
        r"([A-Za-z]+ \d{1,2}, \d{4})\s+Weekly Issue",
        text,
        re.IGNORECASE,
    )

    if not match:
        return ""

    try:
        parsed = datetime.strptime(
            match.group(1),
            "%B %d, %Y",
        )

        return parsed.replace(
            tzinfo=timezone.utc
        ).isoformat()

    except ValueError:
        return ""


def is_valid_article_link(url, title):
    if not url or not title:
        return False

    title = clean_text(title)

    if len(title) < 20:
        return False

    lowered = url.lower()

    blocked = (
        "/periodic-issue/",
        "/taxonomy/",
        "/about",
        "/search",
        "/training",
        "/webm",
        "/perspectives",
        "/past-weekly",
        "/rss",
        "/login",
        "/contact",
    )

    if any(
        part in lowered
        for part in blocked
    ):
        return False

    if not url.startswith(BASE_URL):
        return False

    return True


def extract_excerpt(container, title):
    """
    Try to find a useful description from the article's surrounding
    DOM container without relying on fragile CSS class names.
    """

    if container is None:
        return ""

    title = clean_text(title)

    candidates = []

    for element in container.find_all(
        ["p", "div", "span"],
    ):
        text = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if not text:
            continue

        if text == title:
            continue

        if len(text) < 40:
            continue

        # Avoid obvious navigation / metadata fragments.
        if text in candidates:
            continue

        candidates.append(text)

    if not candidates:
        return ""

    # Prefer a reasonably sized paragraph.
    candidates.sort(
        key=lambda value: (
            abs(len(value) - 300),
            len(value),
        )
    )

    return candidates[0]


def collect(source):
    """
    Collect featured articles from AHRQ PSNet.

    Returns the common article structure expected by collector.py.
    """

    listing_url = clean_text(
        source.get(
            "listing_url",
            "",
        )
    ) or DEFAULT_LISTING_URL

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
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

    # --------------------------------------------------------
    # Determine the weekly issue date.
    # --------------------------------------------------------

    issue_date = ""

    for heading in soup.find_all(
        ["h1", "h2", "title"],
    ):
        text = clean_text(
            heading.get_text(
                " ",
                strip=True,
            )
        )

        parsed = parse_issue_date(
            text
        )

        if parsed:
            issue_date = parsed
            break

    if not issue_date:
        # Search the complete page text as a fallback.
        page_text = clean_text(
            soup.get_text(
                " ",
                strip=True,
            )
        )

        issue_date = parse_issue_date(
            page_text
        )

    # --------------------------------------------------------
    # Locate "This Week's Featured Articles".
    #
    # We intentionally stop before WebM&M content so that the
    # parser does not mix different PSNet content sections.
    # --------------------------------------------------------

    start_heading = None

    for heading in soup.find_all(
        ["h2", "h3"],
    ):
        text = clean_text(
            heading.get_text(
                " ",
                strip=True,
            )
        ).lower()

        if (
            "featured articles" in text
            and "week" in text
        ):
            start_heading = heading
            break

    if start_heading is None:
        raise RuntimeError(
            "Could not locate "
            "'This Week’s Featured Articles' "
            "section on AHRQ PSNet."
        )

    articles = []
    seen_urls = set()

    # Iterate through the DOM after the section heading.
    # Stop at the next major monthly section.
    current = start_heading

    while True:
        current = current.find_next()

        if current is None:
            break

        if current.name in (
            "h2",
            "h3",
        ):
            heading_text = clean_text(
                current.get_text(
                    " ",
                    strip=True,
                )
            ).lower()

            if (
                "this month's" in heading_text
                or "this month’s" in heading_text
            ):
                break

        if current.name != "a":
            continue

        title = clean_text(
            current.get_text(
                " ",
                strip=True,
            )
        )

        href = current.get(
            "href",
            "",
        )
        
        if href.startswith("#"):
            continue

        url = urljoin(
            BASE_URL,
            href,
        )

        if not is_valid_article_link(
            url,
            title,
        ):
            continue

        if url in seen_urls:
            continue

        # Use the closest reasonable DOM container for the excerpt.
        container = current.parent

        for _ in range(3):
            if (
                container is not None
                and container.name
                in (
                    "article",
                    "li",
                    "div",
                    "section",
                )
            ):
                break

            if container is not None:
                container = container.parent

        excerpt = extract_excerpt(
            container,
            title,
        )

        articles.append(
            {
                "source": clean_text(
                    source.get(
                        "name",
                        "AHRQ PSNet",
                    )
                ),
                "title": title,
                "url": url,
                "published_at": issue_date,
                "excerpt": excerpt,
            }
        )

        seen_urls.add(url)

    print(
        f"[AHRQ PSNet] "
        f"Issue date: "
        f"{issue_date or 'UNKNOWN'}"
    )

    print(
        f"[AHRQ PSNet] "
        f"{len(articles)} featured articles found."
    )

    return articles


if __name__ == "__main__":
    source = {
        "name": "AHRQ PSNet",
        "listing_url": DEFAULT_LISTING_URL,
    }

    results = collect(source)

    for index, article in enumerate(
        results,
        start=1,
    ):
        print("")
        print(
            f"{index}. "
            f"{article['title']}"
        )
        print(
            f"   {article['url']}"
        )
        print(
            f"   {article['published_at']}"
        )
        print(
            f"   {article['excerpt'][:250]}"
        )
