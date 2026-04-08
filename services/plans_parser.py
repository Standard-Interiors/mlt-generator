import re
import json
import anthropic
from models.finish_schedule import FinishScheduleEntry, RoomFinishAssignment
from services.pdf_extractor import extract_text_pages, extract_all_page_text_fast
import config

# Regex patterns for finish codes
FINISH_CODE_PATTERN = re.compile(
    r'\b(CPT-[A-Z]?\d+|LVT-[A-Z]?\d+|LVP-[A-Z]?\d+|TL-[A-Z]?\d+|CWT-[A-Z]?\d+|'
    r'SWT-[A-Z]?\d+|C-\d+|T-\d+|TB-\d+|V-\d+|RF-\d+|RT-\d+|LMN-\d+|WM-\d+|RB-\d+|'
    r'SC-\d+|EF-\d+|CN-\d+)\b',
    re.IGNORECASE
)

# Keywords that indicate finish schedule content
HIGH_KEYWORDS = [
    'finish schedule', 'room finish schedule', 'unit finish schedule',
    'typical room finish schedule', 'finish plan'
]
MEDIUM_KEYWORDS = [
    'base moulding', 'flooring', 'wall finish', 'countertop', 'ceiling',
    'tag/type', 'manufacturer', 'series / product', 'finish / color',
    'resilient flooring', 'sealed concrete', 'broadloom carpet',
]
CALLOUT_PATTERN = re.compile(
    r'BASE\s*\n\s*FLOOR\s*\n\s*WALL\s*\n\s*MILLWORK\s*\n\s*COUNTERTOP\s*\n\s*CEILING',
    re.IGNORECASE
)


def scan_for_finish_pages(pdf_path: str) -> list[dict]:
    """Scan all pages of the IFC PDF to find finish schedule pages.

    Returns a list of candidate pages sorted by confidence score, each with:
    - page: page number (1-indexed)
    - score: confidence score (0-100)
    - page_type: classification of what kind of finish data this page has
    - title: detected sheet title
    """
    all_text = extract_all_page_text_fast(pdf_path)
    candidates = []

    for page_num, text in all_text.items():
        text_lower = text.lower()
        score = 0
        page_type = ""
        title = ""

        # Check high-confidence keywords
        for kw in HIGH_KEYWORDS:
            if kw in text_lower:
                score += 30

        # Check medium keywords
        for kw in MEDIUM_KEYWORDS:
            if kw in text_lower:
                score += 5

        # Check for finish code patterns
        codes_found = FINISH_CODE_PATTERN.findall(text)
        unique_codes = set(c.upper() for c in codes_found)
        if len(unique_codes) >= 3:
            score += 20
        elif len(unique_codes) >= 1:
            score += 5

        # Check for room finish callout format
        if CALLOUT_PATTERN.search(text):
            score += 25

        # Classify page type
        if score >= 10:
            if 'tag/type' in text_lower or ('manufacturer' in text_lower and 'series' in text_lower):
                page_type = "finish_table"
                title = "Finish Schedule Table"
            elif 'typical room finish schedule' in text_lower or ('name' in text_lower and 'base moulding' in text_lower):
                page_type = "room_schedule"
                title = "Room Finish Schedule"
            elif CALLOUT_PATTERN.search(text):
                page_type = "interior_finish_plan"
                # Try to extract sheet title
                title_match = re.search(r'(FINISH PLAN\s*-\s*[^\n]+)', text, re.IGNORECASE)
                if title_match:
                    title = title_match.group(1).strip()
                else:
                    title = "Interior Finish Plan"
            elif any(kw in text_lower for kw in ['resilient flooring', 'sealed concrete', 'carpet']):
                page_type = "composite_legend"
                title_match = re.search(r'(FINISH PLAN\s*-\s*[^\n]+)', text, re.IGNORECASE)
                if title_match:
                    title = title_match.group(1).strip()
                else:
                    title = "Composite Plan with Legend"
            else:
                page_type = "other_finish"
                title = "Finish-related page"

        if score >= 10:
            candidates.append({
                "page": page_num,
                "score": min(score, 100),
                "page_type": page_type,
                "title": title,
                "codes_found": list(unique_codes)[:10],
            })

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


PLANS_PARSE_PROMPT = """You are an architectural plans parser for a commercial flooring company. You extract structured finish schedule data from architectural drawing pages.

Given the text extracted from one or more pages of architectural plans (IFC - Issued For Construction), extract ALL flooring-related finish data.

There are several types of data to look for:

1. **Material Definitions** - Tables that define what each finish code means:
   - Code (e.g., CPT-U1, V-06, T-04, RF-06, C-01, LMN-01)
   - Finish type (e.g., BROADLOOM CARPET, LVT, PORCELAIN TILE)
   - Manufacturer (e.g., Shaw Contract, Bedrosians, Mannington)
   - Product name
   - Color/finish
   - Dimensions
   - Application area

2. **Room Assignments** - Which rooms get which finish codes:
   - Room name and number
   - Floor finish code
   - Base finish code
   - Wall finish code
   - Level/floor of the building

3. **Material Legends** - Simple code-to-description mappings found on finish plan pages:
   - e.g., "RF-06 LVT SHAW TERRAIN II ROOT 6\" X 48\""
   - e.g., "CPT-14 CARPET" (short descriptions)
   - e.g., "SC-01 SEALED CONCRETE"

IMPORTANT:
- Only extract FLOORING-related finish codes. Skip paint codes (P-01, PT-02), wallcovering (WC-), lighting (LT-), furniture, equipment, plumbing fixtures.
- The flooring codes we care about start with: CPT, LVT, LVP, TL, CWT, SWT, C-, T-, TB-, V-, RF-, RT-, LMN-, WM-, RB-, EF-, CN-, SC-
- For room assignments, focus on the FLOOR code (not wall/ceiling/base unless they are flooring-related like rubber base RB-).
- If a room has multiple floor codes (e.g., "V-06 / C-05"), list both.

Return ONLY valid JSON (no markdown fences):
{
  "material_definitions": [
    {
      "code": "...",
      "finish_type": "...",
      "manufacturer": "...",
      "product": "...",
      "color": "...",
      "dimensions": "...",
      "application_area": "...",
      "install_notes": "..."
    }
  ],
  "room_assignments": [
    {
      "room_name": "...",
      "room_number": "...",
      "floor_code": "...",
      "base_code": "...",
      "level": "..."
    }
  ],
  "material_legends": {
    "CODE": "description"
  }
}"""


def parse_finish_schedule_pages(pdf_path: str, pages: list[int]) -> dict:
    """Parse finish schedule pages using Claude AI.

    Args:
        pdf_path: Path to the IFC PDF
        pages: List of page numbers (1-indexed) to parse

    Returns:
        Dict with keys: material_definitions, room_assignments, material_legends
    """
    # Extract text from selected pages
    page_texts = extract_text_pages(pdf_path, pages)

    # Build a combined prompt with page markers
    combined_text = ""
    for page_num in sorted(page_texts.keys()):
        combined_text += f"\n\n===== PAGE {page_num} =====\n{page_texts[page_num]}"

    if not combined_text.strip():
        return {"material_definitions": [], "room_assignments": [], "material_legends": {}}

    # If combined text is very long, chunk it
    # Claude can handle ~200K tokens, but let's be reasonable
    max_chars = 150000  # ~37K tokens
    if len(combined_text) > max_chars:
        # Parse in chunks
        return _parse_in_chunks(pdf_path, pages, max_chars)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16384,
        system=PLANS_PARSE_PROMPT,
        messages=[
            {"role": "user", "content": f"Parse these finish schedule pages from architectural plans:\n{combined_text}"},
        ],
        temperature=0.1,
    )

    result_text = response.content[0].text

    # Strip markdown code fences if present
    if result_text.startswith("```"):
        lines = result_text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        result_text = "\n".join(lines)

    data = json.loads(result_text)

    # Add source page info to material definitions
    for md in data.get("material_definitions", []):
        if "source_page" not in md:
            md["source_page"] = pages[0] if pages else 0

    for ra in data.get("room_assignments", []):
        if "source_page" not in ra:
            ra["source_page"] = pages[0] if pages else 0

    return data


def _parse_in_chunks(pdf_path: str, pages: list[int], max_chars: int) -> dict:
    """Parse pages in chunks when combined text is too long."""
    page_texts = extract_text_pages(pdf_path, pages)

    all_defs = []
    all_rooms = []
    all_legends = {}

    chunk_pages = []
    chunk_size = 0

    for page_num in sorted(page_texts.keys()):
        text = page_texts[page_num]
        if chunk_size + len(text) > max_chars and chunk_pages:
            # Parse current chunk
            result = parse_finish_schedule_pages(pdf_path, chunk_pages)
            all_defs.extend(result.get("material_definitions", []))
            all_rooms.extend(result.get("room_assignments", []))
            all_legends.update(result.get("material_legends", {}))
            chunk_pages = []
            chunk_size = 0

        chunk_pages.append(page_num)
        chunk_size += len(text)

    # Parse remaining chunk
    if chunk_pages:
        result = parse_finish_schedule_pages(pdf_path, chunk_pages)
        all_defs.extend(result.get("material_definitions", []))
        all_rooms.extend(result.get("room_assignments", []))
        all_legends.update(result.get("material_legends", {}))

    return {
        "material_definitions": all_defs,
        "room_assignments": all_rooms,
        "material_legends": all_legends,
    }
