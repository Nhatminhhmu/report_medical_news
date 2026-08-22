"""
Báo Điện tử Chính phủ — Y tế parser.

Target:
    https://baochinhphu.vn/y-te.html

The generic WEB collector can discover article links, but this site exposes
publication metadata in page markup that is better handled by a source-specific
parser.

Return format is compatible with collector.normalize_articles():
    {
        "source": str,
        "title": str,
        "url": str,
        "published_at": str,
        "excerpt": str,
    }
"""

import json
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


SOURCE_NAME = "Báo Điện tử Chính phủ — Y tế"
DEFAULT_LISTING_URL = "https://baochinhphu.vn/y-te.html"

REQUEST_TIMEOUT = 30

USER_AGENT = (
    "ReportMedicalNews/0.3 "
    "(Healthcare Operations Intelligence)"
)


def clean_text(value):
    if value is None:
        return ""

    return " ".join(
        str(value).replace("\xa0", " ").split()
    ).strip()


def normalize_url(href, base_url):
    if not href:
        return ""

    return urljoin(
        base_url,
        clean_text(href),
    )


def parse_vietnamese_date(value):
    """
    Convert common Báo Điện tử Chính phủ date strings to ISO 8601 UTC.

    Examples:
        10/08/2026 19:25
        03/08/2026 08:37
        18/08/2026 17:26
    """

    value = clean_text(value)

    if not value:
        return ""

    patterns = [
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ]

    for pattern in patterns:
        try:
            parsed = datetime.strptime(
                value,
                pattern,
            )

            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

            return parsed.isoformat()

        except ValueError:
            continue

    # Already ISO 8601.
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
        ).isoformat()

    except ValueError:
        return ""


def is_article_url(url):
    """
    Keep article URLs from the Y tế listing and reject navigation links.
    """

    lowered = url.lower()

    if not lowered.startswith(
        "https://baochinhphu.vn/"
    ):
        return False

    ignored = (
        "/search",
        "/video/",
        "/multimedia/",
        "/rss",
        "/tag/",
        "/author/",
        "/contact",
        "/about",
        "/privacy",
        "/terms",
        "/y-te.html",
        "#",
    )

    if any(
        token in lowered
        for token in ignored
    ):
        return False

    return True


def extract_datetime_from_element(element):
    """
    Try several common HTML representations:
    - <time datetime="...">
    - data-* date attributes
    - visible text containing dd/mm/YYYY HH:MM
    """

    if element is None:
        return ""

    # <time datetime="...">
    time_element = element.find(
        "time"
    )

    if time_element:
        for attribute in (
            "datetime",
            "content",
            "data-date",
            "data-time",
        ):
            value = time_element.get(
                attribute,
                "",
            )

            parsed = parse_vietnamese_date(
                value
            )

            if parsed:
                return parsed

            if value:
                # Handle ISO datetime directly.
                try:
                    iso_value = value

                    if iso_value.endswith("Z"):
                        iso_value = (
                            iso_value[:-1]
                            + "+00:00"
                        )

                    dt = datetime.fromisoformat(
                        iso_value
                    )

                    if dt.tzinfo is None:
                        dt = dt.replace(
                            tzinfo=timezone.utc
                        )

                    return dt.astimezone(
                        timezone.utc
                    ).isoformat()

                except ValueError:
                    pass

        visible_time = clean_text(
            time_element.get_text(
                " ",
                strip=True,
            )
        )

        parsed = parse_vietnamese_date(
            visible_time
        )

        if parsed:
            return parsed

    # Common data attributes.
    attributes = (
        "data-date",
        "data-time",
        "data-publish-date",
        "data-published",
        "data-published-at",
        "data-create-date",
        "datetime",
    )

    for attribute in attributes:
        value = element.get(
            attribute,
            "",
        )

        parsed = parse_vietnamese_date(
            value
        )

        if parsed:
            return parsed

    # Look for dd/mm/yyyy hh:mm in visible text.
    text = clean_text(
        element.get_text(
            " ",
            strip=True,
        )
    )

    match = re.search(
        r"\b(\d{1,2}/\d{1,2}/\d{4}"
        r"(?:\s+\d{1,2}:\d{2}"
        r"(?::\d{2})?)?)\b",
        text,
    )

    if match:
        parsed = parse_vietnamese_date(
            match.group(1)
        )

        if parsed:
            return parsed

    return ""


def extract_from_json_ld(
    soup,
    url,
):
    """
    Search JSON-LD for Article/NewsArticle metadata.

    Returns:
        (title, published_at, excerpt)
    """

    for script in soup.find_all(
        "script",
        type="application/ld+json",
    ):
        raw = script.string or script.get_text(
            strip=True
        )

        if not raw:
            continue

        try:
            data = json.loads(raw)

        except (
            json.JSONDecodeError,
            TypeError,
        ):
            continue

        candidates = []

        if isinstance(data, dict):
            candidates.append(data)

            graph = data.get(
                "@graph"
            )

            if isinstance(graph, list):
                candidates.extend(
                    item
                    for item in graph
                    if isinstance(item, dict)
                )

        elif isinstance(data, list):
            candidates.extend(
                item
                for item in data
                if isinstance(item, dict)
            )

        for item in candidates:
            item_type = item.get(
                "@type",
                "",
            )

            if isinstance(
                item_type,
                list,
            ):
                item_types = {
                    str(value).lower()
                    for value in item_type
                }

            else:
                item_types = {
                    str(
                        item_type
                    ).lower()
                }

            article_types = {
                "article",
                "newsarticle",
                "report",
                "medicalwebpage",
            }

            if not (
                item_types
                & article_types
            ):
                continue

            item_url = clean_text(
                item.get(
                    "url",
                    "",
                )
            )

            if (
                item_url
                and item_url != url
            ):
                continue

            title = clean_text(
                item.get(
                    "headline",
                    "",
                )
                or item.get(
                    "name",
                    "",
                )
            )

            published = clean_text(
                item.get(
                    "datePublished",
                    "",
                )
                or item.get(
                    "dateCreated",
                    "",
                )
            )

            description = clean_text(
                item.get(
                    "description",
                    "",
                )
            )

            parsed_date = (
                parse_vietnamese_date(
                    published
                )
            )

            return (
                title,
                parsed_date,
                description,
            )

    return "", "", ""


def find_article_container(
    anchor,
):
    """
    Find a reasonably local article/card container around an anchor.
    """

    for tag_name in (
        "article",
        "div",
        "li",
    ):
        container = anchor.find_parent(
            tag_name
        )

        if container is None:
            continue

        text = clean_text(
            container.get_text(
                " ",
                strip=True,
            )
        )

        # Avoid grabbing huge page-level containers.
        if len(text) <= 2500:
            return container

    return anchor.parent


def extract_title(anchor):
    title = clean_text(
        anchor.get_text(
            " ",
            strip=True,
        )
    )

    if len(title) >= 20:
        return title

    for attribute in (
        "title",
        "aria-label",
    ):
        value = clean_text(
            anchor.get(
                attribute,
                "",
            )
        )

        if len(value) >= 20:
            return value

    return title


def extract_excerpt(
    container,
    title,
):
    if container is None:
        return ""

    # Prefer semantic description fields.
    for selector in (
        "[class*='sapo']",
        "[class*='summary']",
        "[class*='excerpt']",
        "[class*='description']",
        "[class*='desc']",
    ):
        element = container.select_one(
            selector
        )

        if element:
            text = clean_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if (
                len(text) >= 30
                and text != title
            ):
                return text[:1000]

    return ""


def collect(source):
    listing_url = clean_text(
        source.get(
            "listing_url",
            "",
        )
    ) or DEFAULT_LISTING_URL

    print(
        f"[WEB] Fetching {SOURCE_NAME}: "
        f"{listing_url}"
    )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": (
            "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
        ),
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

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        url = normalize_url(
            anchor.get("href"),
            listing_url,
        )

        if not is_article_url(
            url
        ):
            continue

        if url in seen_urls:
            continue

        title = extract_title(
            anchor
        )

        if len(title) < 20:
            continue

        container = find_article_container(
            anchor
        )

        # First attempt: metadata attached to
        # the listing card.
        published_at = (
            extract_datetime_from_element(
                container
            )
        )

        excerpt = extract_excerpt(
            container,
            title,
        )

        # Second attempt: article metadata.
        # This is intentionally done only for
        # candidates whose listing card did not
        # expose a date.
        if not published_at:
            try:
                article_response = requests.get(
                    url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )

                if article_response.ok:
                    article_soup = (
                        BeautifulSoup(
                            article_response.text,
                            "html.parser",
                        )
                    )

                    (
                        json_title,
                        json_date,
                        json_excerpt,
                    ) = extract_from_json_ld(
                        article_soup,
                        url,
                    )

                    if json_title:
                        title = json_title

                    if json_date:
                        published_at = json_date

                    if (
                        json_excerpt
                        and not excerpt
                    ):
                        excerpt = (
                            json_excerpt[:1000]
                        )

                    if not published_at:
                        published_at = (
                            extract_datetime_from_element(
                                article_soup
                            )
                        )

            except requests.RequestException as error:
                print(
                    "[WEB] Warning: could not "
                    f"fetch article page {url}: "
                    f"{error}"
                )

        # Do not emit articles without a date.
        # The collector's lookback filter depends
        # on published_at and would otherwise discard
        # them with a misleading generic message.
        if not published_at:
            print(
                "[DATE] Could not determine "
                f"published_at: {title}"
            )
            continue

        seen_urls.add(url)

        articles.append(
            {
                "source": SOURCE_NAME,
                "title": title,
                "url": url,
                "published_at": published_at,
                "excerpt": excerpt,
            }
        )

    print(
        f"[WEB] {SOURCE_NAME}: "
        f"{len(articles)} articles with dates found."
    )

    return articles


if __name__ == "__main__":
    articles = collect(
        {
            "listing_url": DEFAULT_LISTING_URL
        }
    )

    print("")
    print(
        f"[{SOURCE_NAME}] "
        f"{len(articles)} articles found."
    )

    for index, article in enumerate(
        articles,
        start=1,
    ):
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
