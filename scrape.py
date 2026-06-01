"""
Main scraper entry point.

USAGE:
    python scrape.py --source public --max-rulings 5 --verbose
    python scrape.py --source public --max-rulings 100
    python scrape.py --source public --max-rulings 2000 --resume
"""

import argparse
import importlib
import logging
import re
import time
from pathlib import Path

from core import create_session, login_if_needed, fetch_page, save_ruling, load_checkpoint


def load_source(name: str):
    try:
        module = importlib.import_module(f"sources.{name}")
        return module.CONFIG, module
    except ImportError as e:
        raise SystemExit(f"Could not load source '{name}': {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["public", "pu"], required=True)
    parser.add_argument("--output", type=Path, default=Path("./data"))
    parser.add_argument("--max-rulings", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--court-id", type=int, default=0,
                        help="Filter by court ID (default 0 = all). "
                             "Common IDs: 49979=محكمة الجنايات, 49978=مجالس العمل التحكيمية, "
                             "49975=تمييز جزائي, 49976=تمييز مدني, 49977=شورى")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(args.output / f"scrape_{args.source}.log", encoding="utf-8"),
        ],
    )

    config, source_module = load_source(args.source)
    logging.info(f"Loaded source: {config.name} ({config.base_url})")

    session = create_session(config)
    credentials = None
    if config.requires_login:
        credentials = source_module.get_credentials()
    if not login_if_needed(session, config, credentials):
        raise SystemExit("Login failed")

    already_done = load_checkpoint(args.output, config.name) if args.resume else set()
    if already_done:
        logging.info(f"Resume: {len(already_done)} already scraped")

    logging.info("Discovering ruling URLs...")
    urls = config.discover_ruling_urls(session, args.max_rulings, court_id=args.court_id)
    logging.info(f"Found {len(urls)} URLs")

    stats = {"success": 0, "skipped": 0, "failed": 0, "incomplete": 0}

    for i, url in enumerate(urls, 1):
        m = re.search(r"ID=(\d+)", url)
        check_id = m.group(1) if m else url.rstrip("/").split("/")[-1]
        if check_id in already_done:
            stats["skipped"] += 1
            continue

        logging.info(f"[{i}/{len(urls)}] {url}")
        html = fetch_page(session, url, config.request_delay)
        if html is None:
            stats["failed"] += 1
            continue

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            ruling = config.parse_ruling_html(html, url, config.name, timestamp)
        except Exception as e:
            logging.error(f"  Parse error: {e}")
            stats["failed"] += 1
            continue

        save_ruling(ruling, args.output, html)
        stats["success"] += 1
        if not ruling.is_complete:
            stats["incomplete"] += 1

        if args.verbose:
            logging.debug(f"  Court: {ruling.court_name}")
            logging.debug(f"  Judges: {ruling.judge_names}")
            logging.debug(f"  Topics: {len(ruling.topics)}")
            logging.debug(f"  Articles: {ruling.cited_articles}")
            logging.debug(f"  Summary: {len(ruling.page_summary or '')} chars")
            logging.debug(f"  Complete: {ruling.is_complete}")

    logging.info(f"\nDone. {stats}")


if __name__ == "__main__":
    main()