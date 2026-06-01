"""
Diagnostic tool — analyzes a single URL and suggests CSS selectors.

USAGE:
    python diagnose.py --url https://example.lb/ruling/12345
    python diagnose.py --url https://example.lb/ruling/12345 --save-html

WHAT IT DOES:
    1. Fetches the URL
    2. Looks for Arabic legal patterns (court names, judge titles, dates)
    3. Tells you which CSS selectors point at each field
    4. Reports content statistics (length, Arabic ratio, French presence)
    5. Saves the raw HTML for manual inspection

This is the bridge between "I don't know what selectors to use" and
"I have working selectors I can paste into the source module."
"""

import argparse
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# Arabic patterns that strongly indicate specific fields
COURT_PATTERNS = [
    r"محكمة\s+\S+",                    # محكمة الجنايات, محكمة الاستئناف, etc.
    r"المحكمة\s+\S+",
    r"مجلس\s+\S+",
    r"محكمة الاستئناف\s+\S+",  
    r"محكمة الجنايات\s+\S+",
    r"مجلس شورى الدولة\s+\S+",
    r"مجلس القضاء\s+\S+",
    
] 
JUDGE_PATTERNS = [
    r"القاضي\s+\S+\s+\S+",
    r"الرئيس\s+\S+\s+\S+",
    r"المستشار\s+\S+\s+\S+",
    r"برئاسة\s+\S+\s+\S+",
]
DATE_PATTERNS = [
    r"\d{1,2}[/\-\.]\d{1,2}[/\-\.](19|20)\d{2}",
    r"(19|20)\d{2}[/\-\.]\d{1,2}[/\-\.]\d{1,2}",
    r"تاريخ\s*:?\s*\d",
]


def get_selector_path(element) -> str:
    """Build a CSS selector that uniquely identifies an element."""
    parts = []
    for parent in [element] + list(element.parents):
        if parent.name in (None, "[document]", "html", "body"):
            break
        part = parent.name
        if parent.get("id"):
            part = f"#{parent['id']}"
            parts.insert(0, part)
            break
        if parent.get("class"):
            classes = ".".join(parent["class"][:2])  # first 2 classes
            part += f".{classes}"
        parts.insert(0, part)
        if len(parts) >= 4:
            break
    return " > ".join(parts)


def find_elements_containing(soup, patterns: list[str], label: str):
    """Find HTML elements whose text matches any of the patterns."""
    print(f"\n{'─' * 60}")
    print(f"Looking for: {label}")
    print(f"{'─' * 60}")

    found = []
    for pattern in patterns:
        for element in soup.find_all(string=re.compile(pattern)):
            parent = element.parent
            if parent and parent.name not in ("script", "style"):
                text = parent.get_text(strip=True)[:120]
                selector = get_selector_path(parent)
                found.append((selector, text, parent.name,
                              parent.get("class"), parent.get("id")))

    if not found:
        print(f"   No matches found for {label}")
        print(f"     Patterns tried: {patterns}")
        return

    # Deduplicate by selector
    seen = set()
    unique = []
    for item in found:
        if item[0] not in seen:
            seen.add(item[0])
            unique.append(item)

    print(f"   Found {len(unique)} candidate(s):")
    for i, (selector, text, tag, cls, _id) in enumerate(unique[:5], 1):
        print(f"\n  [{i}] Tag: <{tag}>")
        if cls:
            print(f"      class: {cls}")
            print(f"      → soup.select_one('.{'.'.join(cls)}')")
        if _id:
            print(f"      id: {_id}")
            print(f"      → soup.select_one('#{_id}')")
        print(f"      Sample text: {text!r}")


def analyze_structure(soup):
    """Report overall page structure."""
    print(f"\n{'═' * 60}")
    print("OVERALL STRUCTURE")
    print(f"{'═' * 60}")

    # Find big containers (likely the main content area)
    candidates = []
    for tag in soup.find_all(["article", "main", "div", "section"]):
        text = tag.get_text(strip=True)
        if 500 < len(text) < 50000:
            candidates.append((len(text), tag))

    candidates.sort(reverse=True)
    print(f"\nLargest text containers (likely the ruling body):")
    for length, tag in candidates[:5]:
        selector_hint = tag.name
        if tag.get("class"):
            selector_hint += "." + ".".join(tag["class"][:2])
        if tag.get("id"):
            selector_hint += f"#{tag['id']}"
        print(f"  • {selector_hint}: {length} chars")


def analyze_content(soup, raw_html: str):
    """Report content statistics."""
    print(f"\n{'═' * 60}")
    print("CONTENT STATISTICS")
    print(f"{'═' * 60}")

    text = soup.get_text(" ", strip=True)
    arabic_chars = len(re.findall(r"[\u0600-\u06FF]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    digits = len(re.findall(r"\d", text))
    total = len(text)

    print(f"  Total text length: {total} characters")
    print(f"  Arabic characters: {arabic_chars} ({100*arabic_chars/max(total,1):.1f}%)")
    print(f"  Latin characters:  {latin_chars} ({100*latin_chars/max(total,1):.1f}%)")
    print(f"  Digits:            {digits}")

    if latin_chars > 50:
        print(f"\n  ⚠  Significant Latin content detected — likely French.")
        print(f"     Will be filtered in build_dataset.py.")

    # Check for article citations
    citations = re.findall(r"المادة\s+\d+", text)
    print(f"\n  Article citations found: {len(citations)}")
    if citations:
        print(f"  Examples: {citations[:5]}")

    # Check section markers
    print(f"\n  Section markers present:")
    for label, marker in [
        ("Case facts (الوقائع)", "الوقائع"),
        ("Reasoning (الأسباب)", "الأسباب"),
        ("Verdict (لهذه الأسباب)", "لهذه الأسباب"),
        ("Court (محكمة)", "محكمة"),
    ]:
        present = marker in text
        symbol = "✓" if present else "❌"
        print(f"    {symbol} {label}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="URL of one ruling page to analyze")
    parser.add_argument("--save-html", action="store_true",
                        help="Save raw HTML to ./diagnostic_output/")
    parser.add_argument("--cookies", help="Cookie string if page requires login")
    args = parser.parse_args()

    print(f"\nFetching {args.url}...")
    headers = {"User-Agent": "PU-Legal-Research-Diagnostic/1.0",
               "Accept-Language": "ar,en;q=0.5"}
    cookies = {}
    if args.cookies:
        for pair in args.cookies.split(";"):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                cookies[k] = v

    response = requests.get(args.url, headers=headers, cookies=cookies, timeout=30)
    response.encoding = response.apparent_encoding
    print(f"Status: {response.status_code}, Size: {len(response.text)} chars")

    if response.status_code != 200:
        print("⚠  Non-200 status code — page may require login or URL is wrong")
        return

    soup = BeautifulSoup(response.text, "html.parser")

    if args.save_html:
        out_dir = Path("./diagnostic_output")
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "page.html"
        out_path.write_text(response.text, encoding="utf-8")
        print(f"\nSaved raw HTML to {out_path}")

    analyze_structure(soup)
    analyze_content(soup, response.text)
    find_elements_containing(soup, COURT_PATTERNS, "court name")
    find_elements_containing(soup, JUDGE_PATTERNS, "judge name(s)")
    find_elements_containing(soup, DATE_PATTERNS, "ruling date")

    print(f"\n{'═' * 60}")
    print("NEXT STEPS")
    print(f"{'═' * 60}")
    print("1. Open the saved HTML in a browser to confirm what each field looks like")
    print("2. Copy the suggested selectors above into sources/public.py or sources/pu.py")
    print("3. Run: python scrape.py --source <name> --max-rulings 5 --verbose")
    print("4. Inspect the resulting JSON files and iterate\n")


if __name__ == "__main__":
    main()
