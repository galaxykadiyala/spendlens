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
    suggested_category TEXT DEFAULT "",
    card_number TEXT DEFAULT "",
    is_credit INTEGER DEFAULT 0,
    excluded INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS category_overrides (pattern TEXT PRIMARY KEY, category TEXT);
CREATE TABLE IF NOT EXISTS reward_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card TEXT NOT NULL,
    statement_month TEXT NOT NULL,
    opening_balance INTEGER DEFAULT 0,
    earned INTEGER DEFAULT 0,
    redeemed INTEGER DEFAULT 0,
    closing_balance INTEGER DEFAULT 0,
    earned_fuel INTEGER DEFAULT 0,
    earned_grocery INTEGER DEFAULT 0,
    earned_upi INTEGER DEFAULT 0,
    earned_other INTEGER DEFAULT 0,
    source_file TEXT,
    parsed_at TEXT,
    UNIQUE(card, statement_month)
);
CREATE TABLE IF NOT EXISTS transaction_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER REFERENCES transactions(id),
    points_earned INTEGER DEFAULT 0,
    card TEXT,
    date TEXT,
    description TEXT,
    amount REAL
);
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
    ("card_number", 'card_number TEXT DEFAULT ""'),
    ("is_credit", "is_credit INTEGER DEFAULT 0"),
    ("excluded", "excluded INTEGER DEFAULT 0"),
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
            "(date, description, raw_description, amount, card, category, source_file, parsed_at, confidence, suggested_category, card_number) "
            "VALUES (:date, :description, :raw_description, :amount, :card, :category, :source_file, :parsed_at, :confidence, :suggested_category, :card_number)",
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
                    "card_number": r.get("card_number", "") or "",
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
                       order_by="date", order_dir="DESC", excluded=None):
    """Flexible transaction query. Returns (rows, total_count).

    `month` is matched as a YYYY-MM prefix on the ISO date string.
    `excluded`: None → no filter (show all); 0 → only active; 1 → only excluded.
    """
    where = []
    params = []
    if excluded is not None:
        where.append("excluded = ?")
        params.append(int(excluded))
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


def all_transactions(include_excluded=False):
    """Return transactions ordered by date (used for aggregates / CSV export).

    Excludes ``excluded = 1`` rows by default so all dashboard aggregates ignore
    them; pass ``include_excluded=True`` for a complete raw dump.
    """
    rows, _ = query_transactions(order_by="date", order_dir="ASC",
                                 excluded=None if include_excluded else 0)
    return rows


def toggle_excluded(txn_id, excluded):
    """Set the excluded flag (bool) on a transaction. Returns rows affected."""
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE transactions SET excluded = ? WHERE id = ?",
            (1 if excluded else 0, txn_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def auto_match_refunds():
    """Exclude refund pairs: a debit and a same-amount credit on the same card
    within 30 days. Marks both ``excluded = 1`` and returns the number of pairs.

    Pairing requires credit rows (``is_credit = 1``). The PDF parsers currently
    skip credit lines, so on a debit-only dataset this finds 0 pairs until
    credits are captured.
    """
    from datetime import datetime

    def _d(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except (TypeError, ValueError):
            return None

    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, date, amount, card, is_credit FROM transactions WHERE excluded = 0"
        ).fetchall()]

        from collections import defaultdict
        groups = defaultdict(lambda: {"debit": [], "credit": []})
        for r in rows:
            key = (r["card"], round(r["amount"] or 0, 2))
            groups[key]["credit" if r["is_credit"] else "debit"].append(r)

        to_exclude = set()
        pairs = 0
        for g in groups.values():
            used_debits = set()
            for cr in g["credit"]:
                cd = _d(cr["date"])
                for db_row in g["debit"]:
                    if db_row["id"] in used_debits:
                        continue
                    dd = _d(db_row["date"])
                    if cd and dd and abs((cd - dd).days) <= 30:
                        used_debits.add(db_row["id"])
                        to_exclude.add(cr["id"])
                        to_exclude.add(db_row["id"])
                        pairs += 1
                        break

        if to_exclude:
            conn.executemany("UPDATE transactions SET excluded = 1 WHERE id = ?",
                             [(i,) for i in to_exclude])
            conn.commit()
        return pairs
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Reward points
# --------------------------------------------------------------------------- #
def insert_reward_summary(card, month, data, source_file=None):
    """Upsert one card+month reward summary. `data` is the rewards dict."""
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO reward_points "
            "(card, statement_month, opening_balance, earned, redeemed, closing_balance, "
            " earned_fuel, earned_grocery, earned_upi, earned_other, source_file, parsed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                card,
                month,
                int(data.get("opening_balance", 0) or 0),
                int(data.get("earned", 0) or 0),
                int(data.get("redeemed", 0) or 0),
                int(data.get("closing_balance", 0) or 0),
                int(data.get("earned_fuel", 0) or 0),
                int(data.get("earned_grocery", 0) or 0),
                int(data.get("earned_upi", 0) or 0),
                int(data.get("earned_other", 0) or 0),
                source_file,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        return 1
    finally:
        conn.close()


def insert_transaction_points(rows):
    """Bulk-insert per-transaction reward points. Each row: {points_earned, card,
    date, description, amount, transaction_id?}. Returns count inserted."""
    if not rows:
        return 0
    conn = connect()
    try:
        conn.executemany(
            "INSERT INTO transaction_points "
            "(transaction_id, points_earned, card, date, description, amount) "
            "VALUES (:transaction_id, :points_earned, :card, :date, :description, :amount)",
            [
                {
                    "transaction_id": r.get("transaction_id"),
                    "points_earned": int(r.get("points_earned", 0) or 0),
                    "card": r.get("card"),
                    "date": r.get("date"),
                    "description": r.get("description"),
                    "amount": float(r.get("amount", 0) or 0),
                }
                for r in rows
            ],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_reward_summary():
    """All reward_points rows ordered by card, then statement_month."""
    conn = connect()
    try:
        cur = conn.execute(
            "SELECT * FROM reward_points ORDER BY card, statement_month"
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_points_by_card():
    """Per-card rollup: [{card, latest_balance, total_earned, months_tracked}]."""
    conn = connect()
    try:
        cur = conn.execute(
            "SELECT card, "
            "       SUM(earned) AS total_earned, "
            "       COUNT(*) AS months_tracked "
            "FROM reward_points GROUP BY card"
        )
        out = []
        for r in cur.fetchall():
            card = r["card"]
            # latest_balance = closing_balance of the most recent statement_month.
            lb = conn.execute(
                "SELECT closing_balance FROM reward_points "
                "WHERE card = ? ORDER BY statement_month DESC LIMIT 1",
                (card,),
            ).fetchone()
            out.append({
                "card": card,
                "latest_balance": int(lb["closing_balance"]) if lb else 0,
                "total_earned": int(r["total_earned"] or 0),
                "months_tracked": int(r["months_tracked"] or 0),
            })
        return out
    finally:
        conn.close()
