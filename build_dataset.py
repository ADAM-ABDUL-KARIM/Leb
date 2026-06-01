"""
Post-processing: convert scraped JSON files into training-ready dataset.

USAGE:
    python build_dataset.py --input ./data/structured --output ./training_data

WHAT IT DOES:
    1. Loads all scraped Ruling JSON files
    2. Filters out: incomplete rulings, French-heavy rulings, too-short texts
    3. Removes any remaining French segments from Arabic text
    4. Produces a clean CSV/JSONL dataset of (case, articles, ruling) triples
    5. Reports statistics on the final dataset (judges, courts, years, areas)
"""

import argparse
import json
import logging
import re
from collections import Counter
from pathlib import Path

import pandas as pd


# ============================================================
# CLEANING — Arabic-only text normalization
# ============================================================

def remove_french(text: str) -> str:
    """
    Remove French/Latin segments from Arabic text.
    
    Strategy: find runs of Latin characters (with surrounding punctuation)
    and delete them. This handles the bilingual articles in Marwa's dataset
    where Arabic is followed by an exact French translation.
    """
    if not text:
        return text
    
    # Pattern matches: optional "Art. N:" header + Latin sentences
    # We're aggressive here because the doctors said "no French at all"
    text = re.sub(r"Art\.\s*\d+\s*:.*", "", text, flags=re.DOTALL)
    
    # Remove long Latin runs (sentences/paragraphs)
    # Keep short isolated tokens (might be numbers, dates, abbreviations)
    text = re.sub(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s,;:'\.\-\(\)]{20,}", "", text)
    
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_arabic(text: str) -> str:
    """
    Light Arabic normalization. Keep this conservative — over-normalizing
    can destroy legal precision (e.g. removing tashkeel changes meanings).
    """
    if not text:
        return text
    # Normalize alef variants (commonly inconsistent in legal text)
    text = re.sub(r"[إأآا]", "ا", text)
    # Normalize ya variants
    text = re.sub(r"[ىي]", "ي", text)
    # Normalize ta marbuta vs ha
    # NOTE: legal text often distinguishes these — DO NOT enable by default
    # text = re.sub(r"ة", "ه", text)
    return text


# ============================================================
# FILTERING
# ============================================================

def is_valid_ruling(ruling: dict, min_length: int = 100) -> tuple[bool, str]:
    """Returns (is_valid, reason_if_not)."""
    if not ruling.get("is_complete"):
        return False, "missing triple fields"
    if not ruling.get("case_description"):
        return False, "no case description"
    if not ruling.get("ruling_text"):
        return False, "no ruling text"
    if not ruling.get("cited_articles"):
        return False, "no article citations"
    
    total_len = (len(ruling.get("case_description") or "") +
                 len(ruling.get("ruling_text") or ""))
    if total_len < min_length:
        return False, f"too short ({total_len} chars)"
    
    return True, ""


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True,
                        help="Directory containing scraped JSON files")
    parser.add_argument("--output", type=Path, required=True,
                        help="Where to write training-ready dataset")
    parser.add_argument("--min-length", type=int, default=100)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args.output.mkdir(parents=True, exist_ok=True)

    # Load all scraped rulings
    json_files = list(args.input.glob("*.json"))
    logging.info(f"Found {len(json_files)} scraped rulings")

    triples = []
    rejection_stats = Counter()
    
    for jf in json_files:
        ruling = json.loads(jf.read_text(encoding="utf-8"))
        
        valid, reason = is_valid_ruling(ruling, args.min_length)
        if not valid:
            rejection_stats[reason] += 1
            continue
        
        # Clean each field
        case = remove_french(ruling["case_description"])
        verdict = remove_french(ruling["ruling_text"])
        reasoning = remove_french(ruling.get("reasoning") or "")
        
        # Re-check length after French removal
        if len(case) + len(verdict) < args.min_length:
            rejection_stats["too short after cleaning"] += 1
            continue
        
        triples.append({
            "ruling_id": ruling["ruling_id"],
            "court_name": ruling.get("court_name"),
            "judges": "; ".join(ruling.get("judge_names") or []),
            "year": ruling.get("ruling_year"),
            "law_area": ruling.get("law_area"),
            "case_description": case,
            "reasoning": reasoning,
            "cited_articles": ", ".join(ruling.get("cited_articles") or []),
            "ruling_text": verdict,
        })

    # Save as both CSV and JSONL (JSONL is friendlier for huggingface datasets)
    df = pd.DataFrame(triples)
    df.to_csv(args.output / "triples.csv", index=False, encoding="utf-8-sig")
    
    with open(args.output / "triples.jsonl", "w", encoding="utf-8") as f:
        for t in triples:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    # Report statistics — these are the numbers will care about
    logging.info(f"\n=== Dataset Summary ===")
    logging.info(f"Total valid triples: {len(triples)}")
    logging.info(f"Rejections: {dict(rejection_stats)}")
    
    if triples:
        unique_judges = set()
        for t in triples:
            for j in (t["judges"] or "").split(";"):
                if j.strip():
                    unique_judges.add(j.strip())
        
        logging.info(f"Unique judges: {len(unique_judges)}")
        logging.info(f"Unique courts: {df['court_name'].nunique()}")
        logging.info(f"Year range: {df['year'].min()} – {df['year'].max()}")
        logging.info(f"\nCourt distribution:\n{df['court_name'].value_counts().head(10)}")
        logging.info(f"\nLaw area distribution:\n{df['law_area'].value_counts()}")
        logging.info(f"\nAvg case desc length: {df['case_description'].str.len().mean():.0f} chars")
        logging.info(f"Avg ruling length: {df['ruling_text'].str.len().mean():.0f} chars")
        logging.info(f"Avg articles cited: {df['cited_articles'].str.split(',').str.len().mean():.1f}")


if __name__ == "__main__":
    main()
