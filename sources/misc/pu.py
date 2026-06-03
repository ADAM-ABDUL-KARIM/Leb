"""
Source module for the PU library's Lebanese legal database.

This source REQUIRES LOGIN. Credentials are loaded from environment variables:
    export PU_USERNAME="your_pu_username"
    export PU_PASSWORD="your_pu_password"

Never commit credentials to git. Add a .env to .gitignore.

INSTRUCTIONS:
1. Make sure has confirmed in writing that scraping is authorized.
2. Run `python diagnose.py --url <login_page_url>` to inspect the login form.
3. Fill in the TODOs based on what the diagnostic shows.
4. Test with 5 rulings before scaling.
"""

import os
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from core import SourceConfig, build_ruling_from_full_text


# TODO #1: replace with the actual PU database URL  confirms
BASE_URL = "https://REPLACE_ME_WITH_PU_DATABASE.pu.edu.lb"
LOGIN_URL = f"{BASE_URL}/login"  # TODO: confirm exact path


# ============================================================
# LOGIN
# ============================================================

def build_login_payload(credentials: dict) -> dict:
    """
    Build the POST body for the login form.

    TODO #2: inspect the login form on the actual site. The field names below
    are placeholders — they're often "username"/"password" but could be
    "user"/"pass", "email"/"pwd", "login"/"passwd", etc.

    To find them: open the login page in a browser, right-click → Inspect,
    look at the <form> element and its <input name="..."> fields.
    """
    return {
        "username": credentials["username"],   # TODO: replace field name
        "password": credentials["password"],   # TODO: replace field name
        # Some sites also need a CSRF token, "remember me" checkbox, etc.
        # "csrf_token": ...,
    }


def check_login_success(response) -> bool:
    """
    Return True if the login response indicates success.

    TODO #3: customize based on what the site does after successful login.
    Common patterns:
    - Redirects to a dashboard (check response.url or status code 302)
    - Returns HTML containing "logout" link or user's name
    - Sets a session cookie that grants access to protected pages
    - Returns JSON with {"success": true}
    """
    # Placeholder check — replace with site-specific logic
    return (
        response.status_code in (200, 302)
        and "logout" in response.text.lower()
    )


# ============================================================
# URL DISCOVERY
# ============================================================

def discover_ruling_urls(session, max_count: int) -> list[str]:
    """
    Return URLs of ruling pages from the PU database.

    TODO #4: implement based on the database's structure. See sources/public.py
    for the three common patterns (pagination / sitemap / search API).

    For PU specifically, ask the librarian if there's a documented API or
    bulk export option before scraping page-by-page — that's faster, kinder
    to the server, and often the librarian's preferred method.
    """
    raise NotImplementedError("Fill in discover_ruling_urls for PU database")


def discover_article_urls(session, max_count: int) -> list[str]:
    """TODO #5: only if articles are scraped from PU."""
    raise NotImplementedError("Fill in discover_article_urls if needed")


# ============================================================
# PARSING
# ============================================================

def parse_ruling_html(html: str, url: str, source_name: str, timestamp: str):
    """
    Parse a PU database ruling page.

    TODO #6: replace selectors based on actual PU database HTML structure.
    The PU database likely has a more structured layout than public sites,
    possibly with explicit fields for court, judge, parties, etc.
    """
    soup = BeautifulSoup(html, "html.parser")

    # TODO: replace these with PU-specific selectors
    court_el = soup.select_one(".court, .field-court")
    judge_el = soup.select_one(".judges, .field-judges")
    date_el = soup.select_one(".date, .field-date")
    body_el = soup.select_one(".ruling-text, .field-body, .full-text")
    id_el = soup.select_one(".ruling-number, .field-id")
    law_area_el = soup.select_one(".law-area, .field-category")

    metadata = {
        "ruling_id": (id_el.get_text(strip=True) if id_el else None)
                     or url.rstrip("/").split("/")[-1],
        "court_name": court_el.get_text(strip=True) if court_el else None,
        "ruling_date": date_el.get_text(strip=True) if date_el else None,
        "law_area": law_area_el.get_text(strip=True) if law_area_el else None,
    }

    if metadata["ruling_date"]:
        year_match = re.search(r"(19|20)\d{2}", metadata["ruling_date"])
        if year_match:
            metadata["ruling_year"] = int(year_match.group())

    if judge_el:
        judge_text = judge_el.get_text(strip=True)
        metadata["judge_names"] = [
            j.strip() for j in re.split(r"[،,\n;]", judge_text) if j.strip()
        ]

    full_text = (body_el.get_text("\n", strip=True) if body_el
                 else soup.get_text("\n", strip=True))

    return build_ruling_from_full_text(
        full_text=full_text,
        metadata=metadata,
        source_url=url,
        source_name=source_name,
        timestamp=timestamp,
    )


def parse_article_html(html: str, url: str, source_name: str, timestamp: str):
    """TODO #7: implement if articles are scraped from PU."""
    raise NotImplementedError("Fill in parse_article_html if needed")


# ============================================================
# Credential loading helper
# ============================================================

def get_credentials() -> dict:
    """Load PU credentials from environment variables."""
    username = os.environ.get("PU_USERNAME")
    password = os.environ.get("PU_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "Set PU_USERNAME and PU_PASSWORD environment variables.\n"
            "  export PU_USERNAME='your_username'\n"
            "  export PU_PASSWORD='your_password'"
        )
    return {"username": username, "password": password}


# ============================================================
# Export the source configuration
# ============================================================

CONFIG = SourceConfig(
    name="pu",
    base_url=BASE_URL,
    requires_login=True,
    login_url=LOGIN_URL,
    login_payload_builder=build_login_payload,
    login_success_check=check_login_success,
    request_delay=3.0,  # be extra polite to PU's library server
    discover_ruling_urls=discover_ruling_urls,
    discover_article_urls=discover_article_urls,
    parse_ruling_html=parse_ruling_html,
    parse_article_html=parse_article_html,
)
