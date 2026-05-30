"""Optional Claude-assisted category suggestions.

Entirely opt-in. If ANTHROPIC_API_KEY is not set, :func:`is_enabled` returns
False and nothing here is ever called. The `anthropic` package is imported
lazily inside the call so the module imports cleanly even when it isn't
installed. The API key is read from the environment by the SDK — it is never
logged or printed.
"""

import os
import re
import json
import logging

logger = logging.getLogger("spendlens.ai")

MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 50


def is_enabled():
    """True only if an Anthropic API key is present in the environment."""
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def suggest_categories(merchants, categories):
    """Return [{description, category, confidence}] for a batch of merchants.

    Fails silently (returns []) on any error — a missing key, missing package,
    network failure, or unparseable response — so the caller degrades to manual
    categorization. The API key is never logged.
    """
    if not merchants or not is_enabled():
        return []

    try:
        import anthropic  # lazy import — only needed when the key is set
    except Exception as exc:  # package not installed
        logger.error("anthropic package unavailable: %s", exc)
        return []

    prompt = f"""Categorize these Indian credit card merchant names.

Categories: {', '.join(categories)}

Merchants (one per line):
{chr(10).join(merchants)}

Rules:
- Fuel stations, HP/HPCL/Shell → Fuel
- Zepto/Blinkit/grocery apps → Groceries
- Zomato/Swiggy → Food Delivery
- Clinics/pharmacies/hospitals → Medical
- Return ONLY valid JSON array, no explanation

Format: [{{"description": "merchant", "category": "category", "confidence": 0.85}}]"""

    try:
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        message = client.messages.create(
            model=MODEL,
            max_tokens=8192,  # 1024 truncated 50-merchant JSON arrays → parse errors
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text
        raw = (text or "").strip()
        # Strip markdown code fences Claude sometimes adds.
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```\s*$', '', raw, flags=re.MULTILINE)
        raw = raw.strip()
        if not raw:
            logger.warning("Empty response from Claude API — skipping batch")
            return []
        data = json.loads(raw)
        return data
    except Exception as exc:
        # Never surface the key; log only the error type/message.
        logger.error("Claude suggestion call failed: %s", exc)
        return []


def suggest_for_transactions(transactions, categories):
    """Batch transactions (by unique description) and return id→suggestion map.

    `transactions` is a list of {id, description} dicts (typically the
    rule-uncategorized rows). Returns {id: {"category", "confidence"}}.
    """
    if not transactions or not is_enabled():
        return {}

    # Map unique descriptions to the ids that share them (dedupe API work).
    desc_to_ids = {}
    for t in transactions:
        desc = (t.get("description") or "").strip()
        if not desc:
            continue
        desc_to_ids.setdefault(desc, []).append(t["id"])

    unique_descs = list(desc_to_ids.keys())
    result = {}
    for start in range(0, len(unique_descs), BATCH_SIZE):
        batch = unique_descs[start:start + BATCH_SIZE]
        suggestions = suggest_categories(batch, categories)
        # Build a case-insensitive lookup from the model's response.
        by_desc = {}
        for s in suggestions if isinstance(suggestions, list) else []:
            try:
                d = (s.get("description") or "").strip().lower()
                cat = s.get("category")
                conf = float(s.get("confidence", 0.0) or 0.0)
                if d and cat in categories:
                    by_desc[d] = {"category": cat, "confidence": max(0.0, min(conf, 1.0))}
            except Exception:
                continue
        for desc in batch:
            sug = by_desc.get(desc.lower())
            if not sug:
                continue
            for tid in desc_to_ids[desc]:
                result[tid] = sug
    return result
