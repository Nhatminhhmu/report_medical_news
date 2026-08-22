import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


REQUEST_TIMEOUT = 30

USER_AGENT = (
    "ReportMedicalNews/0.1 "
    "(Healthcare Operations Intelligence)"
)


def clean_text(value):
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def normalize_url(
    url,
    base_url,
):
    if not url:
        return ""

    return urljoin(
        base_url,
        url,
    )


def extract_date(text):
    """
    Try to find common HIMSS date formats.
    """

    text = clean_text(text)

    patterns = [
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+\d{1,2},\s+\d{4}\b",

        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:
            return match.group(0)

    return ""


def collect(source):

    name = clean_text(
        source.get(
            "name",
            "HIMSS",
        )
    )

    listing_url = clean_text(
        source.get(
            "listing_url",
            "https://www.himss.org/news-center",
        )
    )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,"
            "image/webp,"
            "*/*;q=0.8"
        ),
    }

    print(
        f"[HIMSS] Fetching: "
        f"{listing_url}"
    )

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

    # --------------------------------------------------------
    # Strategy 1
    #
    # Look for article/news cards.
    # We inspect containers rather than blindly taking
    # every <a> on the page.
    # --------------------------------------------------------

    candidate_containers = soup.select(
        """
        article,
        [class*="article"],
        [class*="news-card"],
        [class*="news_item"],
        [class*="news-item"],
        [class*="content-card"],
        [class*="card"]
        """
    )

    for container in candidate_containers:

        link = container.find(
            "a",
            href=True,
        )

        if not link:
            continue

        title_element = (
            container.find(
                [
                    "h1",
                    "h2",
                    "h3",
                    "h4",
                    "h5",
                ]
            )
        )

        if title_element:

            title = clean_text(
                title_element.get_text(
                    " ",
                    strip=True,
                )
            )

        else:

            title = clean_text(
                link.get_text(
                    " ",
                    strip=True,
                )
            )

        if not title:
            continue

        url = normalize_url(
            link.get("href"),
            listing_url,
        )

        if not url:
            continue

        if url in seen_urls:
            continue

        # HIMSS news article URLs generally contain
        # a meaningful path rather than navigation paths.
        lowered = url.lower()

        ignored = [
            "/search",
            "/login",
            "/membership",
            "/events",
            "/contact",
            "/about",
        ]

        if any(
            item in lowered
            for item in ignored
        ):
            continue

        if len(title) < 20:
            continue

        if len(title) > 300:
            continue

        # ----------------------------------------------------
        # Excerpt
        # ----------------------------------------------------

        excerpt = ""

        paragraphs = container.find_all(
            "p"
        )

        if paragraphs:

            excerpt = clean_text(
                paragraphs[0].get_text(
                    " ",
                    strip=True,
                )
            )

        # ----------------------------------------------------
        # Date
        # ----------------------------------------------------

        container_text = clean_text(
            container.get_text(
                " ",
                strip=True,
            )
        )

        published_at = extract_date(
            container_text
        )

        seen_urls.add(url)

        articles.append(
            {
                "source": name,
                "title": title,
                "url": url,
                "published_at": published_at,
                "excerpt": excerpt,
            }
        )

    # --------------------------------------------------------
    # Strategy 2
    #
    # Fallback: inspect links pointing to likely news pages.
    # --------------------------------------------------------

    if not articles:

        print(
            "[HIMSS] Card parser found no "
            "articles. Running fallback."
        )

        for link in soup.find_all(
            "a",
            href=True,
        ):

            title = clean_text(
                link.get_text(
                    " ",
                    strip=True,
                )
            )

            url = normalize_url(
                link.get("href"),
                listing_url,
            )

            if not title or not url:
                continue

            if url in seen_urls:
                continue

            lowered = url.lower()

            if (
                "/news/" not in lowered
                and "/news-center/" not in lowered
            ):
                continue

            if len(title) < 20:
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
        f"[HIMSS] Found "
        f"{len(articles)} candidate articles."
    )

    return articles
