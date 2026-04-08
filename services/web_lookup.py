import re
import json
import requests
from bs4 import BeautifulSoup
import anthropic
import config

# In-memory caches
_url_cache: dict[str, str | None] = {}
_box_qty_cache: dict[str, dict | None] = {}

# Known vendor domain patterns for prioritizing search results
VENDOR_DOMAINS = {
    "shaw": "shawcontract.com",
    "mohawk": "mohawkflooring.com",
    "mannington": "manningtoncommercial.com",
    "daltile": "daltile.com",
    "bedrosians": "bedrosians.com",
    "karndean": "karndeancommercial.com",
    "tarkett": "tarkett.com",
    "armstrong": "armstrongflooring.com",
    "interface": "interface.com",
    "ecore": "ecoreathletic.com",
    "parador": "parador.de",
    "tilebar": "tilebar.com",
    "emser": "emser.com",
    "arizona tile": "arizonatile.com",
    "floor and decor": "flooranddecor.com",
    "floor & decor": "flooranddecor.com",
    "cobalt": "cobaltsurfaces.com",
    "mats inc": "matsinc.com",
    "encore": "encorecatalog.com",
    "villa": "flooranddecor.com",
    "genrose": "genrose.com",
    "dune": "duneceramics.com",
    "garden state": "gstile.com",
    "amtico": "amtico.com",
}


def _normalize_key(vendor: str, selection: str, color: str) -> str:
    return f"{vendor}|{selection}|{color}".lower().strip()


def lookup_product_url(vendor: str, selection: str, color: str) -> str | None:
    """Search for a product's data page URL.

    Args:
        vendor: Manufacturer name
        selection: Product name/line
        color: Color name

    Returns:
        URL string or None if not found
    """
    if not vendor or vendor.upper() == "TBD":
        return None

    key = _normalize_key(vendor, selection, color)
    if key in _url_cache:
        return _url_cache[key]

    url = _search_bing(vendor, selection, color)
    _url_cache[key] = url
    return url


def _search_bing(vendor: str, selection: str, color: str) -> str | None:
    """Search Google for a product page URL (with DuckDuckGo fallback)."""
    query = f"{vendor} {selection} {color} flooring product data"

    try:
        from googlesearch import search as gsearch
        all_urls = list(gsearch(query, num_results=5, lang="en"))
    except Exception:
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
                all_urls = [r.get("href", "") or r.get("link", "") for r in results if r.get("href") or r.get("link")]
        except Exception:
            return None

    vendor_lower = vendor.lower()
    preferred_domain = None
    for v_key, domain in VENDOR_DOMAINS.items():
        if v_key in vendor_lower:
            preferred_domain = domain
            break

    if preferred_domain:
        for url in all_urls:
            if preferred_domain in url:
                return url

    for url in all_urls:
        if any(domain in url for domain in VENDOR_DOMAINS.values()):
            return url

    return all_urls[0] if all_urls else None


# ──────────────────────────────────────
# BOX QUANTITY LOOKUP
# ──────────────────────────────────────

BOX_QTY_PROMPT = """You extract product packaging data from flooring product web pages.
Given the page text, find the box/carton/roll quantity — how many square feet (SF) or square yards (SY) come in one box, carton, or roll.

Look for phrases like:
- "XX SF per box", "XX sq ft per carton", "XX SF/ctn", "XX sf/box"
- "XX SY per roll", "XX sq yd per carton", "XX SY/roll"
- "Coverage: XX SF", "Box contains XX SF"
- "Carton: XX sq ft", "XX sq. ft. per carton"
- "XX SF per piece" (for large format tiles or planks sold individually)
- "Pieces per box: X" combined with piece size (calculate total SF per box)
- For broadloom carpet: look for "XX SY per roll" or roll size in linear feet x width

If you find pieces-per-box and individual piece size, calculate: pieces × (L × W) / 144 = SF per box.

Return ONLY valid JSON: {"box_qty": 17.6, "unit": "SF"}
If the page shows SY: {"box_qty": 8.0, "unit": "SY"}
If you cannot find the information, return: {"box_qty": 0, "unit": ""}
No other text, just the JSON."""


def lookup_box_quantity(vendor: str, selection: str, color: str, size: str = "") -> dict | None:
    """Search online for a product's box/carton quantity.

    Args:
        vendor: Manufacturer name
        selection: Product name/line
        color: Color name
        size: Material size (helps narrow search)

    Returns:
        Dict with box_qty, unit, source_url or None
    """
    if not vendor or vendor.upper() == "TBD":
        return None

    key = _normalize_key(vendor, selection, color)
    if key in _box_qty_cache:
        return _box_qty_cache[key]

    # Search for product spec data
    urls = _search_google_multi(vendor, selection, color, size)
    if not urls:
        _box_qty_cache[key] = None
        return None

    # Try each URL until we find box qty data
    for url in urls[:3]:
        page_text = _fetch_page_text(url)
        if not page_text or len(page_text) < 50:
            continue

        result = _extract_box_qty_ai(page_text, vendor, selection)
        if result and result.get("box_qty", 0) > 0:
            result["source_url"] = url
            _box_qty_cache[key] = result
            return result

    _box_qty_cache[key] = None
    return None


def _search_google_multi(vendor: str, selection: str, color: str, size: str = "") -> list[str]:
    """Search Google for product page URLs using googlesearch-python."""
    query = f"{vendor} {selection} spec sheet square feet per box carton"

    try:
        from googlesearch import search as gsearch
        all_urls = list(gsearch(query, num_results=10, lang="en"))
    except Exception:
        # Fallback to DuckDuckGo
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=10))
                all_urls = [r.get("href", "") or r.get("link", "") for r in results if r.get("href") or r.get("link")]
        except Exception:
            return []

    # Filter and prioritize by vendor domain
    vendor_lower = vendor.lower()
    preferred_domain = None
    for v_key, domain in VENDOR_DOMAINS.items():
        if v_key in vendor_lower:
            preferred_domain = domain
            break

    good_urls = []
    other_urls = []
    for url in all_urls:
        if not url or any(skip in url for skip in ["google.com", "bing.com", "microsoft.com"]):
            continue
        if preferred_domain and preferred_domain in url:
            good_urls.append(url)
        elif any(domain in url for domain in VENDOR_DOMAINS.values()):
            other_urls.append(url)
        elif any(kw in url.lower() for kw in ["spec", "product", "catalog", "data"]):
            other_urls.append(url)

    return (good_urls + other_urls)[:5]


def _fetch_page_text(url: str) -> str | None:
    """Fetch a URL and extract readable text content."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove script, style, nav elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)

        # Truncate to keep API costs down (first 4000 chars usually has specs)
        return text[:4000] if text else None

    except Exception:
        return None


def _extract_box_qty_ai(page_text: str, vendor: str, selection: str) -> dict | None:
    """Use Claude Haiku to extract box quantity from page text."""
    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=BOX_QTY_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Product: {vendor} {selection}\n\nPage text:\n{page_text}",
                },
            ],
            temperature=0.0,
        )

        result_text = response.content[0].text.strip()

        # Strip markdown fences if present
        if result_text.startswith("```"):
            lines = result_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            result_text = "\n".join(lines).strip()

        data = json.loads(result_text)
        if data.get("box_qty", 0) > 0:
            return {"box_qty": float(data["box_qty"]), "unit": data.get("unit", "SF")}
        return None

    except Exception:
        return None
