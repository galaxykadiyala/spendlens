"""Keyword-based transaction categorizer.

Matching is case-insensitive against the cleaned merchant/description. User
overrides stored in the DB take precedence over the static rules and are applied
retroactively when added.
"""

try:
    from backend import db
except ImportError:  # allow `import db` when run from inside the backend folder
    import db

CATEGORY_RULES = {
    "Fuel": ["hpcl", "hp pay", "shell", "filling station", "fuel station", "petrol",
             "sbt fill", "sri sathya", "garuda petroleum", "kona fill", "siri fill",
             "hp pay direct", "indian oil corpo"],
    "Groceries": ["zepto", "blinkit", "firstclub", "milkbasket", "delightful gourmet",
                  "freshtohome", "bigbasket", "jiomart", "reliance retail", "reliance payment",
                  "grocer", "fresh to home"],
    "Food Delivery": ["swiggy", "zomato", "dunzo"],
    "Food & Dining": ["restaurant", "kfc", "mcdonald", "starbucks", "taco bell", "hungerbox",
                      "pizza", "burger", "cafe", "anand sweets", "la sunila", "sukho thai",
                      "khansaheb", "empire restaurant", "samosaparty", "pearltri food",
                      "tara s", "kevala", "smoor", "simplifyfoods", "mannash"],
    "Medical": ["apollo", "medplus", "pharmacy", "hospital", "dental", "clinic",
                "ebisu eye", "ramdev medical", "raam medical", "rays ph",
                "mosaic wellness", "manmatters", "kumon"],
    "Education": ["nps trust", "school", "tuition", "growthx"],
    "Shopping": ["amazon", "flipkart", "myntra", "nykaa", "lulu international",
                 "fab india", "factory outlet", "svastik", "portronics", "skadoosh",
                 "silverz", "dailyobject", "ashi colour", "kashaya", "innovative retail"],
    "Health & Wellness": ["kashayam ayurveda", "body craft", "chillchemy", "o2 spa", "vlcc", "reboot"],
    "Transport": ["uber", "ola", "parking", "metro", "cab", "park plus", "fastag"],
    "Bills & Utilities": ["airtel", "vodafone", "vil pay", "electricity", "broadband",
                          "tata sky", "cred", "credpay", "airtel in si", "www airtel"],
    "Subscriptions": ["netflix", "hotstar", "spotify", "amazon prime", "mflxnl",
                      "dream11", "dreampl", "alleven", "uni seo", "directo",
                      "claude.ai", "anthropic"],
    "Personal Care": ["loreal", "hair", "salon", "grooming"],
    "Household Help": ["davulur", "nishamb", "mrs pri", "manasa", "mr sadu", "prajwal"],
    "Entertainment": ["movie", "pvr", "inox", "district", "game"],
    "Kids": ["firstcry", "ignited brain", "ms kids", "cuddly"],
    "Insurance": ["acko", "lic", "insurance"],
    "Investments": ["nps trust", "mutual fund", "sip", "groww"],
    "Rent": ["rentenpe", "rentpe", "nobroker", "housing.com rent", "magicbricks rent"],
    "Fees & Charges": ["joining fee", "annual fee", "igst", "surcharge", "fuel surcharge",
                       "forex markup", "rent surcharge", "igst @"],
}

DEFAULT_CATEGORY = "Miscellaneous"


def categorize(description):
    """Return a {"category", "confidence"} dict for a description.

    - User override match → confidence 1.0 (user-confirmed mappings are trusted).
    - Keyword rule match   → confidence 1.0.
    - No match             → Miscellaneous, confidence 0.0.

    User overrides (DB) win first; then the static keyword rules in declared
    order; otherwise Miscellaneous.
    """
    if not description:
        return {"category": DEFAULT_CATEGORY, "confidence": 0.0}
    text = description.lower()

    # User overrides take precedence.
    try:
        for pattern, category in db.get_overrides():
            if pattern and pattern in text:
                return {"category": category, "confidence": 1.0}
    except Exception:
        # DB may not be initialised yet during early parsing — fall through to rules.
        pass

    for category, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw in text:
                return {"category": category, "confidence": 1.0}
    return {"category": DEFAULT_CATEGORY, "confidence": 0.0}


def recategorize(pattern, category):
    """Save a pattern→category override and apply it to existing rows.

    Returns the number of transactions retroactively updated.
    """
    db.save_override(pattern, category)
    return db.apply_override_retroactively(pattern, category)
