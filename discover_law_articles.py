"""
Discover and scrape Lebanese legal article texts from the CIJ legislation site.

STRUCTURE (all GET-based, three levels):
  Level 1  Law.aspx?lawId=X
           The فهرس tree. The FULL tree is in the page HTML (the +/- is just
           CSS show/hide), so one fetch yields every LawTreeSectionID.
  Level 2  LawArticles.aspx?LawTreeSectionID=N&LawID=X
           Lists the articles of one subsection WITH their full text inline:
             • [المادة 1](...LawArticleID=984068...)
               <Arabic text>
               Art.1: <French text>   <- we DROP the French
  (Level 3 single-article pages exist but are unnecessary — Level 2 has text.)

OUTPUT: one JSON per article in data/articles/structured/, keyed by
        {law}_{article_number}, plus a combined articles_index.json.

USAGE:
    # Scrape every article of the configured laws
    python discover_law_articles.py

    # Just one law (for testing)
    python discover_law_articles.py --only "قانون العقوبات"

    # Limit section fetches (smoke test)
    python discover_law_articles.py --only "قانون العقوبات" --max-sections 3
"""

import argparse
import json
import logging
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "http://77.42.251.205"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

# Canonical law name -> lawId on the site.
# These cover ~98.5% of all article citations in the trial-court corpus.
LAW_IDS = {
    "قانون العقوبات": 244611,
    "قانون المخدرات والمؤثرات العقلية والسلائف": 243942,
    "قانون العمل": 190374,
    "قانون الموجبات والعقود": 244226,
    "قانون الضمان الاجتماعي": 244971,
    "قانون اصول المحاكمات المدنية": 244565,
    "الاسلحة والذخائر": 180890,
    "قانون التجارة": 244586,
    "اصول المحاكمات الجزائية": 244483,
    "مزاولة مهنة الصيدلة": 196283,
    "الدخول الى لبنان والاقامة فيه والخروج منه": 179943,
    "قانون عقود العمل الجماعية": 244956,
    "حماية الاحداث المخالفين للقانون او المعرضين للخطر": 244401,
    "قانون السير الجديد": 246455,
    "المعاملات الالكترونية والبيانات ذات الطابع الشخصي": 278573,
}


@dataclass
class Article:
    law_name: str
    law_id: int
    article_number: str
    article_id: str           # LawArticleID
    section_id: str           # LawTreeSectionID it was found under
    is_amended: bool          # had the "معدلة" marker
    arabic_text: str
    effective_date: str = ""
    scrape_timestamp: str = ""
    source_url: str = ""


def fetch(session, url, delay=2.0, retries=3):
    """GET with politeness delay and simple retry."""
    for attempt in range(retries):
        try:
            time.sleep(delay)
            r = session.get(url, headers=HEADERS, timeout=30)
            r.encoding = "utf-8"
            if r.status_code == 200:
                return r.text
            logging.warning(f"  HTTP {r.status_code} for {url}")
        except requests.RequestException as e:
            logging.warning(f"  Request error (attempt {attempt+1}): {e}")
    return None


def extract_section_ids(html: str) -> list:
    """
    From a Law.aspx page, pull every distinct LawTreeSectionID in the فهرس tree.
    The whole tree is in the HTML even though it renders collapsed.
    """
    # Match LawTreeSectionID=NUMBER in any href (case-insensitive on the param)
    ids = re.findall(r"LawTreeSectionID=(\d+)", html, flags=re.IGNORECASE)
    # Deduplicate, drop the placeholder 0, preserve order
    seen, out = set(), []
    for i in ids:
        if i != "0" and i not in seen:
            seen.add(i)
            out.append(i)
    return out


# Real section-page structure (verified against live HTML):
#   <tr>
#     <td class="ArticleNumberBullet">&#149;</td>
#     <td class="ArticleNumber">
#        <a href='LawArticles.aspx?LawArticleID=984068&LawId=244611'>المادة 1</a>
#     </td>                              # "المادة 3  - معدلة" when amended
#   </tr>
#   <tr>
#     <td></td>
#     <td class="ArticleText">
#       <p>... ARABIC TEXT ...</p>
#       <div style="direction: ltr">Art.1: ... FRENCH ...</div>   # may be absent
#     </td>
#   </tr>
# We pair each ArticleNumber cell with the ArticleText cell that follows it.
ARTICLE_LINK_RE = re.compile(r"LawArticleID=(\d+)", flags=re.IGNORECASE)
ARTICLE_NUM_RE = re.compile(r"الماد[ةه]\s*(\d+(?:\s*مكرر)?)")
AMENDED_RE = re.compile(r"معدلة")

def _clean_arabic_body(td) -> str:
    """
    Extract Arabic article text from an <td class='ArticleText'>, dropping the
    French <div dir=ltr> block entirely.
    """
    # Work on a copy so we can delete the French nodes
    # The French half is always inside <div style="direction: ltr ...">
    for div in td.find_all("div"):
        # English: Skip if this div was already destroyed (happens with nested divs)
        # Français: Ignorer si ce div a déjà été détruit (se produit avec les divs imbriqués)
        if div.attrs is None:
            continue
            
        style = (div.get("style") or "").replace(" ", "").lower()
        if "direction:ltr" in style:
            div.decompose()
            
    text = td.get_text(" ", strip=True)
    # Collapse whitespace, strip leading nbsp/bullets/colons
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[•\u0095\-\s:]+", "", text).strip()
    # Safety net: if any "Art.N:" French marker survived, cut at it
    fm = re.search(r"\bArt\.?\s*\d+", text)
    if fm:
        text = text[:fm.start()].strip()
    return text

def parse_section_articles(html: str, law_name: str, law_id: int,
                            section_id: str, timestamp: str) -> list:
    """
    Extract all articles from one section page using the real td-class structure.

    For each <td class="ArticleNumber"> that contains an article link, find the
    NEXT <td class="ArticleText"> in document order and pair them.
    """
    soup = BeautifulSoup(html, "html.parser")
    articles = []

    number_cells = soup.find_all("td", class_="ArticleNumber")
    for num_td in number_cells:
        a = num_td.find("a", href=True)
        if not a or "LawArticleID=" not in a["href"]:
            continue
        m = ARTICLE_LINK_RE.search(a["href"])
        if not m or m.group(1) == "0":
            continue
        article_id = m.group(1)

        label = a.get_text(" ", strip=True)   # "المادة 1" / "المادة 3 - معدلة"
        nm = ARTICLE_NUM_RE.search(label)
        if not nm:
            continue
        number = re.sub(r"\s+", " ", nm.group(1)).strip()  # keep "X مكرر" if present
        is_amended = bool(AMENDED_RE.search(label))

        # Find the ArticleText cell that follows this number cell.
        # Walk forward through the tree to the next td.ArticleText.
        text_td = None
        for sib in num_td.find_all_next("td"):
            classes = sib.get("class") or []
            if "ArticleText" in classes:
                text_td = sib
                break
            # If we hit the next ArticleNumber first, this article has no body
            if "ArticleNumber" in classes:
                break
        if text_td is None:
            continue

        body = _clean_arabic_body(text_td)
        if not body or len(body) < 3:
            continue

        articles.append(Article(
            law_name=law_name,
            law_id=law_id,
            article_number=number,
            article_id=article_id,
            section_id=section_id,
            is_amended=is_amended,
            arabic_text=body,
            effective_date="",
            scrape_timestamp=timestamp,
            source_url=f"{BASE}/LawArticles.aspx?LawArticleID={article_id}&LawId={law_id}",
        ))

    return articles


def scrape_law(session, law_name, law_id, out_dir, delay, max_sections=None,
               verbose=False):
    """Scrape every article of one law. Returns list of Article."""
    logging.info(f"\n=== {law_name} (lawId={law_id}) ===")
    law_url = f"{BASE}/Law.aspx?lawId={law_id}"
    html = fetch(session, law_url, delay)
    if not html:
        logging.error(f"  Could not fetch Law.aspx for {law_name}")
        return []

    section_ids = extract_section_ids(html)
    logging.info(f"  Found {len(section_ids)} sections in the فهرس tree")
    if max_sections:
        section_ids = section_ids[:max_sections]

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    all_articles = []
    seen_article_ids = set()

    for n, sid in enumerate(section_ids, 1):
        sec_url = f"{BASE}/LawArticles.aspx?LawTreeSectionID={sid}&LawID={law_id}&language=ar"
        sec_html = fetch(session, sec_url, delay)
        if not sec_html:
            logging.warning(f"  [{n}/{len(section_ids)}] section {sid}: fetch failed")
            continue

        arts = parse_section_articles(sec_html, law_name, law_id, sid, timestamp)
        # Deduplicate: same article can appear in overlapping section views
        new = [a for a in arts if a.article_id not in seen_article_ids]
        for a in new:
            seen_article_ids.add(a.article_id)
        all_articles.extend(new)

        if verbose:
            logging.info(f"  [{n}/{len(section_ids)}] section {sid}: "
                         f"+{len(new)} articles (total {len(all_articles)})")
        elif n % 20 == 0:
            logging.info(f"  [{n}/{len(section_ids)}] total {len(all_articles)} articles")

    logging.info(f"  DONE {law_name}: {len(all_articles)} unique articles")
    return all_articles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("./data/articles"))
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--only", type=str, default=None,
                        help="Scrape just this one law (exact canonical name)")
    parser.add_argument("--max-sections", type=int, default=None,
                        help="Cap sections per law (smoke test)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    struct_dir = args.output / "structured"
    struct_dir.mkdir(parents=True, exist_ok=True)

    if args.only:
        if args.only not in LAW_IDS:
            raise SystemExit(f"Unknown law: {args.only}\nKnown: {list(LAW_IDS)}")
        laws = {args.only: LAW_IDS[args.only]}
    else:
        laws = LAW_IDS

    session = requests.Session()
    # Prime cookies by hitting the homepage once
    fetch(session, BASE + "/Default.aspx", args.delay)

    grand_total = 0
    index = []
    for law_name, law_id in laws.items():
        arts = scrape_law(session, law_name, law_id, struct_dir,
                          args.delay, args.max_sections, args.verbose)
        # Write each article to its own JSON
        for a in arts:
            safe = re.sub(r"\s+", "_", a.law_name)
            fname = f"{safe}__{a.article_number}.json"
            (struct_dir / fname).write_text(
                json.dumps(asdict(a), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            index.append({
                "law_name": a.law_name,
                "article_number": a.article_number,
                "article_id": a.article_id,
                "is_amended": a.is_amended,
                "file": fname,
                "text_length": len(a.arabic_text),
            })
        grand_total += len(arts)

    # Write the combined index
    (args.output / "articles_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n{'='*55}")
    print(f"ARTICLE SCRAPE COMPLETE")
    print(f"{'='*55}")
    print(f"  Laws scraped:     {len(laws)}")
    print(f"  Total articles:   {grand_total}")
    print(f"  Output:           {struct_dir}")
    print(f"  Index:            {args.output / 'articles_index.json'}")


if __name__ == "__main__":
    main()