# SpendLens 💳

> Indian credit card spending analyzer. Drop your bank statements, get a beautiful local dashboard.

Works with HDFC, HSBC, IDFC First, RBL, Amazon ICICI, IndusInd, Axis, Standard Chartered.
All data stays on your machine — nothing is uploaded anywhere.

![Dashboard](docs/screenshot.png)

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/spendlens
cd spendlens

# Backend
pip install -r requirements.txt

# Frontend
cd frontend && npm install && cd ..

# Copy env and set your income
cp .env.example .env

# Drop unlocked PDF statements into data/statements/
# Then parse:
python parse.py --folder ./data/statements

# Start backend (new terminal)
uvicorn backend.main:app --reload

# Start frontend (new terminal)
cd frontend && npm run dev

# Open http://localhost:5173
```

## Optional: AI-Assisted Categorization

SpendLens can use Claude AI to suggest categories for unknown merchants.

**Setup:**
1. Get a free API key at [console.anthropic.com](https://console.anthropic.com)
2. Add to your `.env`: `ANTHROPIC_API_KEY=sk-ant-...`
3. Re-run `python parse.py --folder ./data/statements`

**Without a key:** Unknown merchants go to the Review Queue for manual categorization.  
**With a key:** Claude suggests a category. You confirm or change it in the Review Queue.

Your API key stays in your local `.env` file and is never committed to the repo.

## Supported Banks

| Bank | Cards |
|---|---|
| HDFC Bank | Diners Black, Swiggy |
| HSBC | LIVE+ |
| IDFC First Bank | Power+, Select |
| RBL Bank | IndianOil XTRA |
| ICICI Bank | Amazon Pay |
| IndusInd Bank | Credit Card |
| Axis Bank | Amex Privilege |
| Standard Chartered | All cards |

## PDF Requirements

Statements must be **unlocked/unencrypted** before dropping into the folder.

To unlock on Mac: open in Preview → Enter password → File → Export as PDF (leave password blank).

To bulk unlock on terminal:
```bash
brew install qpdf
for f in locked/*.pdf; do
  qpdf --password=YOUR_PASSWORD --decrypt "$f" "unlocked/$(basename $f)"
done
```

## How to Add a New Bank

1. Add a detection string and card name to the `BANK_FORMATS` list in `parser.py`
2. Write a `parse_BANKNAME(text, lines, card)` function that returns a list of `{date, description, amount}` dicts
3. Add the bank name → function key in the `PARSERS` mapping used by `detect_and_parse()`
4. Add relevant merchant keywords to `CATEGORY_RULES` in `categorizer.py`
5. Submit a PR!

## CLI Reference

```bash
# Basic usage
python parse.py --folder ./data/statements

# With custom income
python parse.py --folder ./data/statements --income 218000

# Wipe DB and re-parse everything
python parse.py --folder ./data/statements --clear

# Show each transaction found
python parse.py --folder ./data/statements --verbose
```

## API Reference

The FastAPI backend runs at `http://localhost:8000` (interactive docs at `/docs`):

| Method | Route | Description |
|---|---|---|
| GET | `/api/summary` | Totals, savings rate, card count, date range |
| GET | `/api/transactions` | Filter by `card`, `category`, `month`, `q`, amount, paginated |
| GET | `/api/categories` | Per-category totals, % of total/income, MoM change |
| GET | `/api/monthly` | `{month, category, total}` rows for the stacked chart |
| GET | `/api/merchants` | Top 20 merchants |
| GET | `/api/cards` | Per-card totals and category splits |
| GET | `/api/insights` | Red / amber / green spending insights |
| POST | `/api/recategorize` | `{id, category}` or `{pattern, category}` — applies retroactively |
| GET | `/api/review-queue` | Transactions needing categorization (Miscellaneous or confidence < 0.7) |
| POST | `/api/bulk-categorize` | `[{id, category}]` — confirm many at once, saves overrides |
| POST | `/api/parse` | Re-parse the statements folder |
| GET | `/api/export/csv` | Download all transactions as CSV |

## Privacy

- `data/statements/` is in `.gitignore` — your PDFs are never committed
- `data/spendlens.db` is gitignored — your transaction data stays local
- No analytics, no telemetry, no network calls from the backend

## Contributing

PRs welcome. Especially for:
- New bank parsers
- Better merchant name cleaning
- Additional insight rules

## License

MIT
