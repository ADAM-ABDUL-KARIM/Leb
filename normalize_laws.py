"""
Normalize law names in scraped ruling JSONs — PERMANENT in-place fix.

Why: the same law appears under several spellings in the raw data:
    قانون اصول المحاكمات  المدنية   (double space)
    قانون أصول المحاكمات المدنية    (hamza أ)
    قانون اصول المحاكمات المدنية    (no hamza)
These are ONE law. If left unnormalized, joining rulings to scraped article
texts fails on string mismatch and training pairs are silently lost.

This script rewrites cited_articles_full[].law (and re-derives any law-based
fields) so every reference to the same law uses one canonical string.

USAGE:
    # Always dry-run first to see what would change
    python normalize_laws.py --input data/public/structured_combined/ --dry-run

    # Apply for real
    python normalize_laws.py --input data/public/structured_combined/

SAFETY: only touches the folder you point at. Keep structured_jinayet/ and
structured_labor/ as untouched backups; run this on the combined working copy.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path


def normalize_law_name(raw: str) -> str:
    """
    Canonicalize a single law name.

    Steps:
      1. Collapse all whitespace runs to a single space
      2. Normalize alef variants: أ إ آ ا  ->  ا
      3. Strip edge whitespace/punctuation
    Then map known spelling variants to one canonical form.
    """
    if not raw:
        return raw
    s = re.sub(r"\s+", " ", raw.strip())
    # Normalize alef hamza variants to bare alef
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    # Normalize alef maqsura / ta marbuta are left as-is (meaningful in Arabic)
    s = s.strip(" \t،.-")

    # Canonical-form mapping for known equivalent names.
    # Keys are already alef-normalized so they match the output of the steps above.
    CANONICAL = {
        # Civil procedure — three spellings collapse to one
        "قانون اصول المحاكمات المدنية": "قانون اصول المحاكمات المدنية",
        # Traffic law — "السير" and "السير الجديد" are the same modern law
        "قانون السير": "قانون السير الجديد",
        "قانون السير الجديد": "قانون السير الجديد",
        # Commercial law — التجارة and التجارة البرية resolve to same lawId on site
        "قانون التجارة": "قانون التجارة",
        "قانون التجارة البرية": "قانون التجارة",
        # Collective labor contracts — short and long form
        "قانون عقود العمل الجماعية": "قانون عقود العمل الجماعية",
        "قانون عقود العمل الجماعية والوساطة والتحكيم": "قانون عقود العمل الجماعية",
        # Weapons — singular/plural spellings
        "الاسلحة والذخائر": "الاسلحة والذخائر",
        "اسلحة وذخائر": "الاسلحة والذخائر",
    }
    return CANONICAL.get(s, s)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    json_files = sorted(args.input.glob("*.json"))
    if not json_files:
        raise SystemExit(f"No JSON files in {args.input}")

    print(f"Processing {len(json_files)} rulings in {args.input}")
    if args.dry_run:
        print("DRY RUN — no files will be written\n")

    changes = Counter()          # (old -> new) -> count
    files_changed = 0
    total_articles_touched = 0

    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  Error reading {jf.name}: {e}")
            continue

        file_modified = False

        for art in data.get("cited_articles_full") or []:
            old_law = art.get("law")
            if not old_law:
                continue
            new_law = normalize_law_name(old_law)
            if new_law != old_law:
                changes[(old_law, new_law)] += 1
                total_articles_touched += 1
                art["law"] = new_law
                file_modified = True

        if file_modified:
            files_changed += 1
            if not args.dry_run:
                jf.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    print(f"Files changed:          {files_changed}")
    print(f"Article-laws rewritten: {total_articles_touched}\n")

    if changes:
        print("Normalizations applied (old -> new : count):")
        for (old, new), cnt in changes.most_common():
            print(f"  {cnt:5d}  {old!r}")
            print(f"         -> {new!r}")
    else:
        print("No changes needed — law names already canonical.")

    if args.dry_run:
        print("\nDRY RUN complete. Re-run without --dry-run to apply.")


if __name__ == "__main__":
    main()