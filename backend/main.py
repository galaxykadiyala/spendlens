"""SpendLens FastAPI backend — all routes in one module.

Reads transactions from the local SQLite DB and serves aggregates for the
dashboard. No network calls, no telemetry. CORS is opened for the Vite dev
server at http://localhost:5173.
"""

import os
import io
import csv
from typing import List
from collections import defaultdict, OrderedDict

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

try:
    from backend import db, parser, categorizer
except ImportError:  # running from inside backend/
    import db
    import parser
    import categorizer

app = FastAPI(title="SpendLens API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    db.init_db()


# --------------------------------------------------------------------------- #
# Aggregation helpers
# --------------------------------------------------------------------------- #
def _month_of(date_str):
    """YYYY-MM from an ISO date string (or None)."""
    return date_str[:7] if date_str and len(date_str) >= 7 else None


def _sorted_months(transactions):
    return sorted({_month_of(t["date"]) for t in transactions if _month_of(t["date"])})


def _round(x, n=2):
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return 0.0


def _category_month_totals(transactions):
    """Return {category: {month: total}}."""
    out = defaultdict(lambda: defaultdict(float))
    for t in transactions:
        cat = t.get("category") or "Miscellaneous"
        m = _month_of(t["date"])
        if m:
            out[cat][m] += t.get("amount") or 0
    return out


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/api/summary")
def get_summary():
    txns = db.all_transactions()
    income = db.get_monthly_income()
    total = sum(t["amount"] or 0 for t in txns)
    months = _sorted_months(txns)
    num_months = len(months) or 1
    monthly_avg = total / num_months
    cards = {t["card"] for t in txns if t.get("card")}

    # Savings rate over the observed period: income earned vs amount spent.
    total_income = income * num_months
    savings_rate = ((total_income - total) / total_income * 100) if total_income else 0

    dates = sorted(t["date"] for t in txns if t.get("date"))
    return {
        "total_spend": _round(total),
        "monthly_avg": _round(monthly_avg),
        "savings_rate": _round(savings_rate, 1),
        "monthly_income": _round(income),
        "card_count": len(cards),
        "cards": sorted(cards),
        "transaction_count": len(txns),
        "num_months": num_months,
        "date_range": {
            "start": dates[0] if dates else None,
            "end": dates[-1] if dates else None,
        },
        "months": months,
    }


@app.get("/api/transactions")
def get_transactions(
    card: str = Query(None),
    category: str = Query(None),
    month: str = Query(None),
    q: str = Query(None),
    min_amount: float = Query(None),
    max_amount: float = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=1000),
    sort: str = Query("date"),
    dir: str = Query("DESC"),
):
    offset = (page - 1) * per_page
    rows, total = db.query_transactions(
        card=card, category=category, month=month, q=q,
        min_amount=min_amount, max_amount=max_amount,
        limit=per_page, offset=offset, order_by=sort, order_dir=dir,
    )
    return {
        "transactions": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if per_page else 1,
    }


@app.get("/api/categories")
def get_categories():
    txns = db.all_transactions()
    income = db.get_monthly_income()
    total = sum(t["amount"] or 0 for t in txns) or 1
    months = _sorted_months(txns)
    last_month = months[-1] if months else None
    prev_month = months[-2] if len(months) >= 2 else None

    cat_month = _category_month_totals(txns)
    cat_total = {cat: sum(mt.values()) for cat, mt in cat_month.items()}

    result = []
    for cat, ctotal in sorted(cat_total.items(), key=lambda kv: kv[1], reverse=True):
        # MoM change for the latest vs previous month.
        mom = None
        if last_month and prev_month:
            cur = cat_month[cat].get(last_month, 0)
            prev = cat_month[cat].get(prev_month, 0)
            if prev > 0:
                mom = (cur - prev) / prev * 100
            elif cur > 0:
                mom = 100.0
            else:
                mom = 0.0
        result.append({
            "category": cat,
            "total": _round(ctotal),
            "pct_of_total": _round(ctotal / total * 100, 1),
            "pct_of_income": _round(ctotal / (income * (len(months) or 1)) * 100, 1) if income else 0,
            "mom_change": _round(mom, 1) if mom is not None else None,
        })
    return result


@app.get("/api/monthly")
def get_monthly():
    txns = db.all_transactions()
    cat_month = _category_month_totals(txns)
    out = []
    for cat, mt in cat_month.items():
        for month, total in mt.items():
            out.append({"month": month, "category": cat, "total": _round(total)})
    out.sort(key=lambda r: (r["month"], r["category"]))
    return out


@app.get("/api/merchants")
def get_merchants():
    txns = db.all_transactions()
    agg = defaultdict(lambda: {"total": 0.0, "count": 0, "category": None, "last_seen": None})
    for t in txns:
        name = (t.get("description") or "").strip() or "Unknown"
        a = agg[name]
        a["total"] += t.get("amount") or 0
        a["count"] += 1
        a["category"] = a["category"] or t.get("category")
        d = t.get("date")
        if d and (a["last_seen"] is None or d > a["last_seen"]):
            a["last_seen"] = d
    ranked = sorted(agg.items(), key=lambda kv: kv[1]["total"], reverse=True)[:20]
    return [
        {
            "merchant": name,
            "category": a["category"] or "Miscellaneous",
            "total": _round(a["total"]),
            "count": a["count"],
            "last_seen": a["last_seen"],
        }
        for name, a in ranked
    ]


@app.get("/api/cards")
def get_cards():
    txns = db.all_transactions()
    total_all = sum(t["amount"] or 0 for t in txns) or 1
    card_total = defaultdict(float)
    card_cat = defaultdict(lambda: defaultdict(float))
    for t in txns:
        card = t.get("card") or "Unknown"
        amt = t.get("amount") or 0
        card_total[card] += amt
        card_cat[card][t.get("category") or "Miscellaneous"] += amt

    result = []
    for card, ctotal in sorted(card_total.items(), key=lambda kv: kv[1], reverse=True):
        categories = [
            {"category": c, "total": _round(v)}
            for c, v in sorted(card_cat[card].items(), key=lambda kv: kv[1], reverse=True)
        ]
        result.append({
            "card": card,
            "total": _round(ctotal),
            "pct": _round(ctotal / total_all * 100, 1),
            "categories": categories,
        })
    return result


@app.get("/api/insights")
def get_insights():
    txns = db.all_transactions()
    income = db.get_monthly_income()
    insights = []
    if not txns:
        return insights

    total = sum(t["amount"] or 0 for t in txns) or 1
    months = _sorted_months(txns)
    cat_month = _category_month_totals(txns)
    cat_total = {cat: sum(mt.values()) for cat, mt in cat_month.items()}

    # Rule 1: any category > 30% of total spend.
    for cat, ctotal in cat_total.items():
        pct = ctotal / total * 100
        if pct > 30:
            insights.append({
                "level": "red",
                "message": f"⚠️ {cat} is {pct:.0f}% of total spend",
                "amount": _round(ctotal),
            })

    # Rule 2: grocery apps count > 2.
    GROCERY_APPS = ["zepto", "blinkit", "bigbasket", "jiomart", "milkbasket",
                    "freshtohome", "fresh to home", "firstclub"]
    used = set()
    for t in txns:
        d = (t.get("description") or "").lower()
        for app in GROCERY_APPS:
            if app in d:
                used.add(app)
    if len(used) > 2:
        insights.append({
            "level": "amber",
            "message": f"💡 You use {len(used)} grocery apps — consolidating could reduce fees",
            "amount": None,
        })

    # Rule 3: recurring same-merchant ±10% appearing monthly >= 3 times -> subscription.
    merchant_months = defaultdict(list)  # name -> list of (month, amount)
    for t in txns:
        name = (t.get("description") or "").strip().lower()
        m = _month_of(t["date"])
        if name and m:
            merchant_months[name].append((m, t.get("amount") or 0))
    for name, entries in merchant_months.items():
        by_month = defaultdict(float)
        for m, amt in entries:
            by_month[m] += amt
        if len(by_month) >= 3:
            amounts = list(by_month.values())
            avg = sum(amounts) / len(amounts)
            if avg > 0 and all(abs(a - avg) <= 0.10 * avg for a in amounts):
                insights.append({
                    "level": "amber",
                    "message": f"🔁 Detected subscription: '{name}' recurring {len(by_month)} months (~₹{avg:,.0f}/mo)",
                    "amount": _round(avg),
                })

    # Rule 4: medical transactions > 5000 -> likely one-time.
    for t in txns:
        if (t.get("category") == "Medical") and (t.get("amount") or 0) > 5000:
            insights.append({
                "level": "amber",
                "message": f"🏥 Large medical spend of ₹{t['amount']:,.0f} on {t['date']} — likely one-time",
                "amount": _round(t["amount"]),
            })

    # Rule 5: any month total > monthly income -> overspent.
    month_totals = defaultdict(float)
    for t in txns:
        m = _month_of(t["date"])
        if m:
            month_totals[m] += t.get("amount") or 0
    for m, mt in sorted(month_totals.items()):
        if income and mt > income:
            insights.append({
                "level": "red",
                "message": f"🚨 You overspent in {m}",
                "amount": _round(mt),
            })

    # Rule 6: category with highest MoM increase (latest vs previous month).
    if len(months) >= 2:
        last_m, prev_m = months[-1], months[-2]
        best = None
        for cat, mt in cat_month.items():
            cur = mt.get(last_m, 0)
            prev = mt.get(prev_m, 0)
            if prev > 0:
                change = (cur - prev) / prev * 100
                if change > 0 and (best is None or change > best[1]):
                    best = (cat, change, cur)
        if best:
            insights.append({
                "level": "amber",
                "message": f"{best[0]} spend jumped {best[1]:.0f}% vs last month",
                "amount": _round(best[2]),
            })

    # Rule 7: savings rate > 40% -> green.
    num_months = len(months) or 1
    total_income = income * num_months
    if total_income:
        savings_rate = (total_income - total) / total_income * 100
        if savings_rate > 40:
            insights.append({
                "level": "green",
                "message": f"✅ Great job — saving {savings_rate:.0f}% of income this month",
                "amount": None,
            })

    # Order: red first, then amber, then green for a sensible default display.
    order = {"red": 0, "amber": 1, "green": 2}
    insights.sort(key=lambda i: order.get(i["level"], 3))
    return insights


class RecategorizeBody(BaseModel):
    id: int = None
    category: str
    pattern: str = None


@app.post("/api/recategorize")
def post_recategorize(body: RecategorizeBody):
    """Recategorize either a single transaction (by id) or a pattern.

    - If `pattern` is given, save it as an override and apply retroactively.
    - If only `id` is given, update that transaction and also derive a pattern
      from its description so future imports of the same merchant match.
    """
    if not body.category:
        raise HTTPException(status_code=400, detail="category is required")

    if body.pattern:
        updated = categorizer.recategorize(body.pattern, body.category)
        return {"ok": True, "updated": updated, "pattern": body.pattern.lower().strip()}

    if body.id is not None:
        txn = db.get_transaction(body.id)
        if not txn:
            raise HTTPException(status_code=404, detail="transaction not found")
        db.update_transaction_category(body.id, body.category)
        # Persist a pattern from the merchant description for future imports.
        pattern = (txn.get("description") or "").strip().lower()
        updated = 1
        if pattern:
            updated = categorizer.recategorize(pattern, body.category)
        return {"ok": True, "updated": updated, "pattern": pattern}

    raise HTTPException(status_code=400, detail="provide either id or pattern")


@app.get("/api/review-queue")
def get_review_queue():
    """Transactions needing categorization (Miscellaneous or confidence < 0.7).

    Ordered by amount DESC so the highest-value items are reviewed first.
    """
    rows = db.get_review_queue()
    return {
        "total": len(rows),
        "transactions": [
            {
                "id": r["id"],
                "date": r["date"],
                "description": r["description"],
                "amount": _round(r["amount"]),
                "card": r["card"],
                "category": r["category"],
                "suggested_category": r.get("suggested_category") or "",
                "confidence": _round(r.get("confidence") or 0.0, 2),
            }
            for r in rows
        ],
    }


class BulkCategorizeItem(BaseModel):
    id: int
    category: str


@app.post("/api/bulk-categorize")
def post_bulk_categorize(items: List[BulkCategorizeItem]):
    """Confirm categories for many transactions at once.

    For each item: set category + confidence=1.0, and persist the cleaned
    description as a category override so future parses match automatically.
    """
    updated = 0
    for item in items:
        if not item.category:
            continue
        txn = db.get_transaction(item.id)
        if not txn:
            continue
        db.confirm_category(item.id, item.category)
        pattern = (txn.get("description") or "").strip().lower()
        if pattern:
            # Save the override (also retroactively tags matching siblings).
            categorizer.recategorize(pattern, item.category)
        updated += 1
    return {"updated": updated}


@app.get("/api/rewards/summary")
def get_rewards_summary():
    """All reward-point summaries (one row per card+month)."""
    return db.get_reward_summary()


@app.get("/api/rewards/rates")
def get_rewards_rates():
    """Effective reward rate per card: total_points / total_spend * 100."""
    summaries = db.get_points_by_card()
    txn_data = db.all_transactions()

    spend_by_card = defaultdict(float)
    for t in txn_data:
        spend_by_card[t["card"]] += t["amount"] or 0

    rates = []
    for s in summaries:
        card = s["card"]
        spend = spend_by_card.get(card, 0)
        rate = (s["total_earned"] / spend * 100) if spend > 0 else 0
        rates.append({
            "card": card,
            "total_spend": _round(spend),
            "total_points": s["total_earned"],
            "latest_balance": s["latest_balance"],
            "rate_per_100": _round(rate, 1),
        })
    return sorted(rates, key=lambda x: -x["rate_per_100"])


@app.get("/api/rewards/optimize")
def get_rewards_optimize():
    """Per-category 'best card' recommendations where breakdown data exists.

    Uses the category-specific earned_fuel / earned_grocery / earned_upi figures
    (currently only IDFC provides these) against per-card+category spend. Omits a
    category entirely when data is insufficient rather than guessing.
    """
    rows = db.get_reward_summary()
    txn_data = db.all_transactions()

    spend = defaultdict(float)
    for t in txn_data:
        spend[(t["card"], t.get("category", "Miscellaneous"))] += t["amount"] or 0

    pts = defaultdict(float)
    for r in rows:
        card = r["card"]
        if r.get("earned_fuel"):
            pts[(card, "Fuel")] += r["earned_fuel"]
        if r.get("earned_grocery"):
            pts[(card, "Groceries")] += r["earned_grocery"]
        if r.get("earned_upi"):
            pts[(card, "UPI")] += r["earned_upi"]

    recommendations = []
    categories = sorted({cat for _, cat in pts.keys()})
    all_cards = {c for c, _ in spend.keys()}
    for cat in categories:
        card_rates = []
        for card in all_cards:
            cat_spend = spend.get((card, cat), 0)
            cat_pts = pts.get((card, cat), 0)
            if cat_spend > 0 and cat_pts > 0:
                card_rates.append({"card": card, "rate": _round(cat_pts / cat_spend * 100, 1)})
        if card_rates:
            card_rates.sort(key=lambda x: -x["rate"])
            best = card_rates[0]
            recommendations.append({
                "category": cat,
                "best_card": best["card"],
                "rate_per_100": best["rate"],
                "all_cards": card_rates,
            })
    return recommendations


@app.post("/api/parse")
def post_parse():
    """Re-parse every PDF in STATEMENTS_FOLDER and insert new transactions."""
    folder = os.environ.get("STATEMENTS_FOLDER", "./data/statements")
    if not os.path.isdir(folder):
        raise HTTPException(status_code=400, detail=f"folder not found: {folder}")

    db.init_db()
    results = []
    total_new = 0
    for fname in sorted(os.listdir(folder)):
        if not fname.lower().endswith(".pdf"):
            continue
        if db.source_already_parsed(fname):
            results.append({"file": fname, "status": "skipped", "transactions": 0})
            continue
        res = parser.parse_file(os.path.join(folder, fname))
        if res["ok"]:
            n = db.insert_transactions(res["transactions"])
            total_new += n
            results.append({"file": fname, "status": "parsed", "card": res["card"], "transactions": n})
        else:
            results.append({"file": fname, "status": "failed", "error": res["error"]})
    return {"ok": True, "total_new": total_new, "files": results}


@app.get("/api/export/csv")
def export_csv():
    txns = db.all_transactions()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "date", "description", "raw_description", "amount",
                     "card", "category", "source_file", "parsed_at"])
    for t in txns:
        writer.writerow([
            t.get("id"), t.get("date"), t.get("description"), t.get("raw_description"),
            t.get("amount"), t.get("card"), t.get("category"),
            t.get("source_file"), t.get("parsed_at"),
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=spendlens_transactions.csv"},
    )


@app.get("/")
def root():
    return {"name": "SpendLens API", "version": "1.0", "docs": "/docs"}
