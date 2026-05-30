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
                          "XX9359", "IDFCFIRSTBANK", "IDFCFIRST"],
    # Plain RBL card checked before the IndianOil co-brand (more specific marker).
    "RBL Bank":          ["RBL BANK CREDIT CARD"],
    "RBL IndianOil":     ["INDIANOIL RBL", "XTRA CREDIT"],
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
# HDFC Diners Black / Swiggy. Rows look like:
#   14/04/2026| 07:42 WWW MYNTRA COMGURGAON + 55 C 1,785.00 l
# The ₹ glyph extracts as 'C'. The REWARDS column ("+ 55" / "- 55") sits just
# before the amount. A '+' IMMEDIATELY before the amount (e.g. "+ C 14,000.00")
# marks a CREDIT — payment, refund, earned cashback, or EMI reversal — and is
# skipped. International rows show "<FCY> <amt> ... C <INR amt>"; requiring the
# 'C' glyph means we always capture the INR amount.
_HDFC_DATE_RE = re.compile(r'^(\d{2}/\d{2}/\d{4})\s*\|?\s*(?:\d{1,2}:\d{2})?\s*(.*)$')
_HDFC_AMOUNT_RE = re.compile(r'([+\-])?\s*[C₹]\s*([\d,]+\.\d{2})(?:\s+l)?\s*$', re.IGNORECASE)
# Once any of these summary sections begins, no more transactions follow — their
# dates/amounts (loan rows, reward tables) must not be parsed as spends.
_HDFC_STOP = [
    "rewards program points summary", "smart emi loan summary",
    "merchant emi loan summary", "cash back summary", "gst summary",
]
# Defensive description-level skips (the '+' credit rule already catches these).
_HDFC_SKIP = ["cc payment", "card payment"]


def parse_hdfc(pdf, card, statement_year):
    """HDFC Diners Black / Swiggy: line-based, ₹-glyph ('C') amounts.

    Skips credits (a '+' before the amount), stops before the rewards/EMI loan
    summaries, and strips the time, rewards column and trailing PI bullet from
    descriptions.
    """
    rows = []
    stop = False
    for page in pdf.pages:
        if stop:
            break
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.debug("%s: text extraction failed: %s", card, exc)
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            low = s.lower()
            if any(k in low for k in _HDFC_STOP):
                stop = True
                break

            m = _HDFC_DATE_RE.match(s)
            if not m:
                continue
            date = parse_date(m.group(1), statement_year)
            if not date:
                continue

            rest = m.group(2)
            am = _HDFC_AMOUNT_RE.search(rest)
            if not am:
                continue
            sign, amount_str = am.group(1), am.group(2)
            if sign == "+":
                continue  # credit: payment / refund / cashback / EMI reversal
            amount = float(amount_str.replace(",", ""))
            if amount <= 0:
                continue

            before = rest[:am.start()].strip()
            before = re.sub(r'\s*[+\-]\s*\d+\s*$', '', before)          # rewards column
            before = re.sub(r'\s+[A-Z]{3}\s+[\d,]+\.\d{2}\s*$', '', before)  # FCY amount
            before = re.sub(r'\s+\d{6,}\s*$', '', before)               # trailing ref
            before = re.sub(r'^EMI\s+', '', before, flags=re.IGNORECASE)  # EMI badge
            desc = clean_description(before)
            if not desc:
                continue
            if any(k in desc.lower() for k in _HDFC_SKIP):
                continue

            rows.append({
                "date": date,
                "description": desc,
                "raw_description": s,
                "amount": amount,
                "card": card,
            })
    return dedup(rows)


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


# RBL IndianOil XTRA. Transactions are listed under "THE MONTH GONE BY!" with
# columns: Date | Description | Amount. Dates are "DD Mon YYYY"; amounts have no
# DR/CR marker, so credits are identified by description keyword. We scope strictly
# to that section so the page-2 fees/illustration tables (2018-2019 sample rows)
# are never parsed as real transactions.
# RBL's two-column layout concatenates the transaction onto the account-summary
# line, so the date sits mid-line, e.g.
#   "Total Amount Due PAY NOW 4,184.00 3 May 2026 SREE KODANDARAMA SERV ... 4,184.12"
# We search for "DD Mon YYYY <desc> <amount>" with the amount anchored at the end
# (the trailing INR amount), ignoring any summary figure before the date.
_RBL_TXN_RE = re.compile(r'(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+(.+?)\s+([\d,]+\.\d{2})\s*$')
_RBL_START = "the month gone by"
_RBL_STOP = ("eligible for emi", "your spending pattern", "look out for exclusive",
             "reward summary")
_RBL_SKIP = ["payment received", "payment", "reversal", "refund",
             "goods & service tax", "goods and service tax"]


def parse_rbl(pdf, card, statement_year):
    """RBL IndianOil XTRA: parse only the 'THE MONTH GONE BY!' table.

    Plain amounts (no DR/CR); credits are recognised by keyword. Zero-amount
    and payment/credit rows are skipped.
    """
    rows = []
    in_txn = False
    for page in pdf.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.debug("%s: text extraction failed: %s", card, exc)
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            low = s.lower()
            if _RBL_START in low:
                in_txn = True
                continue
            if not in_txn:
                continue
            if any(k in low for k in _RBL_STOP):
                in_txn = False
                continue
            if low.startswith("date") and "description" in low:
                continue  # column header
            m = _RBL_TXN_RE.search(s)
            if not m:
                continue
            date = parse_date(m.group(1), statement_year)
            if not date:
                continue
            amount = float(m.group(3).replace(",", ""))
            if amount <= 0:
                continue
            desc = clean_description(m.group(2))
            if not desc or any(k in desc.lower() for k in _RBL_SKIP):
                continue
            rows.append({
                "date": date,
                "description": desc,
                "raw_description": s,
                "amount": amount,
                "card": card,
            })
    return dedup(rows)


# --------------------------------------------------------------------------- #
# IndusInd CRED RuPay. Table: Date | Details | Merchant Category | CRED Points |
# Amount, with explicit DR/CR. Long UPI rows wrap the merchant-category across
# 2-3 physical lines (amount on its own line). We scope to the "Purchases & Cash
# Transactions" / "Payment Details" sections so the page-1 summary block can't
# fabricate transactions, and reassemble wrapped rows via a line buffer.
# --------------------------------------------------------------------------- #
_INDUS_DATE_RE = re.compile(r'^(\d{2}/\d{2}/\d{4})')
_INDUS_TERM_RE = re.compile(r'(\d{1,6})\s+([\d,]+\.\d{2})\s+(DR|CR)\s*$', re.IGNORECASE)
_INDUS_CATEGORIES = sorted([
    "GROCERY & SUPERMARKET", "CONSUMER DURABLES", "CONSUMER ELECTRONICS",
    "FINANCIAL SERVICES", "APPAREL & ACCESSORIES", "HEALTH & WELLNESS",
    "DEPARTMENT STORES", "DEPARTMENTAL STORES", "MISCELLANEOUS", "RESTAURANTS",
    "AUTOMOTIVE", "ELECTRONICS", "ENTERTAINMENT", "SUPERMARKET", "JEWELLERY",
    "UTILITIES", "EDUCATION", "INSURANCE", "AIRLINES", "RAILWAYS", "TELECOM",
    "MEDICAL", "APPARELS", "APPAREL", "HOTELS", "TRAVEL", "FUEL", "FOOD",
], key=len, reverse=True)
_INDUS_CAT_RE = re.compile(
    r'\s+(?:' + '|'.join(re.escape(c) for c in _INDUS_CATEGORIES) + r')\s*$',
    re.IGNORECASE,
)


def _indusind_desc(prefix):
    """Extract a merchant from the IndusInd 'details + category' segment.

    UPI rows are "UPI <merchant> <ref-digits> <category>" — take the merchant
    between 'UPI' and the reference number. Otherwise strip a trailing known
    merchant-category label.
    """
    prefix = clean_description(prefix)
    m = re.match(r'(?i)^UPI\s+(.+?)\s+\d{8,}\b', prefix)
    if m:
        return clean_description(m.group(1))
    prev = None
    while prev != prefix:
        prev = prefix
        prefix = _INDUS_CAT_RE.sub('', prefix).strip()
    return prefix


def parse_indusind(pdf, card, statement_year):
    """IndusInd CRED RuPay: section-scoped, multi-line-aware. Skips CR rows."""
    rows = []
    in_txn = False
    buffer = []
    for page in pdf.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.debug("%s: text extraction failed: %s", card, exc)
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            low = s.lower()
            if "purchases & cash transactions for" in low or "payment details for" in low:
                in_txn = True
                buffer = []
                continue
            if not in_txn:
                continue
            if low.startswith("total ") or "how to make payments" in low:
                in_txn = False
                buffer = []
                continue

            if _INDUS_DATE_RE.match(s):
                buffer = [s]
            elif buffer:
                buffer.append(s)
            else:
                continue

            combined = " ".join(buffer)
            term = _INDUS_TERM_RE.search(combined)
            if not term:
                continue
            # Completed transaction.
            dm = _INDUS_DATE_RE.match(combined)
            date = parse_date(dm.group(1), statement_year) if dm else ""
            drcr = term.group(3).upper()
            amount = float(term.group(2).replace(",", ""))
            prefix = combined[dm.end():term.start()] if dm else combined[:term.start()]
            desc = _indusind_desc(prefix)
            buffer = []
            if drcr == "CR" or amount <= 0 or not date or not desc:
                continue
            rows.append({
                "date": date,
                "description": desc,
                "raw_description": combined,
                "amount": amount,
                "card": card,
            })
    return dedup(rows)


# Header/footer/payment lines that are never spends in a SC statement.
_SC_SKIP_KEYWORDS = [
    "rewards points summary", "previous balance", "payments/credits",
    "total payment due", "minimum payment due", "statement date",
    "statement period", "payment due date", "credit limit", "cash limit",
    "bill desk payment", "cidnum",  # payment reference
    "making only the minimum", "disclaimer", "most important terms",
    "date description",  # header row
    "igst",  # tax line (e.g. 'IGST @ 18.00%')
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


# A transaction-section header naming the active sub-card, e.g.
#   "Ultimate Mastercard 544438XXXXXX2349"
_SC_CARD_HEADER_RE = re.compile(r'Ultimate Mastercard\s+(5\d{5}[Xx]+\d{4})', re.IGNORECASE)
# A REWARDS POINTS SUMMARY row, e.g.
#   "544438XXXXXX6700 Ultimate Mastercard points 95982.00 1362.00 0.00 97344.00 0.00"
_SC_SUMMARY_RE = re.compile(
    r'(\d{6}[Xx]+\d{4})\s+(.+?)\s+points\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)',
    re.IGNORECASE,
)


def parse_stanchart(pdf, card, statement_year):
    """Standard Chartered Ultimate (one account, possibly two sub-cards).

    Two-pass over all lines: phase 1 extracts transactions (attributing each to
    the active sub-card number), phase 2 reads the REWARDS POINTS SUMMARY table.
    Returns ``(transactions, rewards_list)`` where rewards_list has one entry per
    sub-card. INR (last) amount; CR rows skipped.
    """
    transactions = []
    rewards_list = []
    stop_parsing = False
    in_rewards_summary = False
    current_card_number = ""

    all_lines = []
    for page in pdf.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.debug("%s: text extraction failed on a page: %s", card, exc)
            continue
        all_lines.extend(l.strip() for l in text.split("\n") if l.strip())

    for line in all_lines:
        line_lower = line.lower()

        if "rewards points summary" in line_lower:
            stop_parsing = True
            in_rewards_summary = True
            continue

        if in_rewards_summary:
            sm = _SC_SUMMARY_RE.match(line)
            if sm:
                rewards_list.append({
                    "card_number": sm.group(1),
                    "card_name": sm.group(2).strip(),
                    "opening_balance": int(float(sm.group(3))),
                    "earned": int(float(sm.group(4))),
                    "redeemed": int(float(sm.group(5))),
                    "closing_balance": int(float(sm.group(6))),
                })
            continue

        if stop_parsing:
            continue

        # Transaction section header names the active sub-card.
        hdr = _SC_CARD_HEADER_RE.search(line)
        if hdr:
            current_card_number = hdr.group(1)
            continue

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

        # Strip the columns between description and INR amount: transaction
        # reference (long digit run), rewards earned + type, and the
        # "<N> points [USD x.xx]" / "0" reward fragments.
        desc_raw = re.sub(r'\s+\d{20,}\s+', ' ', desc_raw)
        desc_raw = re.sub(r'\s+\d{1,4}\s+\d{3}\s*$', '', desc_raw)
        desc_raw = re.sub(r'\s+\d{3}\s*$', '', desc_raw)
        desc_raw = re.sub(r'\s+\d+\s+points(?:\s+USD\s+[\d.]+)?\s*$', '', desc_raw,
                          flags=re.IGNORECASE)
        desc_raw = re.sub(r'\s+0\s*$', '', desc_raw).strip()

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
            "card_number": current_card_number,
        })

    return dedup(transactions), rewards_list


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
    "RBL Bank": parse_rbl,
    "RBL IndianOil": parse_rbl,
    "Amazon ICICI": parse_icici,
    "IndusInd": parse_indusind,
    "Axis Amex": parse_generic,
    "Standard Chartered Ultimate": parse_stanchart,
}


# --------------------------------------------------------------------------- #
# Reward points extraction
# --------------------------------------------------------------------------- #
_RW_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}


def _rw_int(s):
    """Parse a reward count like '20,354' or '75362.00' to int. 0 on failure."""
    if s is None:
        return 0
    s = str(s).replace(",", "").strip()
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _rw_find(pattern, text, group=1):
    m = re.search(pattern, text, re.IGNORECASE)
    return _rw_int(m.group(group)) if m else 0


def _statement_month_from_text(text):
    """Extract 'YYYY-MM' from a statement-date line. '' if not found.

    Handles 'May 18, 2026', '23 Apr 2026', and 'Statement Date: 18/05/2026'.
    """
    if not text:
        return ""

    def _mk(year, month):
        try:
            mo = int(month)
        except (TypeError, ValueError):
            mo = _RW_MONTHS.get(str(month)[:3].lower(), 0)
        if 1 <= mo <= 12:
            return f"{int(year):04d}-{mo:02d}"
        return ""

    # Anchored on "Statement Date" (handles SC and numeric forms).
    anchored = [
        r'statement date[^A-Za-z0-9]{0,4}([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(20\d{2})',  # Mon DD, YYYY
        r'statement date[^A-Za-z0-9]{0,4}(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(20\d{2})',     # DD Mon YYYY
        r'statement date[^0-9]{0,4}(\d{1,2})[/-](\d{1,2})[/-](20\d{2})',                  # DD/MM/YYYY
    ]
    for i, pat in enumerate(anchored):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            if i == 0:
                return _mk(m.group(3), m.group(1))
            if i == 1:
                return _mk(m.group(3), m.group(2))
            return _mk(m.group(3), m.group(2))

    # Unanchored fallbacks (first occurrence — usually the statement date).
    m = re.search(r'\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(20\d{2})\b', text)
    if m and m.group(1)[:3].lower() in _RW_MONTHS:
        return _mk(m.group(3), m.group(1))
    m = re.search(r'\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(20\d{2})\b', text)
    if m and m.group(2)[:3].lower() in _RW_MONTHS:
        return _mk(m.group(3), m.group(2))
    return ""


def _rewards_icici(text):
    """ICICI Amazon Pay: EARNINGS block with a '<earned> <transferred>' data row."""
    m = re.search(r'EARNINGS', text)
    if not m:
        return None
    dm = re.search(r'(\d{1,7})\s+(\d{1,7})', text[m.end():m.end() + 250])
    if not dm:
        return None
    earned = _rw_int(dm.group(1))
    return {"opening_balance": 0, "earned": earned, "redeemed": 0,
            "closing_balance": earned, "earned_fuel": 0, "earned_grocery": 0,
            "earned_upi": 0, "earned_other": earned}


def _rewards_idfc(text):
    """IDFC FIRST: rewards summary with category breakdown."""
    earned = _rw_find(r'Earned this Month\s*\+?\s*([\d,]+)', text)
    closing = _rw_find(r'Total Reward Points Available[^\d=]*=?\s*([\d,]+)', text)
    # Points opening balance is an integer (the rupee one is 'Opening Balance r419.58').
    om = re.search(r'Opening Balance\s+([\d,]+)(?!\s*\.\d)', text)
    opening = _rw_int(om.group(1)) if om else 0
    redeemed = _rw_find(r'Adjusted/Redeemed\s*-?\s*([\d,]+)', text)
    fuel = _rw_find(r'HPCL[^\n]*?FASTag[^\n]*?([\d,]+)', text)
    grocery = _rw_find(r'Grocery\s*&\s*Utility spends[^\d]*([\d,]+)', text)
    upi = _rw_find(r'UPI spends[^\d]*([\d,]+)', text)
    other = _rw_find(r'Other retail spends[^\d]*([\d,]+)', text)
    if not (earned or closing or opening):
        return None
    if not closing and (opening or earned):
        closing = max(opening + earned - redeemed, 0)
    return {"opening_balance": opening, "earned": earned, "redeemed": redeemed,
            "closing_balance": closing, "earned_fuel": fuel, "earned_grocery": grocery,
            "earned_upi": upi, "earned_other": other}


def _rewards_hdfc(text):
    """HDFC Diners Black: 'Reward Points <closing>' + a 4-number summary row
    (opening, earned, disbursed, adjusted/lapsed). Swiggy (cashback) → None."""
    # Closing balance sits just before the 'Points Earned' label (e.g. '56,534 Points Earned').
    closing = _rw_find(r'([\d,]+)\s+Points Earned', text)
    # The 4-number summary row (opening earned disbursed adjusted) follows the
    # 'Reward Points ...' header block.
    m = re.search(
        r'Reward Points[\s\S]{0,240}?[\r\n]\s*([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\b',
        text,
    )
    if not m and not closing:
        return None
    opening = earned = redeemed = 0
    if m:
        opening = _rw_int(m.group(1))
        earned = _rw_int(m.group(2))
        redeemed = _rw_int(m.group(3))
    if not closing and (opening or earned):
        closing = max(opening + earned - redeemed, 0)
    if not (earned or closing):
        return None
    return {"opening_balance": opening, "earned": earned, "redeemed": redeemed,
            "closing_balance": closing, "earned_fuel": 0, "earned_grocery": 0,
            "earned_upi": 0, "earned_other": earned}


def _rewards_hsbc(text):
    """HSBC LIVE+ is a cashback card (no reward-point summary) → None."""
    m = re.search(r'reward points?\b[^\n]*?opening[^\d]*([\d,]+)[^\d]+([\d,]+)[^\d]+([\d,]+)',
                  text, re.IGNORECASE)
    if not m:
        return None
    opening, earned, closing = (_rw_int(m.group(i)) for i in (1, 2, 3))
    return {"opening_balance": opening, "earned": earned, "redeemed": 0,
            "closing_balance": closing, "earned_fuel": 0, "earned_grocery": 0,
            "earned_upi": 0, "earned_other": earned}


def _rewards_sc(text):
    """Standard Chartered: REWARDS POINTS SUMMARY card row(s)."""
    best = None
    for m in re.finditer(
        r'(?i)Mastercard\s+points\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)', text
    ):
        opening = _rw_int(m.group(1))
        earned = _rw_int(m.group(2))
        adjusted = _rw_int(m.group(3))
        closing = _rw_int(m.group(4))
        if best is None or earned > best["earned"]:
            best = {"opening_balance": opening, "earned": earned, "redeemed": adjusted,
                    "closing_balance": closing, "earned_fuel": 0, "earned_grocery": 0,
                    "earned_upi": 0, "earned_other": earned}
    return best


def _rewards_generic(text):
    """Best-effort sweep for banks without a bespoke reward parser."""
    m = re.search(
        r'(?:reward|points)\s+(?:balance|earned|summary)[^\n]*\n[^\n]*?(\d[\d,]+)',
        text, re.IGNORECASE,
    )
    if not m:
        return None
    earned = _rw_int(m.group(1))
    if not earned:
        return None
    return {"opening_balance": 0, "earned": earned, "redeemed": 0,
            "closing_balance": earned, "earned_fuel": 0, "earned_grocery": 0,
            "earned_upi": 0, "earned_other": earned}


_REWARD_EXTRACTORS = {
    "Amazon ICICI": _rewards_icici,
    "IDFC Power+": _rewards_idfc,
    "IDFC Select": _rewards_idfc,
    "HSBC Live+": _rewards_hsbc,
    "Standard Chartered Ultimate": _rewards_sc,
    "HDFC Diners Black": _rewards_hdfc,
    "HDFC Swiggy": _rewards_hdfc,
}


def _extract_rewards(card, text):
    """Dispatch to a bank reward extractor. Never raises — returns dict or None."""
    try:
        fn = _REWARD_EXTRACTORS.get(card, _rewards_generic)
        rewards = fn(text)
        if rewards is None:
            return None
        # Ensure all keys present.
        for k in ("opening_balance", "earned", "redeemed", "closing_balance",
                  "earned_fuel", "earned_grocery", "earned_upi", "earned_other"):
            rewards[k] = int(rewards.get(k, 0) or 0)
        return rewards
    except Exception as exc:
        logger.debug("reward extraction failed for %s: %s", card, exc)
        return None


def _month_from_transactions(txns):
    """Most recent YYYY-MM among transaction dates (statement-period anchor)."""
    months = [t["date"][:7] for t in txns if t.get("date") and len(t["date"]) >= 7]
    return max(months) if months else ""


# A description that is just a date, optionally prefixed with "To" (statement
# period fragments like "To 04 MAR 2026" / "04 Mar 2026" / "21/01/26").
_NOISE_DATE_RE = re.compile(
    r'^(?:to\s+)?\d{1,2}[\s/.\-]+(?:[a-z]{3,9}|\d{1,2})[\s/.\-]+\d{2,4}$', re.IGNORECASE
)
# Balance / summary phrases that are never real spends.
_NOISE_SUBSTR = (
    "net outstanding", "outstanding balance", "opening balance", "closing balance",
    "previous balance", "balance carried", "net amount due",
)


def _is_noise_desc(desc):
    """True for non-merchant artifacts that leak from summary/footer rows:
    account balances, statement-period dates, 'Convert' EMI flags, and stray
    statement-text fragments. Applied to every parsed transaction."""
    if not desc:
        return True
    low = desc.strip().lower()
    if low in ("convert", "convert to emi", "balance"):
        return True
    if any(k in low for k in _NOISE_SUBSTR):
        return True
    if _NOISE_DATE_RE.match(low):
        return True
    if re.match(r'^[a-z]{1,3}\s+statement$', low):  # 'il statement', 'ch statement'
        return True
    return False


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
    """Detect the bank, parse it, and extract reward points.

    Returns ``{"transactions": [...], "rewards": dict|None}``. Each transaction
    dict has: date, description, raw_description, amount, card, category,
    confidence, suggested_category, source_file. Returns empty transactions (and
    rewards None) if the bank is unrecognized. Uses context managers throughout.
    """
    card = detect_bank(pdf_path)
    if not card:
        logger.error("Bank format not recognized: %s", os.path.basename(pdf_path))
        return {"transactions": [], "rewards": None}

    with pdfplumber.open(pdf_path) as pdf:
        head = ""
        full = []
        for idx, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            full.append(txt)
            if idx < 3:
                head += txt
        full_text = "\n".join(full)
        statement_year = _statement_year_from_text(head)
        parser_fn = CARD_PARSER.get(card, parse_generic)
        # Standard Chartered returns (transactions, rewards_list); others a list.
        sc_rewards_list = []
        if card == "Standard Chartered Ultimate":
            raw_rows, sc_rewards_list = parser_fn(pdf, card, statement_year)
        else:
            raw_rows = parser_fn(pdf, card, statement_year)
    # pdf is closed here.

    source_file = os.path.basename(pdf_path)
    txns = []
    for r in raw_rows:
        desc = r["description"]
        if _is_noise_desc(desc):  # drop balances, statement dates, 'Convert' flags
            continue
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
            "card_number": r.get("card_number", ""),
        })

    # Reward points (never fails the parse). SC combines its per-sub-card rows
    # into one account-level summary; other banks use the text extractors.
    if card == "Standard Chartered Ultimate":
        if sc_rewards_list:
            rewards = {
                "opening_balance": sum(r["opening_balance"] for r in sc_rewards_list),
                "earned": sum(r["earned"] for r in sc_rewards_list),
                "redeemed": sum(r["redeemed"] for r in sc_rewards_list),
                "closing_balance": sum(r["closing_balance"] for r in sc_rewards_list),
                "earned_fuel": 0, "earned_grocery": 0, "earned_upi": 0,
                "earned_other": sum(r["earned"] for r in sc_rewards_list),
                "card_breakdown": sc_rewards_list,
            }
        else:
            rewards = None
    else:
        rewards = _extract_rewards(card, full_text)
    if rewards is not None:
        month = _statement_month_from_text(full_text) or _month_from_transactions(txns)
        rewards["statement_month"] = month

    return {"transactions": txns, "rewards": rewards}


def parse_file(pdf_path):
    """Parse one PDF into the {"ok", "card", "transactions", "rewards", "error"} envelope.

    Never raises — a bad file is reported via the dict so a batch keeps going.
    Used by the FastAPI ``/api/parse`` route.
    """
    source_file = os.path.basename(pdf_path)
    result = {"ok": False, "card": None, "transactions": [], "rewards": None, "error": None}
    try:
        parsed = detect_and_parse(pdf_path)
        txns = parsed["transactions"]
        result["transactions"] = txns
        result["rewards"] = parsed.get("rewards")
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
    parsed = detect_and_parse(pdf_path)
    transactions = parsed["transactions"]
    rewards = parsed.get("rewards")
    if not transactions:
        print(f"❌ No transactions extracted from {pdf_path}")
        if rewards:
            print(f"   (rewards found: {rewards})")
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
    if rewards:
        print("\nRewards:")
        print(f"  month={rewards.get('statement_month')} opening={rewards['opening_balance']} "
              f"earned={rewards['earned']} redeemed={rewards['redeemed']} "
              f"closing={rewards['closing_balance']} "
              f"(fuel={rewards['earned_fuel']} grocery={rewards['earned_grocery']} "
              f"upi={rewards['earned_upi']} other={rewards['earned_other']})")
