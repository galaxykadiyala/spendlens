# SpendLens 💳

> Indian credit-card spending analyzer. Drop your bank statements in, get a beautiful local dashboard. Nothing is uploaded anywhere — all parsing and storage happen on your machine.

Works with HDFC (Diners Black, Swiggy, Year-End), HSBC LIVE+, IDFC First (Power+, Select), RBL (IndianOil XTRA & plain), Amazon Pay ICICI, IndusInd (CRED RuPay), Axis, and Standard Chartered.

![Dashboard](docs/screenshot.png)

## What you get

A dark, local dashboard with:

- **Overview** — total/monthly/savings stats with a **From–To month range** and a per-month spend breakdown
- **Categories** — donut + sortable table, with **This Month / Last 3M / 6M / This Year / All** range pills
- **Monthly** — stacked-area trend by category; click a month to expand its transactions
- **Cards** — spend comparison and per-card category splits
- **Merchants** — top merchants by spend
- **Rewards** — reward-point balances, trend, effective earn-rate per card, category optimizer
- **Transactions** — search/filter/sort, inline re-categorize, **exclude** rows from totals, **auto-match refunds**, CSV export
- **Review Queue** — confirm categories for unknown merchants (safe: only saves rows you review)
- **Insights** — red/amber/green spending alerts

---

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** and npm
- (optional) `qpdf` if you need to bulk-unlock password-protected PDFs

---

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/spendlens
cd spendlens

# 1) Backend — create a virtualenv and install deps
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2) Frontend — install deps
cd frontend && npm install && cd ..

# 3) Config — copy the example env and set your monthly income
cp .env.example .env
# edit .env if needed (MONTHLY_INCOME, STATEMENTS_FOLDER, DB_PATH, optional ANTHROPIC_API_KEY)
```

## Add your statements

1. **Unlock** any password-protected PDFs (see [PDF unlocking](#pdf-unlocking) below).
2. Put the unlocked PDFs in **`data/statements/`** (create it if it doesn't exist):

   ```bash
   mkdir -p data/statements
   cp /path/to/*.pdf data/statements/
   ```

   This folder is **git-ignored**, so your statements never get committed.

## Parse

With the virtualenv active:

```bash
python parse.py --folder ./data/statements
```

This reads every PDF, detects the bank, extracts debits (credits/payments are skipped), categorizes them, and writes to a local SQLite DB at `data/spendlens.db`. Re-running is **idempotent** — already-parsed files are skipped unless you pass `--clear`.

## Run (two terminals)

**Terminal 1 — backend API** (from the repo root, venv active):

```bash
uvicorn backend.main:app --reload
```

Serves the API at `http://localhost:8000` (interactive docs at `/docs`).

**Terminal 2 — frontend**:

```bash
cd frontend && npm run dev
```

Open **http://localhost:5173**. The dev server proxies `/api` to the backend on port 8000, so **both must be running**.

---

## Using it on another machine

The code is in git, but your **data is not** (`data/`, `.env`, `node_modules/` are git-ignored). So on a new system:

```bash
git clone <your-repo> && cd spendlens
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
cp .env.example .env                 # set income / API key again
mkdir -p data/statements             # copy your unlocked PDFs back in
python parse.py --folder ./data/statements --clear
uvicorn backend.main:app --reload    # terminal 1
cd frontend && npm run dev           # terminal 2
```

Your statements and the parsed `spendlens.db` don't travel with the repo — copy the PDFs over and re-parse (or copy `data/spendlens.db` manually if you want the exact same DB).

---

## Optional: AI-assisted categorization

SpendLens can use Claude to suggest categories for unknown merchants.

1. Get an API key at [console.anthropic.com](https://console.anthropic.com)
2. Add it to `.env`: `ANTHROPIC_API_KEY=sk-ant-...`
3. Re-run `python parse.py --folder ./data/statements`

**Without a key:** unknown merchants go to the Review Queue for manual categorization.
**With a key:** Claude suggests a category; you confirm/change it in the Review Queue.

Your key stays in your local `.env` and is never committed.

---

## Supported banks

| Bank | Cards / formats |
|---|---|
| HDFC Bank | Diners Black, Swiggy, **Year-End statement** |
| HSBC | LIVE+ |
| IDFC First Bank | Power+, Select |
| RBL Bank | IndianOil XTRA, plain RBL |
| ICICI Bank | Amazon Pay |
| IndusInd Bank | CRED RuPay |
| Axis Bank | Amex / generic |
| Standard Chartered | Ultimate (multi-card accounts) |

> ⚠️ A **Year-End** statement covers a full financial year and overlaps your monthly statements for the same period. Keep **either** the year-end **or** the monthly files for a given card — not both — or spend will be double-counted.

## PDF unlocking

Statements must be **unlocked/unencrypted** before parsing.

On Mac: open in Preview → enter password → File → Export as PDF (leave password blank).

Bulk unlock in a terminal:

```bash
brew install qpdf
for f in locked/*.pdf; do
  qpdf --password=YOUR_PASSWORD --decrypt "$f" "data/statements/$(basename "$f")"
done
```

## CLI reference

```bash
python parse.py --folder ./data/statements              # parse new files (idempotent)
python parse.py --folder ./data/statements --income 218000   # set monthly income
python parse.py --folder ./data/statements --clear      # wipe DB and re-parse everything
python parse.py --folder ./data/statements --verbose    # DEBUG logging (which strategy won)
python parse.py --folder ./data/statements --workers 8  # parallel parsing workers

# Debug a single file without touching the DB:
python -c "from backend.parser import test_parser; test_parser('data/statements/yourfile.pdf')"
```

## API reference

Backend at `http://localhost:8000` (docs at `/docs`). Aggregate endpoints ignore excluded transactions.

| Method | Route | Description |
|---|---|---|
| GET | `/api/summary` | Totals, savings rate, card count, date range |
| GET | `/api/transactions` | Filter by `card`, `category`, `month`, `q`, amount, `excluded`; paginated |
| GET | `/api/categories` | Per-category totals, % of total/income, MoM change |
| GET | `/api/monthly` | `{month, category, total}` rows for the stacked chart |
| GET | `/api/merchants` | Top merchants |
| GET | `/api/cards` | Per-card totals and category splits |
| GET | `/api/insights` | Red / amber / green spending insights |
| GET | `/api/rewards/summary` | Reward-point summaries per card/month |
| GET | `/api/rewards/rates` | Effective earn rate (points per ₹100) per card |
| GET | `/api/rewards/optimize` | Best card per category, where data allows |
| POST | `/api/recategorize` | `{id, category}` or `{pattern, category}` — applies retroactively |
| GET | `/api/review-queue` | Transactions needing categorization (Miscellaneous or confidence < 0.7) |
| POST | `/api/bulk-categorize` | `[{id, category}]` — confirm many at once |
| POST | `/api/transactions/{id}/exclude` | `{excluded: true/false}` — exclude/include a row |
| POST | `/api/transactions/auto-exclude-refunds` | Auto-match debit/credit refund pairs |
| POST | `/api/parse` | Re-parse the statements folder |
| GET | `/api/export/csv` | Download active transactions as CSV |

## Adding a new bank

1. Add a detection marker + card name to the `DETECTION` dict in `backend/parser.py`.
2. Write a `parse_<bank>(pdf, card, statement_year)` function returning a list of `{date, description, amount}` dicts (use the existing parsers as templates).
3. Map the card name → your function in `CARD_PARSER`.
4. Add relevant merchant keywords to `CATEGORY_RULES` in `backend/categorizer.py`.
5. Test with `test_parser(...)`, then open a PR.

## Privacy

- `data/statements/`, `data/*.db`, and `.env` are git-ignored — PDFs, your transaction DB, and your API key stay local.
- No analytics, no telemetry, no outbound network calls from the backend (the only optional network call is to the Anthropic API, and only if you set `ANTHROPIC_API_KEY`).

## License

MIT
