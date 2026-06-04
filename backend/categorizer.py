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
    'Fuel': ['hpcl', 'hp pay', 'shell', 'filling station', 'fuel station', 'petrol', 'sbt fill', 'sri sathya', 'garuda petroleum', 'kona fill', 'siri fill', 'hp pay direct', 'indian oil corpo', 'petro service', 'avighna enterprises ja', 'sri chakra fuel statio', 'parrikar petro servi', 'andal siri', 'vrishabhadri', 'sree kodandarama'],
    'Groceries': ['zepto', 'blinkit', 'firstclub', 'milkbasket', 'delightful gourmet', 'freshtohome', 'bigbasket', 'jiomart', 'reliance retail', 'reliance payment', 'grocer', 'fresh to home', 'kirana', 'lulu international shopbangalore', 'lulu international sho bangalore in', 'lulu international sho', 'gramiyaa', 'rsp*gramiyaa', 'food on farm', 'gulab oils', 'foodonfarmpickles', 'rsp*blink commerce pvt', 'iap blink commerce pvt ltd bangalore', 'blink commerce pvt ltd bangalore in', 'big basket', 'superhe', 'star bazaar', 'venkateswara co', 'instamart', 'rel retail', 'sm grand mart', 'namdhari', 'ratnadeep', 'fortune hyper'],
    'Food Delivery': ['swiggy', 'zomato', 'dunzo'],
    'Food & Dining': ['restaurant', 'kfc', 'mcdonald', 'starbucks', 'taco bell', 'hungerbox', 'pizza', 'burger', 'cafe', 'anand sweets', 'la sunila', 'sukho thai', 'khansaheb', 'empire restaurant', 'samosaparty', 'pearltri food', 'tara s', 'kevala', 'smoor', 'simplifyfoods', 'mannash', 'ishaara', 'tru native', 'godavari vantillu', 'meenaks', 'olive', 'chanda nagar', 'anand s', 'sea poi', 'eatgood technologiebangalore', 'press to', 'milano ice cream', 'sb/starbuc/paymen', 'chutnefy', 'jst snacks 4861424 bengaluru in', 'mahendra 5ivepillars l', 'barbeque nation', 'seven turns', 'bhartiya jalpan', 'nagas', 'mc donalds', 'boba tree', 'le kene', 'onezo', 'zealofo', 'pearltrifoods', 'p761 ph'],
    'Medical': ['apollo', 'medplus', 'pharmacy', 'hospital', 'dental', 'clinic', 'ebisu eye', 'ramdev medical', 'raam medical', 'rays ph', 'mosaic wellness', 'manmatters', 'kumon', 'superhealth', 'qikwell', 'asian instiute of gas', 'balaji heart center r', 'ram medicals', 'bharat vikas generic m', 'tata 1mg healthcare so', 'tatvartha health', 'aarthi scans pvt ltd', 'clinikally digital hea', 'pharmeasy inmumbai', '1mghealthcaresolutionspgurgaon', 'sri maruthi pharma', 'paediatrix', 'add on scans', 'house of vision'],
    'Education': ['nps trust', 'school', 'tuition', 'growthx', 'simpliaxis', 'national public sc', 'npsitplpune', 'rezi resume builder', 'insaid', 'babbel'],
    'Shopping': ['amazon', 'flipkart', 'myntra', 'nykaa', 'lulu international', 'fab india', 'factory outlet', 'svastik', 'portronics', 'skadoosh', 'silverz', 'dailyobject', 'ashi colour', 'kashaya', 'innovative retail', 'reliance digital', 'apple india', 'eureka forbes', 'uniqlo', 'lifelong', 'deena uniform', 'zudio a unit of trent', 'gokwik', 'brand drops', 'inayaaccessories', 'adret retail private l', 'prestige', 'hennes n mauritzbengaluru', 'worldofasaya', 'rsp*printo document s', 'aditya birla fashion anmumbai', 'toyota kirloskar motor', 'ind*vistaprint', 'bibliophiles', 'life style internationabengaluru', 'handpickd', 'mr diy', 'croma', 'diverse retails', 'hamleys', 'gyftr', 'flipkart internet', 'ikea', 'tuibinz', 'showoffff', 'aditya birla fashion', 'soch apparels', 'decathlon', 'ventota retail', 'flipkart payments', 'fabindia', 'nkp empire', 'miniso', 'kk oasis'],
    'Jewellery': ['jeweller', 'thangamaligai', 'ramala', 'malabar', 'tanishq', 'kalyan jewell', 'joyalukkas', 'senco gold', 'pc jeweller', 'bluestone', 'caratlane', 'c puttaiah and sons', 'sparkle gold', 'shree sai gold'],
    'Health & Wellness': ['kashayam ayurveda', 'body craft', 'chillchemy', 'o2 spa', 'vlcc', 'reboot', 'ultrahuman', 'sushrut ayurved', 'nutrition', 'tru native f&b private', 'razorpay*bright nutric', 'health and happine', 'happylab solutions pvt', 'traya health', 'herbs nutriproducts pr', 'rsp*fitshit health so', 'naturaltein lng privat', 'satiya nutraceutica', 'curefit', 'aeronutrix sports', 'avimee herbal private', 'helios lifestyle pvt l', 'herbaceous'],
    'Transport': ['uber', 'ola', 'parking', 'metro', 'cab', 'park plus', 'fastag', 'm s nexus hyderabad re 356', 'm s nexus hyderabad re', 'nexus koramangala', 'jsp auto core'],
    'Travel': ['emirates', 'etihad', 'indigo', 'vistara', 'air india', 'spicejet', 'akasa air', 'oebb', 'wien ticket', 'jetpac', 'vfs global', 'mmt hotel', 'makemytrip', 'goibibo', 'cleartrip', 'yatra', 'redbus', 'flixbus'],
    'Bills & Utilities': ['airtel', 'vodafone', 'vil pay', 'electricity', 'broadband', 'tata sky', 'cred', 'credpay', 'airtel in si', 'www airtel', 'spaybbps 9004201229 banga', 'bharat billpayment 512602431859', 'bharat billpayment 509706133632', 'bharat billpayment 503604408941', 'gateway security'],
    'Subscriptions': ['netflix', 'hotstar', 'spotify', 'amazon prime', 'mflxnl', 'dream11', 'dreampl', 'alleven', 'uni seo', 'directo', 'claude.ai', 'anthropic', 'openai *chatgpt subscr', 'openai', 'rechargevali', 'bundl technologiesbengaluru', 'rsp*eenadu television', 'googlecloud', 'ind*linkedin', 'upwork', 'emudhra'],
    'Personal Care': ['loreal', 'hair', 'salon', 'grooming', 'foxtale', 'tru derma pvt ltd', 'youstabengaluru', 'twistylocks', 'seoulskin', 'mytrident', 'minimalist', 'fsn ecommerce'],
    'Household Help': ['urbanclap'],
    'Entertainment': ['movie', 'pvr', 'inox', 'district', 'game', 'grips go karting and b', 'viacom18 media private', 'paul john'],
    'Kids': ['firstcry', 'ignited brain', 'ms kids', 'cuddly'],
    'Insurance': ['acko', 'lic', 'insurance', 'star health', 'starhealth', 'pramerica'],
    'Investments': ['nps trust', 'mutual fund', 'sip', 'groww', 'smallcase', 'npsitpl'],
    'Rent': ['rentenpe', 'rentpe', 'nobroker', 'housing.com rent', 'magicbricks rent'],
    'Fees & Charges': ['joining fee', 'annual fee', 'igst', 'surcharge', 'fuel surcharge', 'forex markup', 'rent surcharge', 'igst @', 'forex mark-up fee retail dcc', 'redemption fee - cp002279270', 'processing fee #1965508', 'cbdt'],
    'Donations': ['bharat vikas parishad', 'impact guru'],
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
