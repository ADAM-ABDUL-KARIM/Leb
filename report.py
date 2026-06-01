"""
Quick reporting tool — summarizes what scraped so far.

USAGE:
    python report.py --input ./data/public/structured
    python report.py --input ./data/public/structured --show-judges
    python report.py --input ./data/public/structured --show-topics

WHAT IT REPORTS:
    - Total rulings scraped
    - Unique judges
    - Court distribution
    - Year coverage
    - Topic diversity
    - PDF download success rate
    - Completeness statistics
"""

import argparse
import json
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True,
                        help="Directory of scraped JSON files")
    parser.add_argument("--show-judges", action="store_true",
                        help="Print full list of unique judges")
    parser.add_argument("--show-topics", action="store_true",
                        help="Print top topic tags")
    parser.add_argument("--show-courts", action="store_true",
                        help="Print court distribution")
    args = parser.parse_args()

    json_files = list(args.input.glob("*.json"))
    if not json_files:
        print(f"No JSON files in {args.input}")
        return

    print(f"\n{'═' * 60}")
    print(f"SCRAPE REPORT — {len(json_files)} rulings")
    print(f"{'═' * 60}\n")

    rulings = []
    for jf in json_files:
        try:
            rulings.append(json.loads(jf.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"  Error reading {jf.name}: {e}")

    # Court types
    courts = Counter()
    for r in rulings:
        courts[r.get("court_name") or "(unknown)"] += 1

    print("COURTS")
    print("─" * 40)
    for court, n in courts.most_common(10):
        print(f"  {n:5d}  {court}")
    if len(courts) > 10:
        print(f"  ... and {len(courts) - 10} more")
    print(f"  TOTAL: {len(courts)} unique court types\n")

    # Years
    years = Counter()
    for r in rulings:
        if r.get("ruling_year"):
            years[r["ruling_year"]] += 1
    print("YEARS")
    print("─" * 40)
    if years:
        print(f"  Range: {min(years)} – {max(years)}")
        print(f"  Distinct years: {len(years)}")
        for year in sorted(years.keys(), reverse=True)[:5]:
            print(f"    {year}: {years[year]} rulings")
    print()

    # Judges — this is the key  metric
    all_judges = Counter()
    panels = Counter()
    for r in rulings:
        judges = r.get("judge_names") or []
        if judges:
            panels[tuple(sorted(judges))] += 1
            for j in judges:
                all_judges[j] += 1

    print("JUDGES 30+ requirement)")
    print("─" * 40)
    print(f"  Unique judges: {len(all_judges)}")
    print(f"  Unique panels: {len(panels)}")
    if len(all_judges) >= 30:
        print(f"  ✓ Meets the 30+ judge requirement")
    else:
        print(f"  ⚠  Need {30 - len(all_judges)} more judges to reach 30")
    print(f"\n  Top 10 most active judges:")
    for judge, n in all_judges.most_common(10):
        print(f"    {n:5d}  {judge}")
    print()

    if args.show_judges:
        print("  ALL JUDGES:")
        for judge, n in all_judges.most_common():
            print(f"    {n:5d}  {judge}")
        print()

    # Topics
    all_topics = Counter()
    for r in rulings:
        for t in r.get("topics") or []:
            all_topics[t] += 1
    print("TOPICS")
    print("─" * 40)
    print(f"  Unique topics: {len(all_topics)}")
    print(f"  Total topic mentions: {sum(all_topics.values())}")
    avg_topics = sum(len(r.get('topics') or []) for r in rulings) / max(len(rulings), 1)
    print(f"  Avg topics per ruling: {avg_topics:.1f}")
    print(f"\n  Top 10 most common topics:")
    for topic, n in all_topics.most_common(10):
        print(f"    {n:5d}  {topic}")
    print()

    if args.show_topics:
        print("  ALL TOPICS (sorted by frequency):")
        for topic, n in all_topics.most_common():
            print(f"    {n:5d}  {topic}")
        print()

    # Articles
    article_counts = []
    unique_articles = set()
    for r in rulings:
        articles = r.get("cited_articles") or []
        article_counts.append(len(articles))
        unique_articles.update(articles)
    print("CITED ARTICLES")
    print("─" * 40)
    print(f"  Unique articles referenced: {len(unique_articles)}")
    print(f"  Avg articles per ruling: {sum(article_counts)/max(len(article_counts),1):.1f}")
    print(f"  Rulings with 0 articles: {sum(1 for c in article_counts if c == 0)}")
    print(f"  Rulings with 1+ articles: {sum(1 for c in article_counts if c >= 1)}")
    print()

    # Completeness
    summary_lengths = [len(r.get("page_summary") or "") for r in rulings]
    print("DATA QUALITY")
    print("─" * 40)
    print(f"  Complete (summary + articles): {sum(1 for r in rulings if r.get('is_complete'))}/{len(rulings)}")
    print(f"  With page summary: {sum(1 for r in rulings if r.get('page_summary'))}")
    print(f"  With ≥1 cited article: {sum(1 for c in article_counts if c >= 1)}")
    print(f"  Avg summary length: {sum(summary_lengths)/max(len(summary_lengths),1):.0f} chars")
    print(f"  Contains French: {sum(1 for r in rulings if r.get('has_french'))}")
    print()

    # PDFs
    with_pdf_url = sum(1 for r in rulings if r.get("pdf_url"))
    pdf_downloaded = sum(1 for r in rulings if r.get("pdf_downloaded"))
    print("PDFs")
    print("─" * 40)
    print(f"  Rulings with PDF link: {with_pdf_url}/{len(rulings)}")
    print(f"  PDFs downloaded: {pdf_downloaded}")
    if pdf_downloaded:
        sizes = [r["pdf_size_bytes"] for r in rulings if r.get("pdf_size_bytes")]
        if sizes:
            total_mb = sum(sizes) / 1024 / 1024
            print(f"  Total PDF storage: {total_mb:.1f} MB")
            print(f"  Avg PDF size: {sum(sizes)/len(sizes)/1024:.0f} KB")
    print()


if __name__ == "__main__":
    main()
