"""
Reparse tool — re-extract structured fields from saved raw HTML.

USE THIS WHEN:
    - You discover an extraction bug after a long scrape
    - You improve the parsing logic in sources/public.py
    - You don't want to re-scrape thousands of rulings just to fix the JSON

USAGE:
    # Reparse every ruling under data/public/
    python reparse.py --source public

    # Test on a small sample first (recommended)
    python reparse.py --source public --limit 10 --dry-run

    # Reparse a specific ruling by ID
    python reparse.py --source public --id 154869

WHAT IT DOES:
    1. Reads every HTML file in data/{source}/raw/
    2. Runs the current parse_ruling_html() against each
    3. Writes the new JSON to data/{source}/structured/ (overwrites old)
    4. Reports a before/after summary

NOTHING IS RE-SCRAPED. Network is not touched.
"""

import argparse
import importlib
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path


def load_source(name: str):
    try:
        module = importlib.import_module(f"sources.{name}")
        return module.CONFIG, module
    except ImportError as e:
        raise SystemExit(f"Could not load source '{name}': {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["public", "pu"], required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    parser.add_argument("--raw-subdir", type=str, default="raw",
                        help="Name of the raw HTML subdirectory under data/{source}/ "
                             "(e.g. 'raw_jinayet' or 'raw_labor')")
    parser.add_argument("--structured-subdir", type=str, default="structured",
                        help="Name of the structured JSON subdirectory under data/{source}/ "
                             "(e.g. 'structured_jinayet' or 'structured_labor')")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only reparse the first N rulings (for testing)")
    parser.add_argument("--id", type=str,
                        help="Reparse one specific ruling by ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    config, _ = load_source(args.source)

    raw_dir = args.data_dir / config.name / args.raw_subdir
    json_dir = args.data_dir / config.name / args.structured_subdir

    if not raw_dir.exists():
        raise SystemExit(f"Raw HTML directory not found: {raw_dir}")

    # Collect files to process
    if args.id:
        html_files = [raw_dir / f"{args.id}.html"]
        if not html_files[0].exists():
            raise SystemExit(f"No HTML file for ID {args.id}")
    else:
        html_files = sorted(raw_dir.glob("*.html"))
        if args.limit:
            html_files = html_files[: args.limit]

    logging.info(f"Reparsing {len(html_files)} HTML files...")
    if args.dry_run:
        logging.info("DRY RUN — no JSON files will be written")

    # Track before/after stats
    stats = {
        "total": 0,
        "summary_changed": 0,
        "summary_appeared": 0,    # was empty before, now populated
        "summary_disappeared": 0, # was populated before, now empty (bad!)
        "summary_same": 0,
        "complete_before": 0,
        "complete_after": 0,
        "errors": 0,
    }

    for html_path in html_files:
        ruling_id = html_path.stem
        stats["total"] += 1

        try:
            html = html_path.read_text(encoding="utf-8")
        except Exception as e:
            logging.warning(f"Could not read {html_path}: {e}")
            stats["errors"] += 1
            continue

        # Load the OLD JSON to compare
        json_path = json_dir / f"{ruling_id}.json"
        old_summary = None
        old_complete = False
        old_url = f"http://77.42.251.205/ViewRulePage.aspx?ID={ruling_id}&selection="
        if json_path.exists():
            try:
                old = json.loads(json_path.read_text(encoding="utf-8"))
                old_summary = old.get("page_summary")
                old_complete = bool(old.get("is_complete"))
                old_url = old.get("source_url") or old_url
                if old_complete:
                    stats["complete_before"] += 1
            except Exception:
                pass

        # Reparse with current logic
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            new = config.parse_ruling_html(html, old_url, config.name, timestamp)
        except Exception as e:
            logging.error(f"Parse error for {ruling_id}: {e}")
            stats["errors"] += 1
            continue

        new_summary = new.page_summary
        if new.is_complete:
            stats["complete_after"] += 1

        # Compare summaries
        if (old_summary or "") == (new_summary or ""):
            stats["summary_same"] += 1
        else:
            stats["summary_changed"] += 1
            if not old_summary and new_summary:
                stats["summary_appeared"] += 1
            elif old_summary and not new_summary:
                stats["summary_disappeared"] += 1
                logging.warning(f"  {ruling_id}: summary disappeared after reparse!")

            if args.verbose:
                logging.debug(f"  {ruling_id}: summary changed")
                logging.debug(f"    OLD ({len(old_summary or '')} chars): {(old_summary or '')[:120]!r}")
                logging.debug(f"    NEW ({len(new_summary or '')} chars): {(new_summary or '')[:120]!r}")

        # Write the updated JSON (unless dry-run)
        if not args.dry_run:
            json_dir.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(asdict(new), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        if stats["total"] % 200 == 0:
            logging.info(f"  Processed {stats['total']}/{len(html_files)}")

    # Final summary
    print()
    print("=" * 60)
    print(f"REPARSE SUMMARY")
    print("=" * 60)
    print(f"  Total processed:        {stats['total']}")
    print(f"  Summary unchanged:      {stats['summary_same']}")
    print(f"  Summary changed:        {stats['summary_changed']}")
    print(f"    - newly populated:    {stats['summary_appeared']}")
    print(f"    - went empty (bad):   {stats['summary_disappeared']}")
    print(f"  Complete before:        {stats['complete_before']}")
    print(f"  Complete after:         {stats['complete_after']}")
    print(f"  Errors:                 {stats['errors']}")

    if stats["summary_disappeared"] > 0:
        print(f"\n  ⚠  {stats['summary_disappeared']} rulings lost their summary.")
        print(f"     Run: python inspect_data.py --filter no-summary --limit 5")
        print(f"     to see what was lost — the new extractor may need more tuning.")

    if args.dry_run:
        print(f"\n  DRY RUN — no files were modified.")
        print(f"  Re-run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()