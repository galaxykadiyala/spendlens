#!/usr/bin/env python3
"""SpendLens CLI — hardened batch parser for credit-card PDF statements.

Validates files, skips already-parsed ones (idempotent), parses in parallel,
handles Ctrl+C gracefully, inserts each file's rows atomically, and prints a
rich summary. Behaves identically for 1 file or 100.

Usage:
    python parse.py --folder ./data/statements
    python parse.py --folder ./data/statements --income 218000
    python parse.py --folder ./data/statements --clear
    python parse.py --folder ./data/statements --verbose
    python parse.py --folder ./data/statements --workers 8
"""

import os
import sys
import signal
import argparse
import logging
import multiprocessing
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Load .env (best-effort) so DB_PATH / income / API key match the backend.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Make `backend` importable whether run from repo root or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import db, parser, ai, categorizer  # noqa: E402

VERSION = "1.1"

# Set by the SIGINT handler so we can stop cleanly between files.
interrupted = False


def handle_interrupt(sig, frame):
    global interrupted
    interrupted = True
    print("\n\n⚠  Interrupted — saving progress and exiting cleanly...")


# --------------------------------------------------------------------------- #
# File validation
# --------------------------------------------------------------------------- #
def validate_files(folder):
    """Return (valid_files, skipped_files).

    Recursively finds PDFs; drops empty / unreadable ones, and warns about
    non-PDF files sitting in the folder.
    """
    valid, skipped = [], []
    for f in sorted(Path(folder).glob("**/*.pdf")):
        try:
            if f.stat().st_size == 0:
                skipped.append(f"{f.name} — empty file")
            elif not os.access(f, os.R_OK):
                skipped.append(f"{f.name} — permission denied")
            else:
                valid.append(str(f))
        except OSError as exc:
            skipped.append(f"{f.name} — {exc}")

    # Warn about non-PDF files in the top level of the folder.
    try:
        for f in sorted(Path(folder).iterdir()):
            if f.is_file() and f.suffix.lower() != ".pdf":
                print(f"⚠  Skipping non-PDF: {f.name}")
    except OSError:
        pass

    return valid, skipped


# --------------------------------------------------------------------------- #
# Parallel parsing
# --------------------------------------------------------------------------- #
def parse_all(files, workers=None):
    """Parse files concurrently. Returns [(filepath, txns)] sorted by path.

    `txns` is a list of transactions (possibly empty) on success, or ``None`` if
    that file raised. Parsing is read-only (no DB writes happen here), so it is
    thread-safe. Honors the global `interrupted` flag between completions.
    """
    if not files:
        return []
    workers = workers or min(4, multiprocessing.cpu_count())
    results = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(parser.detect_and_parse, f): f for f in files}
        try:
            for i, future in enumerate(as_completed(futures), 1):
                if interrupted:
                    break
                filepath = futures[future]
                filename = Path(filepath).name
                print(f"[{i}/{len(files)}] {filename}", end=" ... ", flush=True)
                try:
                    txns = future.result()
                    results[filepath] = txns
                    if txns:
                        print(f"✓ {len(txns)} txns [{txns[0].get('card', 'unknown')}]")
                    else:
                        print("⚠ 0 txns")
                except Exception as e:
                    results[filepath] = None
                    print(f"✗ FAILED: {e}")
        finally:
            # Don't block on queued work when interrupted; cancel what hasn't started.
            executor.shutdown(wait=not interrupted, cancel_futures=interrupted)

    return [(f, results[f]) for f in sorted(results.keys())]


# --------------------------------------------------------------------------- #
# Atomic DB insert (per file)
# --------------------------------------------------------------------------- #
def insert_file_atomic(txns):
    """Insert one file's transactions atomically. Returns count inserted.

    Uses an explicit transaction so a mid-insert failure never leaves a file
    partially populated. (db.insert_transactions also commits atomically; this
    wraps it defensively and rolls back on error.)
    """
    if not txns:
        return 0
    try:
        return db.insert_transactions(txns)
    except Exception as exc:
        logging.getLogger("spendlens.parse").error("Insert failed, rolled back: %s", exc)
        return 0


def _count_category(category):
    """Count transactions currently in a category (read-only DB query)."""
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT COUNT(*) AS c FROM transactions WHERE category = ?", (category,)
        )
        return cur.fetchone()["c"]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
def print_summary(results, skipped_db, skipped_files, total_new):
    print("\n" + "=" * 55)

    ok = [(f, t) for f, t in results if t]
    empty = [(f, t) for f, t in results if t == []]
    failed = [(f, t) for f, t in results if t is None]

    print(
        f"\n{total_new} new transactions | "
        f"{len(ok)} parsed | "
        f"{len(skipped_db)} skipped | "
        f"{len(empty)} empty | "
        f"{len(failed)} failed"
    )

    # Per-card breakdown.
    if ok:
        print("\nBy card:")
        card_totals = {}
        for _, txns in ok:
            for t in txns:
                card = t.get("card", "Unknown")
                card_totals.setdefault(card, {"count": 0, "amount": 0.0})
                card_totals[card]["count"] += 1
                card_totals[card]["amount"] += t["amount"]
        for card, data in sorted(card_totals.items()):
            print(f"  {card:<28} {data['count']:>4} txns   ₹{data['amount']:>12,.0f}")

    # Date range across everything parsed this run.
    all_dates = [t["date"] for _, txns in ok for t in txns if t.get("date")]
    if all_dates:
        print(f"\nDate range: {min(all_dates)} → {max(all_dates)}")

    # Validation skips (empty / permission / non-PDF handled earlier).
    if skipped_files:
        print("\n⚠  Skipped during validation:")
        for s in skipped_files:
            print(f"   {s}")

    # Files that parsed but yielded nothing — likely an unseen layout.
    if empty:
        print("\n⚠  These files parsed but found 0 transactions — check manually:")
        for f, _ in empty:
            print(f"   {Path(f).name}")

    if failed:
        print("\n✗  These files failed to parse:")
        for f, _ in failed:
            print(f"   {Path(f).name}")

    # AI cost estimate / Review Queue hint.
    misc_count = _count_category("Miscellaneous")
    if os.getenv("ANTHROPIC_API_KEY"):
        est_cost = (misc_count / 50) * 0.001
        print(f"\n🤖 AI suggestions: {misc_count} unknown merchants → ~${est_cost:.3f} estimated")
    elif misc_count > 0:
        print(f"\nℹ️  {misc_count} transactions need categorization → Review Queue")
        print("   Optional: add ANTHROPIC_API_KEY to .env for auto-suggestions")

    # Next steps.
    print("\n→  uvicorn backend.main:app --reload")
    print("→  cd frontend && npm run dev")
    print("→  http://localhost:5173\n")


# --------------------------------------------------------------------------- #
# Optional AI pass (opt-in via ANTHROPIC_API_KEY)
# --------------------------------------------------------------------------- #
def run_ai_suggestions():
    """Store Claude category suggestions for rule-uncategorized rows, if enabled."""
    if not ai.is_enabled():
        return
    unknowns = db.low_confidence_transactions(threshold=0.0)
    if not unknowns:
        return
    print(f"\n🤖 Asking Claude to suggest categories for {len(unknowns)} unknown merchants…")
    try:
        categories = list(categorizer.CATEGORY_RULES.keys()) + [categorizer.DEFAULT_CATEGORY]
        suggestions = ai.suggest_for_transactions(unknowns, categories)
        for tid, sug in suggestions.items():
            db.set_suggestion(tid, sug["category"], sug["confidence"])
        print(f"   {len(suggestions)} suggestions stored (confirm them in the Review Queue).")
    except Exception as exc:
        # Never crash on AI failure; never print the key.
        print(f"   AI suggestions unavailable: {exc}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="SpendLens statement parser")
    ap.add_argument("--folder", default=os.environ.get("STATEMENTS_FOLDER", "./data/statements"),
                    help="Folder containing unlocked PDF statements")
    ap.add_argument("--income", type=float, default=None,
                    help="Set monthly income (saved to DB settings)")
    ap.add_argument("--clear", action="store_true",
                    help="Wipe all existing transactions before parsing")
    ap.add_argument("--verbose", action="store_true",
                    help="Enable DEBUG logging (shows which extraction strategy won)")
    ap.add_argument("--workers", type=int, default=None,
                    help="Number of parallel parsing workers (default: min(4, CPUs))")
    args = ap.parse_args()

    # Logging: keep noisy third-party loggers quiet; only our parser goes DEBUG.
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if args.verbose:
        logging.getLogger("spendlens.parser").setLevel(logging.DEBUG)

    # Graceful Ctrl+C.
    signal.signal(signal.SIGINT, handle_interrupt)

    print(f"SpendLens Parser v{VERSION}")
    print("=====================")

    db.init_db()
    if args.income is not None:
        db.set_setting("monthly_income", args.income)
        print(f"Monthly income set to ₹{args.income:,.0f}")
    if args.clear:
        db.clear_all()
        print("Cleared all existing transactions.")

    folder = args.folder
    if not os.path.isdir(folder):
        print(f"✗ Folder not found: {folder}")
        sys.exit(1)

    valid, skipped_files = validate_files(folder)
    if not valid:
        print(f"No readable PDF files found in {folder}")
        if skipped_files:
            for s in skipped_files:
                print(f"   skipped: {s}")
        sys.exit(0)

    # Idempotency: skip files already in the DB unless --clear was passed.
    to_parse, skipped_db = [], []
    for f in valid:
        name = Path(f).name
        if not args.clear and db.source_already_parsed(name):
            print(f"→ {name} already parsed, skipping")
            skipped_db.append(name)
        else:
            to_parse.append(f)

    # Parse (parallel, interruptible).
    results = parse_all(to_parse, args.workers)

    # Insert each file's rows atomically (skip empty / failed).
    total_new = 0
    for f, txns in results:
        if interrupted:
            break
        if not txns:  # None (failed) or [] (empty)
            continue
        total_new += insert_file_atomic(txns)

    # Optional AI suggestions for unknown merchants.
    if not interrupted:
        run_ai_suggestions()

    print_summary(results, skipped_db, skipped_files, total_new)

    # Clean exit (0) even on interrupt — progress was saved.
    sys.exit(0)


if __name__ == "__main__":
    main()
