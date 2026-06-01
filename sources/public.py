"""
Source module for the Lebanese University CIJ legal database.
Site: http://77.42.251.205/

URL patterns discovered from DevTools Network inspection:
  - Search is GET, not POST: AdvancedRulingSearch.aspx?searchText=&AndOr=AND&...&rulYear=YYYY&pageNumber=N&language=ar
  - Detail page: ViewRulePage.aspx?ID={ID}
"""

import logging
import re
from urllib.parse import urlencode
from bs4 import BeautifulSoup

from core import (
    SourceConfig, fetch_page, Ruling, has_french_text,
    extract_article_citations, extract_articles_with_law,
)


BASE_URL = "http://77.42.251.205"
SEARCH_PAGE_URL = f"{BASE_URL}/AdvancedRulingSearch.aspx"
# Year range for the search filter. Goes back to 1930 because:
#   - مجالس العمل التحكيمية (labor arbitration) has rulings into the 1970s
#   - محكمة الجنايات has rulings into the 1980s and earlier
#   - Empty years are cheap (~2s each) and the scraper skips them automatically
# The 2 most recent years (2024, 2025) often return empty for older courts —
# that's fine, they're handled like any other empty year.
AVAILABLE_YEARS = list(range(1930, 2026))


def build_detail_url(ruling_id: str) -> str:
    return f"{BASE_URL}/ViewRulePage.aspx?ID={ruling_id}&selection="


def build_search_url(year: int, page: int = 1, court_id: int = 0) -> str:
    """
    Build the exact GET URL the browser uses (copied from Network tab payload).

    court_id values (from the المحكمة dropdown in the search form):
      0      = all courts (default)
      49973  = إستئناف مدني (civil appeal)
      113983 = ابتدائي مدني (civil first instance)  -- mostly empty
      49974  = استئناف جزائي (criminal appeal)
      114173 = المجلس الدستوري (constitutional council)
      102412 = المجلس العدلي (judicial council)
      101411 = تمييز جزائي - مطبوعات (press cassation)
      49975  = تمييز جزائي (criminal cassation)
      49976  = تمييز مدني (civil cassation)
      49977  = شورى (administrative)
      49978  = مجالس العمل التحكيمية (labor arbitration — TRIAL court)
      114064 = محكمة الاستئناف الناظرة بجرائم المطبوعات (press appeal)
      49979  = محكمة الجنايات (criminal felonies — TRIAL court)
    """
    params = [
        ("searchText", ""),
        ("AndOr", "AND"),
        ("typeid", "0"),
        ("courtID", str(court_id)),
        ("depid", "0"),
        ("rulNumber", "0"),
        ("rulYear", str(year)),
        ("judjes", ""),
        ("desicionmonth", "0"),
        ("DesicionDay", "0"),
        ("DesicionYear", "0"),
        ("pageNumber", str(page)),
        ("language", "ar"),
    ]
    return f"{SEARCH_PAGE_URL}?{urlencode(params)}"


def reset_session(session):
    """
    Clear cookies and fetch the homepage to establish a fresh session.
    This prevents the server from returning cached results from a previous year.
    """
    session.cookies.clear()
    try:
        session.get(BASE_URL + "/", timeout=15)
    except Exception:
        pass


def discover_ruling_urls(session, max_count: int, years=None, delay: float = 2.5,
                          court_id: int = 0):
    if years is None:
        years = sorted(AVAILABLE_YEARS, reverse=True)

    urls = []
    urls_set = set()

    if court_id:
        logging.info(f"FILTERING by court_id={court_id} (single court mode)")

    for year in years:
        if len(urls) >= max_count:
            break

        # Reset session between years so cached state from year N doesn't bleed into year N+1
        logging.info(f"Year {year}: resetting session...")
        reset_session(session)

        page = 1
        empty_pages_in_a_row = 0
        prev_first_url = None  # detect when server returns same content

        while len(urls) < max_count:
            search_url = build_search_url(year, page, court_id)
            html = fetch_page(session, search_url, delay)
            if html is None:
                break

            page_urls = extract_ruling_links(html)
            if not page_urls:
                logging.info(f"  Year {year}: no rulings (empty page {page}) — moving to next year")
                break

            # Detect if server is returning the same page regardless of pageNumber
            if page > 1 and page_urls and page_urls[0] == prev_first_url:
                logging.info(f"  Year {year}: page {page} returns same content as previous — done")
                break
            prev_first_url = page_urls[0]

            new_urls = [u for u in page_urls if u not in urls_set]
            if not new_urls:
                empty_pages_in_a_row += 1
                if empty_pages_in_a_row >= 2:
                    logging.info(f"  Year {year}: 2 consecutive pages with no new URLs — done")
                    break
            else:
                empty_pages_in_a_row = 0
                urls.extend(new_urls)
                urls_set.update(new_urls)
                logging.info(f"  Year {year} page {page}: +{len(new_urls)} new (total {len(urls)})")

            page += 1

    return urls[:max_count]


def extract_ruling_links(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    seen = set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"ViewRulePage\.aspx\?ID=(\d+)", a["href"], re.IGNORECASE)
        if m:
            ruling_id = m.group(1)
            if ruling_id not in seen:
                seen.add(ruling_id)
                urls.append(build_detail_url(ruling_id))
    return urls


def discover_article_urls(session, max_count: int):
    return []


def clean_judge_name(raw: str) -> str:
    """
    Normalize a judge name from the raw site text.

    Observed quirks in scraped data:
      - Names wrapped in slashes:           /سعيد/                 → سعيد
      - First+Last with double slash:       /سعيد//الحجار/         → سعيد الحجار
      - Embedded newlines inside the name:  /عبد\\nالملك//برباري/  → عبد الملك برباري
      - Stray punctuation/whitespace at edges

    Without this cleaning, the same human was counted as several "different"
    judges because each surface form produced a different string.
    """
    if not raw:
        return ""
    # Replace double-slash (the part separator) with a single space
    s = raw.replace("//", " ")
    # Strip remaining slashes
    s = s.replace("/", " ")
    # Collapse all whitespace (including newlines) into single spaces
    s = re.sub(r"\s+", " ", s)
    # Trim edge punctuation and whitespace
    s = s.strip(" ،.-,;\t")
    return s


def parse_judges(card: dict) -> list:
    """Build the deduplicated, cleaned judge list from card data."""
    judges = []
    seen = set()

    presider = clean_judge_name(card.get("presiding_judge") or "")
    if presider:
        judges.append(presider)
        seen.add(presider)

    panel = card.get("panel_members") or ""
    for j in re.split(r"[،\-,;]+", panel):
        clean = clean_judge_name(j)
        if clean and clean not in seen:
            judges.append(clean)
            seen.add(clean)

    return judges


def parse_ruling_html(html: str, url: str, source_name: str, timestamp: str):
    """Parse one ruling detail page into a Ruling object."""
    soup = BeautifulSoup(html, "html.parser")

    m = re.search(r"ID=(\d+)", url)
    ruling_id = m.group(1) if m else url.rstrip("/").split("/")[-1]

    card = extract_card(soup)
    judges = parse_judges(card)

    ruling_year = None
    if card.get("year_str"):
        try:
            ruling_year = int(card["year_str"])
        except ValueError:
            pass

    topics = extract_topics(soup)
    page_summary = extract_page_summary(soup)
    structured = extract_legislation(soup)
    full_text = soup.get_text("\n", strip=True)

    # Run the rich text-based extractor on both summary and full text
    # The summary is more reliable when present; full_text catches more.
    text_for_extraction = page_summary or full_text
    text_articles = extract_articles_with_law(text_for_extraction)

    # Merge: structured (from sidebar) takes priority for law info since it's
    # the most authoritative. Text-extracted articles fill in what the sidebar missed.
    structured_by_num = {c["number"]: c for c in structured}
    for ta in text_articles:
        if ta["number"] not in structured_by_num:
            # New article number not in the sidebar — add it
            structured_by_num[ta["number"]] = {
                "number": ta["number"],
                "law": ta["law"] or "",  # may be None
            }
        elif not structured_by_num[ta["number"]].get("law") and ta["law"]:
            # Sidebar had the number but no law — fill it in from text
            structured_by_num[ta["number"]]["law"] = ta["law"]

    merged_structured = list(structured_by_num.values())
    all_nums = sorted(structured_by_num.keys(), key=lambda x: int(x) if x.isdigit() else 0)

    return Ruling(
        ruling_id=ruling_id,
        source_url=url,
        source_name=source_name,
        scrape_timestamp=timestamp,
        court_name=card.get("court_type"),
        court_type=card.get("court_type"),
        judge_names=judges,
        ruling_date=card.get("ruling_date"),
        ruling_year=ruling_year,
        topics=topics,
        page_summary=page_summary,
        cited_articles=all_nums,
        cited_articles_full=merged_structured,
        full_text=full_text,
        has_french=has_french_text(full_text),
        is_complete=bool(page_summary and all_nums),
    )


def extract_card(soup) -> dict:
    result = {}
    label_map = {
        "المحكمة": "court_type",
        "الرقم": "rule_number",
        "السنة": "year_str",
        "تاريخ الجلسة": "ruling_date",
        "الرئيس": "presiding_judge",
        "الأعضاء": "panel_members",
    }
    for label, field_name in label_map.items():
        for el in soup.find_all(string=re.compile(rf"^\s*{re.escape(label)}\s*:?\s*$")):
            parent = el.parent
            if not parent:
                continue
            value = None
            sib = parent.find_next_sibling()
            if sib:
                value = sib.get_text(strip=True)
            if not value and parent.parent:
                cells = parent.parent.find_all(["td", "div", "span"])
                for i, cell in enumerate(cells):
                    if label in cell.get_text():
                        if i + 1 < len(cells):
                            value = cells[i + 1].get_text(strip=True)
                            break
            if value and value != label:
                result[field_name] = value
                break
    return result


def extract_topics(soup) -> list:
    topics = set()
    for tag in soup.find_all(["p", "div", "span", "h2", "h3", "h4"]):
        text = tag.get_text(" ", strip=True)
        if 20 < len(text) < 1000 and text.count(" / ") >= 2:
            arabic = len(re.findall(r"[\u0600-\u06FF]", text))
            if arabic > 20:
                for t in text.split(" / "):
                    t = t.strip()
                    if 2 < len(t) < 80 and len(re.findall(r"[\u0600-\u06FF]", t)) > 1:
                        topics.add(t)
    return sorted(topics)


SITE_NOISE = [
    # Various phrasings of the footer "about us" boilerplate
    "مركز الابحاث والدراسات",
    "مركز الدراسات والأبحاث",      # different word order — same boilerplate
    "مركز الدراسات والابحاث",      # without hamza
    "انشىء مركز",                   # starts the footer paragraph
    "كوحدة جامعية مستقلة",          # later in the footer
    "إتصل بنا",
    "حول الموقع",
    "Made by IDS",
    "الجامعة اللبنانية",
    "الصفحة الرئيسية",
    "الصقحة الرئيسية",              # typo present on the site
    "رؤساء المركز",
    "روابط مفيدة",
    "حول المركز",
]


# Anchor markers — the real summary always starts with one of these verbs
# describing a court's action. If we find one, we strongly prefer that block.
RULING_VERB_PATTERNS = [
    "ردت محكمة",                    # "the court rejected..."
    "ابطلت محكمة",                  # "the court annulled..."
    "ادانت محكمة",                  # "the court convicted..."
    "قضت محكمة",                    # "the court ruled..."
    "حكمت محكمة",                   # "the court adjudicated..."
    "اعتبرت محكمة",                 # "the court considered..."
    "ان القضاء",                    # "the judiciary..."
    "ان المحكمة",                   # "the court..."
    "ان محكمة",
    "اوجبت محكمة",
    "نقضت محكمة",
    "ابرمت محكمة",
]


def extract_page_summary(soup) -> str:
    """
    Extract the on-page ruling summary — the legal-analytical paragraph.

    Strategy:
    1. Find candidates: Arabic text blocks of reasonable length.
    2. Filter out any block containing footer boilerplate phrases.
    3. STRONGLY prefer blocks that start with a court-action verb
       (ردت/ابطلت/قضت محكمة...). These are unambiguously ruling summaries.
    4. Fall back to the largest non-noise block.
    """
    candidates = []
    for tag in soup.find_all(["div", "p", "article", "td"]):
        text = tag.get_text(" ", strip=True)
        arabic = len(re.findall(r"[\u0600-\u06FF]", text))
        if arabic < 50 or len(text) > 15000:
            continue
        if any(noise in text for noise in SITE_NOISE):
            continue
        # Score: real ruling summaries start with a court-action verb
        has_verb = any(verb in text[:80] for verb in RULING_VERB_PATTERNS)
        candidates.append((has_verb, arabic, text))

    # Sort: verb-prefixed first, then by Arabic char count
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2] if candidates else ""


def extract_legislation(soup) -> list:
    citations = []
    seen = set()
    header = soup.find(string=re.compile("تشريعات مرتبطة"))
    if not header:
        return citations
    container = header.find_parent()
    if not container:
        return citations
    for el in container.find_all_next(["a", "li", "p", "div", "span"]):
        text = el.get_text(" ", strip=True)
        m = re.match(r"^\s*•?\s*مادة\s+(?:رقم\s+)?(\d{1,4})\s+(.+?)\s*$", text)
        if m:
            number, law = m.group(1), m.group(2).strip()
            key = (number, law)
            if key not in seen and 1 <= int(number) <= 9999:
                seen.add(key)
                citations.append({"number": number, "law": law})
        if citations and text and "مادة" not in text and len(text) > 200:
            break
    return citations


def parse_article_html(html, url, source_name, timestamp):
    raise NotImplementedError("Articles extracted from rulings directly")


CONFIG = SourceConfig(
    name="public",
    base_url=BASE_URL,
    requires_login=False,
    request_delay=2.5,
    user_agent="PU-Legal-Research-Bot (contact: cij@ul.edu.lb)",
    discover_ruling_urls=discover_ruling_urls,
    discover_article_urls=discover_article_urls,
    parse_ruling_html=parse_ruling_html,
    parse_article_html=parse_article_html,
)