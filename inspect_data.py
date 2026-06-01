"""
Inspection tool — eyeball specific rulings after scraping.

USAGE:
    # Look at all incomplete rulings (no articles, no summary, etc.)
    python inspect_data.py --input data/public/structured/ --filter incomplete

    # Look at the shortest summaries (likely extraction problems)
    python inspect_data.py --input data/public/structured/ --filter short

    # Look at the rulings with no cited articles
    python inspect_data.py --input data/public/structured/ --filter no-articles

    # Look at a specific ruling by ID
    python inspect_data.py --input data/public/structured/ --id 154869

    # Look at the first N rulings (sanity check)
    python inspect_data.py --input data/public/structured/ --filter all --limit 5

    # See the full text fallback if you suspect summary extraction is wrong
    python inspect_data.py --input data/public/structured/ --id 154869 --show-full-text

WHAT IT SHOWS PER RULING:
    - Metadata (court, judges, date, year)
    - Topics
    - Cited articles (number + law name)
    - The page summary text (so you can read it and verify it's the right paragraph)
    - Source URL (so you can open the original page side-by-side)
"""

import argparse
import json
from pathlib import Path


def load_rulings(input_dir: Path):
    files = sorted(input_dir.glob("*.json"))
    rulings = []
    for f in files:
        try:
            rulings.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"  Error reading {f.name}: {e}")
    return rulings


def filter_rulings(rulings, filter_name: str):
    if filter_name == "incomplete":
        return [r for r in rulings if not r.get("is_complete")]
    if filter_name == "no-articles":
        return [r for r in rulings if not r.get("cited_articles")]
    if filter_name == "no-summary":
        return [r for r in rulings if not r.get("page_summary")]
    if filter_name == "short":
        # Rulings with summaries shorter than the median
        with_summary = [(r, len(r.get("page_summary") or "")) for r in rulings]
        with_summary = [(r, n) for r, n in with_summary if n > 0]
        with_summary.sort(key=lambda x: x[1])
        return [r for r, _ in with_summary[: max(10, len(with_summary) // 4)]]
    if filter_name == "no-topics":
        return [r for r in rulings if not r.get("topics")]
    if filter_name == "many-articles":
        return sorted(
            rulings,
            key=lambda r: len(r.get("cited_articles") or []),
            reverse=True,
        )[:20]
    # default: all rulings
    return rulings


def print_ruling(r, show_full_text: bool = False):
    """Print one ruling in a readable format."""
    print()
    print("═" * 70)
    print(f"  ID:        {r.get('ruling_id')}")
    print(f"  URL:       {r.get('source_url')}")
    print(f"  Court:     {r.get('court_name')}")
    print(f"  Date:      {r.get('ruling_date')} (year {r.get('ruling_year')})")
    print(f"  Judges:    {', '.join(r.get('judge_names') or [])}")
    print(f"  Complete:  {r.get('is_complete')}")
    print(f"  Has Fr:    {r.get('has_french')}")
    print("─" * 70)

    topics = r.get("topics") or []
    print(f"  Topics ({len(topics)}):")
    for t in topics:
        print(f"    • {t}")
    print("─" * 70)

    articles = r.get("cited_articles") or []
    articles_full = r.get("cited_articles_full") or []
    print(f"  Cited articles ({len(articles)}):")
    if articles_full:
        for c in articles_full:
            print(f"    • Article {c['number']} — {c['law']}")
    elif articles:
        print(f"    Numbers only: {', '.join(articles)}")
    else:
        print("    (none)")
    print("─" * 70)

    summary = r.get("page_summary") or ""
    print(f"  Page summary ({len(summary)} chars):")
    if summary:
        # Wrap at ~100 chars for readability
        words = summary.split()
        line = "    "
        for w in words:
            if len(line) + len(w) > 100:
                print(line)
                line = "    " + w
            else:
                line += (" " if line != "    " else "") + w
        if line.strip():
            print(line)
    else:
        print("    (empty)")
    print("─" * 70)

    if show_full_text:
        full = r.get("full_text") or ""
        print(f"  Full text ({len(full)} chars):")
        print("    " + full[:1500].replace("\n", "\n    "))
        if len(full) > 1500:
            print(f"    ... ({len(full) - 1500} more chars)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True,
                        help="Directory of scraped JSON files")
    parser.add_argument("--filter",
                        choices=["all", "incomplete", "no-articles", "no-summary",
                                "short", "no-topics", "many-articles"],
                        default="all",
                        help="Which rulings to inspect")
    parser.add_argument("--id", type=str, help="Inspect one specific ruling by ID")
    parser.add_argument("--limit", type=int, default=20,
                        help="Maximum number of rulings to print")
    parser.add_argument("--show-full-text", action="store_true",
                        help="Also show the raw full_text fallback (verbose)")
    args = parser.parse_args()

    rulings = load_rulings(args.input)
    print(f"Loaded {len(rulings)} rulings from {args.input}")

    if args.id:
        match = [r for r in rulings if r.get("ruling_id") == args.id]
        if not match:
            print(f"No ruling with ID {args.id}")
            return
        for r in match:
            print_ruling(r, args.show_full_text)
        return

    filtered = filter_rulings(rulings, args.filter)
    print(f"Filter '{args.filter}' → {len(filtered)} rulings (showing {min(args.limit, len(filtered))})")

    for r in filtered[:args.limit]:
        print_ruling(r, args.show_full_text)

    print()
    print("═" * 70)
    print(f"Done. {min(args.limit, len(filtered))} rulings shown.")


if __name__ == "__main__":
    main()