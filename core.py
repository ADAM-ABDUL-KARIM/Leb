"""
Core scraper engine — source-agnostic logic.
Handles fetching, Arabic section extraction, saving, and resume support.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_REQUEST_DELAY = 2.5
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30
DEFAULT_USER_AGENT = "PU-Legal-Research-Bot (contact: cij@ul.edu.lb)"


@dataclass
class Ruling:
    """One court ruling — the core data unit for the pipeline."""
    ruling_id: str
    source_url: str
    source_name: str
    scrape_timestamp: str

    # Metadata
    court_name: Optional[str] = None
    court_type: Optional[str] = None
    judge_names: list = field(default_factory=list)
    ruling_date: Optional[str] = None
    ruling_year: Optional[int] = None

    # Topic tags (slash-separated keywords below the title)
    topics: list = field(default_factory=list)

    # The summary paragraph (yellow-highlighted text on the page)
    page_summary: Optional[str] = None

    # Cited articles: numbers only + full (with law names)
    cited_articles: list = field(default_factory=list)
    cited_articles_full: list = field(default_factory=list)

    # Full raw page text (fallback)
    full_text: Optional[str] = None

    # Quality flags
    has_french: bool = False
    is_complete: bool = False  # True when summary + at least one article


@dataclass
class SourceConfig:
    name: str
    base_url: str
    requires_login: bool = False
    login_url: Optional[str] = None
    login_payload_builder: Optional[Callable] = None
    login_success_check: Optional[Callable] = None
    request_delay: float = DEFAULT_REQUEST_DELAY
    user_agent: str = DEFAULT_USER_AGENT
    discover_ruling_urls: Callable = None
    discover_article_urls: Callable = None
    parse_ruling_html: Callable = None
    parse_article_html: Callable = None


def create_session(config: SourceConfig) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": config.user_agent,
        "Accept-Language": "ar,en;q=0.5",
    })
    retry_strategy = Retry(
        total=DEFAULT_MAX_RETRIES,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def login_if_needed(session, config, credentials=None) -> bool:
    if not config.requires_login:
        return True
    if not credentials or not config.login_url or not config.login_payload_builder:
        logging.error("Login required but config incomplete")
        return False
    payload = config.login_payload_builder(credentials)
    response = session.post(config.login_url, data=payload, timeout=DEFAULT_TIMEOUT)
    if config.login_success_check(response):
        logging.info("Login successful")
        return True
    logging.error(f"Login failed (status={response.status_code})")
    return False


def fetch_page(session, url: str, delay: float) -> Optional[str]:
    try:
        time.sleep(delay)
        response = session.get(url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        return response.text
    except requests.exceptions.RequestException as e:
        logging.warning(f"Failed to fetch {url}: {e}")
        return None


def has_french_text(text: str, threshold: int = 5) -> bool:
    if not text:
        return False
    return len(re.findall(r"[A-Za-zÀ-ÿ]{3,}", text)) > threshold


# ---------------------------------------------------------------------------
# Article citation extraction
# ---------------------------------------------------------------------------
# This module finds article references in Arabic legal text and identifies
# which law each article belongs to. Patterns observed in real CIJ data:
#   - المادة /50/ عمل           (slash-delimited number with law)
#   - المادة 50 فقرة (ج) عمل   (with paragraph reference)
#   - المادتين 257 و265 موجبات وعقود  (dual reference)
#   - المادتين /369/ و/370/ اصول مدنية
#   - 459/460 و459/454/460 عقوبات  (slash-chains -- multiple in one citation)
#   - المادة (1) من نظام...        (parenthesized number)
#   - المادة التاسعة ضمان           (word-based ordinal)
#   - بالمادة 48 من قانون العمل  (preposition prefix)

# Arabic ordinal words → digit equivalents (for "المادة التاسعة" style)
_ORDINAL_MAP = {
    "الاولى": "1", "الأولى": "1",
    "الثانية": "2",
    "الثالثة": "3",
    "الرابعة": "4",
    "الخامسة": "5",
    "السادسة": "6",
    "السابعة": "7",
    "الثامنة": "8",
    "التاسعة": "9",
    "العاشرة": "10",
    "الحادية": "11",
    "الثانية عشرة": "12",
}

# Law-name vocabulary: short keyword found in summary → canonical full name.
# Order matters — most-specific keyword first so "موجبات وعقود" wins over "موجبات".
_LAW_KEYWORDS = [
    # Long, specific phrases first
    ("قانون المخدرات والمؤثرات العقلية والسلائف", "قانون المخدرات والمؤثرات العقلية والسلائف"),
    ("قانون حماية المستهلك", "قانون حماية المستهلك"),
    ("قانون عقود العمل الجماعية والوساطة والتحكيم", "قانون عقود العمل الجماعية"),
    ("عقد العمل الجماعي", "قانون عقود العمل الجماعية"),
    ("اصول مدنية", "قانون أصول المحاكمات المدنية"),
    ("أصول مدنية", "قانون أصول المحاكمات المدنية"),
    ("أ.م.م", "قانون أصول المحاكمات المدنية"),
    ("ا.م.م", "قانون أصول المحاكمات المدنية"),
    ("موجبات وعقود", "قانون الموجبات والعقود"),
    ("الموجبات والعقود", "قانون الموجبات والعقود"),
    ("موجبات", "قانون الموجبات والعقود"),
    ("ضمان اجتماعي", "قانون الضمان الاجتماعي"),
    ("قانون الضمان", "قانون الضمان الاجتماعي"),
    ("ضمان", "قانون الضمان الاجتماعي"),
    ("قانون العقوبات", "قانون العقوبات"),
    ("ق.ع", "قانون العقوبات"),
    ("عقوبات", "قانون العقوبات"),
    ("قانون العمل", "قانون العمل"),
    # Bare-word fallbacks — these are short common nouns, kept last
    # so they don't pre-empt more specific matches like "قانون العمل"
    ("عمل", "قانون العمل"),
    ("مخدرات", "قانون المخدرات والمؤثرات العقلية والسلائف"),
    ("تجارة", "قانون التجارة"),
    ("قانون الانتخاب", "قانون الانتخاب"),
    ("البلديات", "قانون البلديات"),
    ("الجمارك", "قانون الجمارك"),
    ("الموظفين", "نظام الموظفين"),
]


def _find_law_after(text: str) -> str:
    """Identify which law follows an article reference (looks in next ~50 chars)."""
    if not text:
        return None
    snippet = text[:60]
    for keyword, canonical in _LAW_KEYWORDS:
        if keyword in snippet:
            return canonical
    return None


def extract_article_citations(text: str) -> list:
    """
    Backwards-compatible: return JUST article numbers (deduplicated, sorted).
    Used by callers that don't need law names.
    """
    full = extract_articles_with_law(text)
    nums = sorted({a["number"] for a in full}, key=lambda x: int(x) if x.isdigit() else 0)
    return nums


def extract_articles_with_law(text: str) -> list:
    """
    Find article references and identify their law.
    Returns list of {"number": str, "law": str | None} dicts, deduplicated.

    Handles all observed citation patterns. Verified on real CIJ ruling data.
    """
    if not text:
        return []

    results = []

    # ---- Pattern 1: المادتين/المادتان X و Y [law]  (dual reference) ----
    for m in re.finditer(
        r"(?:ال)?ماد(?:ت[يا]ن)\s*/?\s*(\d+)\s*/?\s*و\s*/?\s*(\d+)\s*/?",
        text,
    ):
        law = _find_law_after(text[m.end():])
        for num in (m.group(1), m.group(2)):
            results.append({"number": num, "law": law})

    # ---- Pattern 2: slash-chains (459/460 or 459/454/460) following article keyword ----
    # Multiple articles cited together with slashes
    for m in re.finditer(
        r"(?:ال)?ماد[ةتينان]+\s*(?:ل?ل?)\s*((?:\d+/){1,4}\d+)",
        text,
    ):
        chain = m.group(1)
        nums = re.findall(r"\d+", chain)
        law = _find_law_after(text[m.end():])
        for n in nums:
            results.append({"number": n, "law": law})

    # ---- Pattern 3: single article with digits ----
    # Catches: المادة /50/, المادة 50, المادة (1), بالمادة 48, etc.
    for m in re.finditer(
        r"(?:ال)?ماد[ةتينان]+\s*[/\(]?\s*(\d+)\s*[/\)]?",
        text,
    ):
        num = m.group(1)
        law = _find_law_after(text[m.end():])
        results.append({"number": num, "law": law})

    # ---- Pattern 4: word-based ordinals (المادة التاسعة, المادة الاولى) ----
    ordinal_pattern = "|".join(re.escape(w) for w in _ORDINAL_MAP.keys())
    for m in re.finditer(
        r"(?:ال)?ماد[ةتينان]+\s+(" + ordinal_pattern + r")",
        text,
    ):
        num = _ORDINAL_MAP[m.group(1)]
        law = _find_law_after(text[m.end():])
        results.append({"number": num, "law": law})

    # ---- Pattern 5: "م. 50" shorthand ----
    for m in re.finditer(r"م\.\s*(\d{1,4})", text):
        results.append({"number": m.group(1), "law": None})

    # Deduplicate: same (number, law) pair only kept once.
    # If the same number appears with a law and without, prefer the version with law.
    by_number = {}  # number -> best entry
    for r in results:
        existing = by_number.get(r["number"])
        if existing is None or (existing["law"] is None and r["law"] is not None):
            by_number[r["number"]] = r

    return list(by_number.values())


def save_ruling(ruling: Ruling, output_dir: Path, raw_html: str) -> None:
    source_dir = output_dir / ruling.source_name
    raw_dir = source_dir / "raw"
    json_dir = source_dir / "structured"
    raw_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{ruling.ruling_id}.html").write_text(raw_html, encoding="utf-8")
    (json_dir / f"{ruling.ruling_id}.json").write_text(
        json.dumps(asdict(ruling), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_checkpoint(output_dir: Path, source_name: str) -> set:
    json_dir = output_dir / source_name / "structured"
    if not json_dir.exists():
        return set()
    return {p.stem for p in json_dir.glob("*.json")}