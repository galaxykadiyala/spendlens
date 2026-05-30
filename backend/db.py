"""SQLite helpers for SpendLens.

Single source of truth for the schema, connection handling, inserts, queries and
settings. Everything else in the backend talks to the database through this module.
"""

import os
import sqlite3
from datetime import datetime

# Resolve DB path from env, defaulting to ./data/spendlens.db relative to repo root.
_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "spendlens.db"
)
DB_PATH = os.environ.get("DB_PATH", _DEFAULT_DB)

DEFAULT_MONTHLY_INCOME = 218000

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    description TEXT,
    raw_description TEXT,
    amount REAL,
    card TEXT,
    category TEXT,
    source_file TEXT,
    parsed_at TEXT,
    confidence REAL DEFAULT 0.0,
    suggested_category TEXT DEFAULT ""
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS category_overrides (pattern TEXT PRIMARY KEY, category TEXT);
CREATE INDEX IF NOT EXISTS idx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_card ON transactions(card);
CREATE INDEX IF NOT EXISTS idx_category ON transactions(category);
"""


def get_db_path():
    """Return the active DB path (re-reads env so CLI overrides take effect)."""
    return os.environ.get("DB_PATH", DB_PATH)


def connect():
    """Open a connection with row access by column name. Caller closes it."""
    path = get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# Columns added after the initial release — migrated in safely for existing DBs.
# (col_name, column definition appended to ALTER TABLE).
_MIGRATIONS = [
    ("confidence", "confidence REAL DEFAULT 0.0"),
    ("suggested_category", 'suggested_category TEXT DEFAULT ""'),
]


def _existing_columns(conn, table):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cur.fetchall()}


def _migrate(conn):
    """Apply additive column migrations safely (SQLite lacks ALTER ... IF NOT EXISTS).

    We inspect the current columns and only ALTER for ones that are missing, so
    this is a no-op on already-migrated and brand-new databases alike.
    """
    cols = _existing_columns(conn, "transactions")
    for name, ddl in _MIGRATIONS:
        if name not in cols:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {ddl}")


def init_db():
    """Create tables/indexes if missing, migrate older DBs, seed default settings."""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # Seed default monthly income only if not already set.
        cur = conn.execute("SELECT value FROM settings WHERE key = 'monthly_income'")
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('monthly_income', ?)",
                (str(DEFAULT_MONTHLY_INCOME),),
            )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def get_setting(key, default=None):
    conn = connect()
    try:
        cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key, value):
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def get_monthly_income():
    """Return monthly income as a float (falls back to the default)."""
    raw = get_setting("monthly_income", str(DEFAULT_MONTHLY_INCOME))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(DEFAULT_MONTHLY_INCOME)


# --------------------------------------------------------------------------- #
# Category overrides
# --------------------------------------------------------------------------- #
def get_overrides():
    """Return list of (pattern, category) override tuples."""
    conn = connect()
    try:
        cur = conn.execute("SELECT pattern, category FROM category_overrides")
        return [(r["pattern"], r["category"]) for r in cur.fetchall()]
    finally:
        conn.close()


def save_override(pattern, category):
    """Persist a pattern→category override (upsert)."""
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO category_overrides (pattern, category) VALUES (?, ?) "
            "ON CONFLICT(pattern) DO UPDATE SET category = excluded.category",
            (pattern.lower().strip(), category),
        )
        conn.commit()
    finally:
        conn.close()


def apply_override_retroactively(pattern, category):
    """Re-tag existing transactions whose description matches the pattern."""
    conn = connect()
    try:
        like = f"%{pattern.lower().strip()}%"
        # Overrides are user-confirmed, so matched rows become high-confidence
        # and drop out of the review queue.
        cur = conn.execute(
            "UPDATE transactions SET category = ?, confidence = 1.0 "
            "WHERE lower(description) LIKE ? OR lower(raw_description) LIKE ?",
            (category, like, like),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Transactions
# --------------------------------------------------------------------------- #
def source_already_parsed(source_file):
    """True if any rows already exist for this source file."""
    conn = connect()
    try:
        cur = conn.execute(
            "SELECT 1 FROM transactions WHERE source_file = ? LIMIT 1", (source_file,)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def insert_transactions(rows):
    """Bulk-insert transaction dicts. Returns the number inserted.

    Each row dict needs: date, description, raw_description, amount, card,
    category, source_file.
    """
    if not rows:
        return 0
    conn = connect()
    try:
        parsed_at = datetime.now().isoformat(timespec="seconds")
        conn.executemany(
            "INSERT INTO transactions "
            "(date, description, raw_description, amount, card, category, source_file, parsed_at, confidence, suggested_category) "
            "VALUES (:date, :description, :raw_description, :amount, :card, :category, :source_file, :parsed_at, :confidence, :suggested_category)",
            [
                {
                    "date": r.get("date"),
                    "description": r.get("description"),
                    "raw_description": r.get("raw_description", r.get("description")),
                    "amount": float(r.get("amount", 0) or 0),
                    "card": r.get("card"),
                    "category": r.get("category", "Miscellaneous"),
                    "source_file": r.get("source_file"),
                    "parsed_at": parsed_at,
                    "confidence": float(r.get("confidence", 0.0) or 0.0),
                    "suggested_category": r.get("suggested_category", "") or "",
                }
                for r in rows
            ],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def clear_all():
    """Wipe all transactions (used by --clear). Keeps settings/overrides."""
    conn = connect()
    try:
        conn.execute("DELETE FROM transactions")
        conn.commit()
    finally:
        conn.close()


def update_transaction_category(txn_id, category):
    """Set the category for a single transaction. Returns rows affected."""
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE transactions SET category = ? WHERE id = ?", (category, txn_id)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_transaction(txn_id):
    conn = connect()
    try:
        cur = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def confirm_category(txn_id, category):
    """Confirm a category for a transaction: sets category + confidence=1.0.

    Returns rows affected (0 if the id was not found).
    """
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE transactions SET category = ?, confidence = 1.0 WHERE id = ?",
            (category, txn_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def set_suggestion(txn_id, suggested_category, confidence):
    """Store an AI/heuristic suggestion (suggested_category + confidence).

    Does not change the committed `category`; the user confirms via the review
    queue. Returns rows affected.
    """
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE transactions SET suggested_category = ?, confidence = ? WHERE id = ?",
            (suggested_category or "", float(confidence or 0.0), txn_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_review_queue():
    """Transactions needing review: Miscellaneous OR confidence < 0.7.

    Ordered by amount DESC so the highest-impact items surface first.
    """
    conn = connect()
    try:
        cur = conn.execute(
            "SELECT id, date, description, amount, card, category, "
            "suggested_category, confidence "
            "FROM transactions "
            "WHERE category = 'Miscellaneous' OR confidence < 0.7 "
            "ORDER BY amount DESC, id DESC"
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def low_confidence_transactions(threshold=0.0):
    """Return transactions at or below a confidence threshold (default exactly 0.0).

    Used by the AI pass to find rule-uncategorized rows.
    """
    conn = connect()
    try:
        cur = conn.execute(
            "SELECT id, description FROM transactions WHERE confidence <= ?",
            (float(threshold),),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def query_transactions(card=None, category=None, month=None, q=None,
                       min_amount=None, max_amount=None, limit=None, offset=0,
                       order_by="date", order_dir="DESC"):
    """Flexible transaction query. Returns (rows, total_count).

    `month` is matched as a YYYY-MM prefix on the ISO date string.
    """
    where = []
    params = []
    if card:
        where.append("card = ?")
        params.append(card)
    if category:
        where.append("category = ?")
        params.append(category)
    if month:
        where.append("substr(date, 1, 7) = ?")
        params.append(month)
    if q:
        where.append("(lower(description) LIKE ? OR lower(raw_description) LIKE ?)")
        like = f"%{q.lower()}%"
        params.extend([like, like])
    if min_amount is not None:
        where.append("amount >= ?")
        params.append(float(min_amount))
    if max_amount is not None:
        where.append("amount <= ?")
        params.append(float(max_amount))

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # Whitelist order columns to avoid SQL injection.
    allowed_cols = {"date", "amount", "description", "card", "category", "id"}
    if order_by not in allowed_cols:
        order_by = "date"
    order_dir = "DESC" if str(order_dir).upper() != "ASC" else "ASC"

    conn = connect()
    try:
        count_cur = conn.execute(
            f"SELECT COUNT(*) AS c FROM transactions {where_sql}", params
        )
        total = count_cur.fetchone()["c"]

        sql = f"SELECT * FROM transactions {where_sql} ORDER BY {order_by} {order_dir}, id DESC"
        page_params = list(params)
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            page_params.extend([int(limit), int(offset)])
        cur = conn.execute(sql, page_params)
        rows = [dict(r) for r in cur.fetchall()]
        return rows, total
    finally:
        conn.close()


def all_transactions():
    """Return every transaction ordered by date (used for CSV export/aggregates)."""
    rows, _ = query_transactions(order_by="date", order_dir="ASC")
    return rows
