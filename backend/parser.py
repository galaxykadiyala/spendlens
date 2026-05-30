"""PDF statement parser for SpendLens — hardened edition.

All PDFs are assumed to be unlocked/unencrypted — no decryption is attempted.
For every bank we try two extraction strategies in order:

  Strategy A — pdfplumber table extraction (``page.extract_tables()``)
  Strategy B — raw text, line-by-line with regex (fallback if A finds nothing)

Every parser iterates **all** pages, runs inside a ``with pdfplumber.open(...)``
context manager (the pdf object is never stored outside the block), skips bad
rows instead of crashing, and de-duplicates before returning.

Public surface used by the rest of the app:
  - ``detect_and_parse(pdf_path)`` → list of enriched transaction dicts
  - ``parse_file(pdf_path)``       → {"ok", "card", "transactions", "error"}
  - ``detect_bank(pdf_path)``      → card name string ("" if unrecognized)
  - ``test_parser(pdf_path)``      → human-readable debug dump (no DB writes)
"""

import os
import re
import logging
from datetime import datetime

import pdfplumber

try:
    from backend import categorizer
except ImportError:  # running from inside backend/
    import categorizer

logger = logging.getLogger("spendlens.parser")

# Suppress noisy "Could not get FontBBox" warnings from the PDF backends without
# hiding real errors.
logging.getLogger("pdfplumber").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #
# Date patterns: 20 Dec 25, 20/12/2025, 20-12-2025, 05FEB, 20DEC25
DATE_PATTERN = r'(\d{1,2}[\s/-]?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[\s/-]?\d{0,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
# Amount pattern: 4,259.68 or 4,259.68 DR or 35.00DR
AMOUNT_PATTERN = r'([\d,]+\.?\d*)\s*(DR|CR)?'

_DATE_RE = re.compile(DATE_PATTERN, re.IGNORECASE)
# For locating the transaction amount we require a decimal part, so reference
# numbers / dates are not mistaken for amounts.
_AMOUNT_TOKEN = re.compile(r'([\d,]+\.\d{1,2})\s*(DR|CR)?', re.IGNORECASE)

# Rows that are never spends (payments, balances, summaries, reward lines).
SKIP_KEYWORDS = [
    "payment received", "payment recieved", "payment - thank", "payment thank",
    "opening balance", "closing balance", "total amount due", "minimum amount due",
    "reward points", "previous balance", "available credit", "credit limit",
    "statement summary", "amount due", "finance charge summary",
]

# Bank detection markers, checked across the first 3 pages (upper-cased text).
DETECTION = {
    "HDFC Diners Black": ["DINERS BLACK CREDIT CARD", "DINERS BLACK"],
    "HDFC Swiggy":       ["SWIGGY HDFC BANK CREDIT CARD"],
    "HSBC Live+":        ["HSBC LIVE+ CREDIT CARD", "HSBC LIVE+"],
    # IDFC Select is checked before Power+ so its specific marker wins; Power+
    # carries the generic IDFC markers and acts as the default IDFC card.
    "IDFC Select":       ["FIRST SELECT CREDIT CARD", "FIRST SELECT"],
    "IDFC Power+":       ["FIRST POWER+", "FIRST POWER PLUS", "POWER PLUS CREDIT CARD",
                          "XX9359", "9359", "IDFCFIRSTBANK", "IDFCFIRST"],
    "RBL IndianOil":     ["INDIANOIL RBL", "RBL BANK", "XTRA CREDIT"],
    "Amazon ICICI":      ["AMAZON PAY ICICI", "AMAZON PAY ICICI BANK",
                          "AMAZON PAY CREDIT CARD", "AMAZONPAYCC@ICICIBANK"],
    "IndusInd":          ["INDUSIND BANK CREDIT CARD", "INDUSIND BANK"],
    "Axis Amex":         ["AXIS BANK AMEX", "AXIS BANK"],
    "Standard Chartered Ultimate": ["STANDARD CHARTERED", "STANCHART",
                                    "ULTIMATE MASTERCARD", "SC.COM/IN"],
}


# --------------------------------------------------------------------------- #
# Core helpers
# --------------------------------------------------------------------------- #
def clean_description(text):
    """Collapse whitespace and trim."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def parse_amount(raw):
    """Parse an amount string. Returns ``(amount: float, is_credit: bool)``.

    Returns ``(0.0, False)`` on failure so the caller simply skips the row.
    Handles ``4,259.68``, ``4,259.68 DR``, ``35.00DR``, ``₹ 1,200.00 CR`` etc.
    """
    raw = (raw or "").strip()
    up = raw.upper()
    is_credit = up.endswith("CR") or up.endswith(" CR")
    cleaned = re.sub(r"[^\d.]", "", up.replace("DR", "").replace("CR", "").replace(",", ""))
    if not cleaned:
        return 0.0, False
    try:
        return float(cleaned), is_credit
    except ValueError:
        return 0.0, False


def parse_date(raw, statement_year=None):
    """Parse a date string to ISO ``YYYY-MM-DD``. Returns "" on failure.

    Tries the common Indian-statement formats, then the compact ``DDMON`` /
    ``DDMONYY`` forms (e.g. ``05FEB``, ``20DEC25``).
    """
    raw = (raw or "").strip()
    if not raw:
        return ""

    formats = [
        "%d %b %y",   # 20 Dec 25
        "%d %b %Y",   # 20 Dec 2025
        "%d/%m/%Y",   # 20/12/2025
        "%d-%m-%Y",   # 20-12-2025
        "%d/%m/%y",   # 20/12/25
        "%d-%m-%y",   # 20-12-25
        "%d%b%Y",     # 20Dec2025
        "%d%b%y",     # 20Dec25
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Handle DDMON or DDMONYY (e.g. "05FEB", "20DEC25"), with optional separators.
    match = re.match(r"(\d{1,2})[\s/-]?([A-Za-z]{3})[\s/-]?(\d{2,4})?", raw)
    if match:
        day, mon, yr = match.groups()
        yr = yr or str(statement_year or datetime.now().year)
        yr = ("20" + yr) if len(yr) == 2 else yr
        try:
            return datetime.strptime(f"{day} {mon} {yr}", "%d %b %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass

    return ""  # caller skips this row


def clean_idfc_description(raw):
    """Clean an IDFC description.

    Only UPI strings (starting with ``UPICC/`` or ``UPI/``) are split on ``/``;
    the 4th segment is the merchant. Non-UPI descriptions (e.g.
    ``SRI SATHYA SAI FUELS``, ``HindustanPetroleumCor``) are kept as-is, trimmed.
    """
    raw = (raw or "").strip()
    if raw.upper().startswith(("UPICC/", "UPI/")):
        parts = raw.split("/")
        # parts[3] is the merchant, parts[4] is the bank — use parts[3].
        merchant = parts[3] if len(parts) > 3 else raw
        # Remove UPI reference numbers (runs of 6+ digits).
        return re.sub(r"\b\d{6,}\b", "", merchant).strip()
    return raw


def dedup(transactions):
    """Drop exact duplicate rows keyed on (date, description, amount)."""
    seen = set()
    result = []
    for t in transactions:
        key = (t["date"], t["description"], t["amount"])
        if key not in seen:
            seen.add(key)
            result.append(t)
    return result


def should_skip(text):
    low = (text or "").lower()
    return any(k in low for k in SKIP_KEYWORDS)


def _statement_year_from_text(text):
    """Find a plausible 4-digit year to anchor year-less dates (e.g. 05FEB)."""
    m = re.search(r"\b(20\d{2})\b", text or "")
    return int(m.group(1)) if m else None


def _find_amount(text):
    """Return ``(amount, is_credit, span)`` for the LAST decimal amount in text.

    Returns ``(0.0, False, None)`` when no amount is present. Taking the last
    amount matches the typical ``<date> <description> <amount>`` row layout.
    """
    last = None
    for m in _AMOUNT_TOKEN.finditer(text or ""):
        last = m
    if not last:
        return 0.0, False, None
    amount, is_credit = parse_amount(last.group(0))
    return amount, is_credit, last.span()


# --------------------------------------------------------------------------- #
# Extraction strategies
# --------------------------------------------------------------------------- #
def _row_from_text(line, statement_year, clean_desc):
    """Build a transaction dict from a single text line, or None to skip it."""
    if should_skip(line):
        return None
    dm = _DATE_RE.search(line)
    if not dm:
        return None
    date = parse_date(dm.group(0), statement_year)
    if not date:
        return None

    amount, is_credit, span = _find_amount(line)
    if span is None or amount == 0.0 or is_credit:
        return None

    # Description is whatever sits between the date and the amount.
    if span[0] >= dm.end():
        desc = line[dm.end():span[0]]
    else:
        desc = (line[:dm.start()] + " " + line[span[1]:])
    desc = clean_desc(desc.strip(" -|\t"))
    if not desc or "payment" in desc.lower():
        return None
    return {"date": date, "description": desc, "amount": amount}


def _row_from_cells(cells, statement_year, clean_desc):
    """Build a transaction dict from one extracted table row, or None to skip."""
    text_cells = [str(c).strip() for c in cells if c is not None and str(c).strip()]
    if not text_cells:
        return None
    joined = " ".join(text_cells)
    if should_skip(joined):
        return None

    # Locate the date cell.
    date, date_idx = "", -1
    for i, c in enumerate(text_cells):
        dm = _DATE_RE.search(c)
        if dm:
            d = parse_date(dm.group(0), statement_year)
            if d:
                date, date_idx = d, i
                break
    if not date:
        return None

    # Locate the amount — scan right-to-left so the rightmost amount wins.
    amount, is_credit, amt_idx = 0.0, False, -1
    for i in range(len(text_cells) - 1, -1, -1):
        a, cr, span = _find_amount(text_cells[i])
        if span is not None and a > 0.0:
            amount, is_credit, amt_idx = a, cr, i
            break
    if amount == 0.0 or is_credit:
        return None

    # Description = the most alphabetic cell that isn't the date or amount cell.
    desc, best = "", -1
    for i, c in enumerate(text_cells):
        if i in (date_idx, amt_idx):
            continue
        alpha = sum(ch.isalpha() for ch in c)
        if alpha > best:
            best, desc = alpha, c
    desc = clean_desc(desc.strip())
    if not desc or "payment" in desc.lower():
        return None
    return {"date": date, "description": desc, "amount": amount}


def _parse_bank(pdf, card, statement_year, clean_desc):
    """Run the dual strategy over an open pdf and return deduped raw rows.

    `clean_desc` is the bank-specific description cleaner. The pdf object is
    owned by the caller's ``with`` block; this function only reads from it.
    """
    # Strategy A — table extraction across all pages.
    rows = []
    for page in pdf.pages:
        try:
            tables = page.extract_tables() or []
        except Exception as exc:  # never crash on one bad page
            logger.debug("%s: table extraction failed on a page: %s", card, exc)
            tables = []
        for table in tables:
            for cells in table:
                try:
                    r = _row_from_cells(cells, statement_year, clean_desc)
                except Exception:
                    r = None  # bad row → skip, never crash
                if r:
                    rows.append(r)
    strategy = "table"

    # Strategy B — raw text fallback if tables yielded nothing.
    if not rows:
        for page in pdf.pages:
            try:
                txt = page.extract_text() or ""
            except Exception as exc:
                logger.debug("%s: text extraction failed on a page: %s", card, exc)
                txt = ""
            for line in txt.splitlines():
                if not line.strip():
                    continue
                try:
                    r = _row_from_text(line, statement_year, clean_desc)
                except Exception:
                    r = None
                if r:
                    rows.append(r)
        strategy = "text"

    logger.debug("%s: strategy '%s' extracted %d transactions", card, strategy, len(rows))
    return dedup(rows)


# --------------------------------------------------------------------------- #
# Per-bank parsers — each iterates all pages (via _parse_bank) and dedups.
# --------------------------------------------------------------------------- #
def parse_hdfc(pdf, card, statement_year):
    return _parse_bank(pdf, card, statement_year, clean_description)


def parse_hsbc(pdf, card, statement_year):
    return _parse_bank(pdf, card, statement_year, clean_description)


# Header/summary/credit keywords that must never be treated as IDFC spends.
_IDFC_SKIP_KEYWORDS = [
    "billdesk", "payment/dp", "opening balance", "closing balance",
    "total amount", "minimum amount", "payments & other credits",
    "purchases, emis", "reward", "emi", "available credit",
    "statement period", "joining fee", "annual fee", "igst assessment",
    "interest rate", "congratulations", "please note", "card number",
    "relationship no", "transaction date", "transaction details",
    "share your credit", "refer this", "convert your", "apply now",
    "special benefits", "important information", "insurance details",
    "schedule of charges", "grievance", "your card information",
    "payment modes", "pay via", "pay through", "pay now",
    "r1,", "r0.", "r13,", "r28,",  # summary amounts with rupee symbol
]

# A transaction date at the start of a line ("20 Dec 25" or "20/12/2025").
_IDFC_DATE_RE = re.compile(
    r'^(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{2,4}'
    r'|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
    re.IGNORECASE,
)
# An amount immediately followed by DR (debit) at the end of a line.
_IDFC_AMOUNT_DR_RE = re.compile(r'([\d,]+\.\d{2})\s+DR\s*$', re.IGNORECASE)
# An amount followed by DR or CR at the end of a line (any settled amount).
_IDFC_AMOUNT_ANY_RE = re.compile(r'([\d,]+\.\d{2})\s+(?:DR|CR)\s*$', re.IGNORECASE)
# A credit (CR) amount at the end of a line — skipped (payments, refunds, fees).
_IDFC_AMOUNT_CR_RE = re.compile(r'([\d,]+\.\d{2})\s+CR\s*$', re.IGNORECASE)


def _parse_idfc_lines(text, card_name, statement_year=None):
    """Parse IDFC statement text, reconstructing transactions split across lines.

    IDFC's extracted text wraps long UPI descriptions across 2-3 physical lines,
    e.g. the date + amount land on their own line while the merchant string is
    split before and after it::

        UPICC/DR/572105501400/MEDPLUS/HD
        21 Dec 25 159.00 DR
        FC/medplus/Paid v

    The anchor is the line carrying both a leading date and a trailing
    ``amount DR``. When that anchor has no text between the date and the amount,
    its description is rebuilt from the buffered fragment(s) before it plus the
    continuation fragment(s) after it. Single-line rows
    (``20 Dec 25 SBT FILLING STATION Convert 4,259.68 DR``) are handled directly.
    Credits (CR) and header/summary lines are skipped.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    transactions = []
    pending = []  # buffered description fragments seen before the current anchor

    def _is_skip(s):
        low = s.lower()
        return any(kw in low for kw in _IDFC_SKIP_KEYWORDS)

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # Header / footer / summary line — drop any buffered fragments at the boundary.
        if _is_skip(line):
            pending = []
            i += 1
            continue

        # Credit line (payment, refund, fee waiver) — skip and reset fragments.
        if _IDFC_AMOUNT_CR_RE.search(line):
            pending = []
            i += 1
            continue

        dr = _IDFC_AMOUNT_DR_RE.search(line)
        if dr:
            remainder = line[:dr.start()].strip()
            date_m = _IDFC_DATE_RE.match(remainder)
            if not date_m:
                # Amount+DR with no leading date — not a usable row on its own.
                pending = []
                i += 1
                continue

            middle = remainder[date_m.end():].strip()
            middle = re.sub(r'\s+Convert\s*$', '', middle, flags=re.IGNORECASE).strip()

            raw_lines = list(pending)
            consumed_to = i
            if middle:
                # Self-contained row; prepend any buffered fragments (wrapped name).
                desc_parts = pending + [middle]
            else:
                # Split row: gather continuation fragments that follow the anchor.
                post = []
                j = i + 1
                while j < n:
                    nxt = lines[j]
                    if _is_skip(nxt) or _IDFC_DATE_RE.match(nxt) or _IDFC_AMOUNT_ANY_RE.search(nxt):
                        break
                    post.append(nxt)
                    j += 1
                desc_parts = pending + post
                raw_lines = pending + [line] + post
                consumed_to = j - 1

            amount = float(dr.group(1).replace(",", ""))
            date = parse_date(date_m.group(1), statement_year=statement_year)
            desc = clean_idfc_description(" ".join(p for p in desc_parts if p).strip())

            pending = []
            i = consumed_to + 1

            if amount <= 0 or not date or not desc:
                continue
            # Belt-and-braces: never let a payment/billdesk row through.
            if any(kw in desc.lower() for kw in ("billdesk", "payment/dp")):
                continue

            transactions.append({
                "date": date,
                "description": desc,
                "raw_description": " ".join(raw_lines) if raw_lines else line,
                "amount": amount,
                "card": card_name,
            })
            continue

        # A bare date line (no amount yet) or a plain text fragment: buffer it as
        # a description fragment for the next anchor.
        pending.append(line)
        i += 1

    return transactions


def parse_idfc(pdf, card, statement_year):
    """IDFC (Power+/Select): table extraction first, dedicated line parser as fallback."""
    # Strategy A — table extraction across all pages.
    rows = []
    for page in pdf.pages:
        try:
            tables = page.extract_tables() or []
        except Exception as exc:
            logger.debug("%s: table extraction failed on a page: %s", card, exc)
            tables = []
        for table in tables:
            for cells in table:
                try:
                    r = _row_from_cells(cells, statement_year, clean_idfc_description)
                except Exception:
                    r = None
                if r:
                    rows.append(r)
    strategy = "table"

    # Strategy B — dedicated IDFC line parser over all pages' raw text.
    if not rows:
        text = ""
        for page in pdf.pages:
            try:
                text += (page.extract_text() or "") + "\n"
            except Exception as exc:
                logger.debug("%s: text extraction failed on a page: %s", card, exc)
        rows = _parse_idfc_lines(text, card, statement_year)
        strategy = "text"

    logger.debug("%s: strategy '%s' extracted %d transactions", card, strategy, len(rows))
    return dedup(rows)


def parse_rbl(pdf, card, statement_year):
    return _parse_bank(pdf, card, statement_year, clean_description)


# Header/footer/payment lines that are never spends in a SC statement.
_SC_SKIP_KEYWORDS = [
    "rewards points summary", "previous balance", "payments/credits",
    "total payment due", "minimum payment due", "statement date",
    "statement period", "payment due date", "credit limit", "cash limit",
    "bill desk payment", "cidnum",  # payment reference
    "making only the minimum", "disclaimer", "most important terms",
    "date description",  # header row
]

# Merchant-prefix noise to strip from SC descriptions (longest/most specific first).
_SC_PREFIXES = ["PAY*", "RAZ*", "CAS*", "PHP*", "IAP ", "WWW "]

# A SC transaction line: 6-digit date + description + trailing INR amount.
_SC_ROW_RE = re.compile(r'^(\d{6})\s+(.+?)\s+([\d,]+\.\d{2})\s*$')


def _parse_stanchart_date(raw):
    """Parse SC's DDMMYY format, e.g. ``230326`` → ``2026-03-23``. "" on failure."""
    raw = (raw or "").strip()
    if re.match(r'^\d{6}$', raw):
        day, mon, yr = raw[0:2], raw[2:4], raw[4:6]
        try:
            return datetime.strptime(f"{day}/{mon}/20{yr}", "%d/%m/%Y").strftime("%Y-%m-%d")
        except ValueError:
            return ""
    return ""


def _clean_stanchart_desc(raw):
    """Trim a SC description: drop trailing city, then a known merchant prefix."""
    raw = (raw or "").strip()
    # Remove a short city suffix after the last comma (e.g. ", Bangalore").
    parts = raw.rsplit(",", 1)
    if len(parts) == 2 and len(parts[1].strip()) < 20:
        raw = parts[0].strip()
    # Remove a known aggregator/prefix (PAY*, RAZ*, CAS*, PHP*, IAP , WWW ).
    for prefix in _SC_PREFIXES:
        if raw.upper().startswith(prefix.upper()):
            raw = raw[len(prefix):].strip()
            break
    return raw.strip()


def parse_stanchart(pdf, card, statement_year):
    """Standard Chartered Ultimate. Text/line based; INR (last) amount; skips CR.

    Stops at the "Rewards Points Summary" section. Uses the open pdf object and
    iterates all pages; never opens a new handle.
    """
    transactions = []
    stop_parsing = False

    for page in pdf.pages:
        if stop_parsing:
            break
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.debug("%s: text extraction failed on a page: %s", card, exc)
            continue
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        for line in lines:
            line_lower = line.lower()

            # Everything from the rewards summary onward is not transactions.
            if "rewards points summary" in line_lower:
                stop_parsing = True
                break

            if any(kw in line_lower for kw in _SC_SKIP_KEYWORDS):
                continue

            # Skip credits / payments / cashbacks (amount ends in CR).
            if re.search(r'\bCR\s*$', line):
                continue

            m = _SC_ROW_RE.match(line)
            if not m:
                continue
            date_str, desc_raw, amount_str = m.groups()

            date = _parse_stanchart_date(date_str)
            if not date:
                continue

            # Strip the trailing columns that sit between description and INR amount:
            # transaction reference (long digit run), then rewards earned + type.
            desc_raw = re.sub(r'\s+\d{20,}\s+', ' ', desc_raw)
            desc_raw = re.sub(r'\s+\d{1,4}\s+\d{3}\s*$', '', desc_raw)
            desc_raw = re.sub(r'\s+\d{3}\s*$', '', desc_raw).strip()

            desc = _clean_stanchart_desc(desc_raw)
            if not desc or re.match(r'^\d+$', desc):
                continue

            amount, is_credit = parse_amount(amount_str)
            if is_credit or amount <= 0:
                continue

            # Belt-and-braces: drop known non-spend rows by description.
            if any(kw in desc.lower() for kw in ("bill desk", "cidnum", "fuel surcharge reversal")):
                continue

            transactions.append({
                "date": date,
                "description": desc,
                "raw_description": line,
                "amount": amount,
                "card": card,
            })

    return dedup(transactions)


# --------------------------------------------------------------------------- #
# ICICI Amazon Pay — line-based parser (multi-line descriptions, extra columns).
# --------------------------------------------------------------------------- #
# Date + SerNo (11-13 digits) anywhere on the line — some rows carry a stray
# leading token (e.g. "100% ") before the date, so this is not anchored at ^.
_ICICI_DATE_RE = re.compile(r'(\d{2}/\d{2}/\d{4})\s+(\d{10,13})\s*(.*)')
_ICICI_AMOUNT_RE = re.compile(r'([\d,]+\.\d{2})\s*(CR)?\s*$', re.IGNORECASE)
_ICICI_SKIP = [
    "payment received", "payment recieved", "bbps payment",
    "opening balance", "closing balance", "reward", "credit limit",
    "available credit", "statement", "minimum amount", "total amount",
    "international spends", "credit summary", "earnings",
]


def _icici_strip_columns(before_amt):
    """Drop trailing reward-points / international-amount numeric columns.

    Rows read ``<merchant> <reward-pts> <amount>``; this removes the numeric
    column(s) between the merchant name and the INR amount.
    """
    return re.sub(r'(?:\s+\d[\d,]*\.?\d*)+\s*$', '', (before_amt or "")).strip()


def parse_icici(pdf, card, statement_year):
    """ICICI Amazon Pay: line-based parser handling multi-line descriptions.

    ICICI table layout: Date | SerNo(11-13 digits) | Description (wraps to
    next line with 'IN' suffix noise) | Reward Pts | Intl Amt | Amount
    Credits end with ' CR' and are skipped. Debits have no suffix.
    """
    transactions = []
    full_lines = []
    for page in pdf.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.debug("%s: text extraction failed: %s", card, exc)
            continue
        full_lines.extend(text.splitlines())

    i = 0
    while i < len(full_lines):
        line = full_lines[i].strip()
        m = _ICICI_DATE_RE.search(line)
        if not m:
            i += 1
            continue

        date_str, ser_no, rest = m.groups()
        date = parse_date(date_str, statement_year)
        if not date:
            i += 1
            continue

        desc_parts = [rest.strip()] if rest.strip() else []
        amount = 0.0
        is_credit = False

        amt_m = _ICICI_AMOUNT_RE.search(rest)
        if amt_m:
            amount, is_credit = parse_amount(amt_m.group(0))
            desc_parts = [_icici_strip_columns(rest[:amt_m.start()])]
        else:
            j = i + 1
            while j < len(full_lines):
                nxt = full_lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                if _ICICI_DATE_RE.search(nxt):
                    break
                amt_m = _ICICI_AMOUNT_RE.search(nxt)
                if amt_m:
                    amount, is_credit = parse_amount(amt_m.group(0))
                    before_amt = _icici_strip_columns(nxt[:amt_m.start()])
                    if before_amt and not re.match(
                        r'^(IN|#.*|\d[\d,]*\.?\d*)$', before_amt, re.IGNORECASE
                    ):
                        desc_parts.append(before_amt)
                    i = j
                    break
                if re.match(
                    r'^(IN|#\s*International Spends|\d[\d,]*\.?\d*)$',
                    nxt, re.IGNORECASE
                ):
                    j += 1
                    continue
                desc_parts.append(nxt)
                j += 1

        desc = clean_description(" ".join(p for p in desc_parts if p))

        if is_credit or amount <= 0:
            i += 1
            continue
        if any(kw in desc.lower() for kw in _ICICI_SKIP):
            i += 1
            continue
        if date and desc:
            transactions.append({
                "date": date,
                "description": desc,
                "raw_description": line,
                "amount": amount,
                "card": card,
            })
        i += 1

    return dedup(transactions)


def parse_generic(pdf, card, statement_year):
    return _parse_bank(pdf, card, statement_year, clean_description)


CARD_PARSER = {
    "HDFC Diners Black": parse_hdfc,
    "HDFC Swiggy": parse_hdfc,
    "HSBC Live+": parse_hsbc,
    "IDFC Power+": parse_idfc,
    "IDFC Select": parse_idfc,
    "RBL IndianOil": parse_rbl,
    "Amazon ICICI": parse_icici,
    "IndusInd": parse_generic,
    "Axis Amex": parse_generic,
    "Standard Chartered Ultimate": parse_stanchart,
}


# --------------------------------------------------------------------------- #
# Detection + dispatch
# --------------------------------------------------------------------------- #
def detect_bank(pdf_path):
    """Detect the card from the first 3 pages. Returns "" if unrecognized."""
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:3]:  # check first 3 pages
            text += (page.extract_text() or "")
    text_upper = text.upper()
    for card_name, markers in DETECTION.items():
        if any(m in text_upper for m in markers):
            return card_name
    return ""


def detect_and_parse(pdf_path):
    """Detect the bank and return a list of enriched transaction dicts.

    Each dict: date, description, raw_description, amount, card, category,
    confidence, suggested_category, source_file. Returns ``[]`` if the bank is
    unrecognized or nothing parsed. Uses context managers throughout.
    """
    card = detect_bank(pdf_path)
    if not card:
        logger.error("Bank format not recognized: %s", os.path.basename(pdf_path))
        return []

    with pdfplumber.open(pdf_path) as pdf:
        text = ""
        for page in pdf.pages[:3]:
            text += (page.extract_text() or "")
        statement_year = _statement_year_from_text(text)
        parser_fn = CARD_PARSER.get(card, parse_generic)
        raw_rows = parser_fn(pdf, card, statement_year)
    # pdf is closed here.

    source_file = os.path.basename(pdf_path)
    txns = []
    for r in raw_rows:
        desc = r["description"]
        cat = categorizer.categorize(desc)
        txns.append({
            "date": r["date"],
            "description": desc,
            "raw_description": r.get("raw_description", desc),
            "amount": r["amount"],
            "card": card,
            "category": cat["category"],
            "confidence": cat["confidence"],
            "suggested_category": "",
            "source_file": source_file,
        })
    return txns


def parse_file(pdf_path):
    """Parse one PDF into the {"ok", "card", "transactions", "error"} envelope.

    Never raises — a bad file is reported via the dict so a batch keeps going.
    Used by the FastAPI ``/api/parse`` route.
    """
    source_file = os.path.basename(pdf_path)
    result = {"ok": False, "card": None, "transactions": [], "error": None}
    try:
        txns = detect_and_parse(pdf_path)
        result["transactions"] = txns
        result["card"] = txns[0]["card"] if txns else (detect_bank(pdf_path) or None)
        result["ok"] = True
    except Exception as exc:  # never crash the batch on one file
        result["error"] = str(exc)
        logger.error("Failed to parse %s: %s", source_file, exc)
    return result


# --------------------------------------------------------------------------- #
# Debug helper
# --------------------------------------------------------------------------- #
def test_parser(pdf_path):
    """Debug a single PDF without touching the database.

    Usage: python -c "from backend.parser import test_parser; test_parser('file.pdf')"
    """
    transactions = detect_and_parse(pdf_path)
    if not transactions:
        print(f"❌ No transactions extracted from {pdf_path}")
        return
    print(f"✅ {len(transactions)} transactions extracted")
    print(f"   Card:       {transactions[0].get('card', 'unknown')}")
    print(f"   Date range: {transactions[0]['date']} → {transactions[-1]['date']}")
    print("\nFirst 5:")
    for t in transactions[:5]:
        print(f"  {t['date']}  {t['description'][:45]:<45}  ₹{t['amount']:>10,.2f}")
    print("\nLast 3:")
    for t in transactions[-3:]:
        print(f"  {t['date']}  {t['description'][:45]:<45}  ₹{t['amount']:>10,.2f}")
    cats = {}
    for t in transactions:
        cats[t.get("category", "?")] = cats.get(t.get("category", "?"), 0) + t["amount"]
    print("\nCategories:")
    for cat, total in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat:<25} ₹{total:>10,.2f}")
