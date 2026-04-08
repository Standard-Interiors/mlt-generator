import re
from models.estimate import EstimateMaterial
from models.finish_schedule import (
    FinishScheduleEntry, RoomFinishAssignment, Discrepancy, CrossRefResult
)

# Known aliases (codes that may differ between estimate and plans)
CODE_ALIASES = {
    "LVP-U1": "LVT-U1",
    "LVT-U1": "LVP-U1",
}


def normalize_code(code: str) -> str:
    """Normalize a finish code for comparison."""
    return code.strip().upper().replace(" ", "")


def fuzzy_match(a: str, b: str) -> bool:
    """Check if two strings are fuzzy-equal (case-insensitive, stripped)."""
    if not a or not b:
        return False
    a_norm = re.sub(r'[^a-z0-9]', '', a.lower())
    b_norm = re.sub(r'[^a-z0-9]', '', b.lower())

    # Exact match after normalization
    if a_norm == b_norm:
        return True

    # One contains the other
    if a_norm in b_norm or b_norm in a_norm:
        return True

    # Token overlap
    a_tokens = set(a.lower().split())
    b_tokens = set(b.lower().split())
    if a_tokens and b_tokens:
        overlap = len(a_tokens & b_tokens)
        total = max(len(a_tokens), len(b_tokens))
        if overlap / total >= 0.5:
            return True

    return False


def cross_reference(
    estimate_materials: list[EstimateMaterial],
    material_definitions: list[dict],
    room_assignments: list[dict],
    material_legends: dict,
) -> CrossRefResult:
    """Compare estimate materials against plans finish schedule data.

    Args:
        estimate_materials: Parsed materials from the estimate
        material_definitions: FinishScheduleEntry dicts from plans
        room_assignments: RoomFinishAssignment dicts from plans
        material_legends: Code -> description mapping from plans

    Returns:
        CrossRefResult with matches, discrepancies, estimate_only, plans_only
    """
    # Build estimate map: normalized code -> material
    estimate_map: dict[str, EstimateMaterial] = {}
    for mat in estimate_materials:
        code = normalize_code(mat.product_code)
        if code:
            estimate_map[code] = mat

    # Build plans material map: normalized code -> definition
    plans_mat_map: dict[str, dict] = {}
    for md in material_definitions:
        code = normalize_code(md.get("code", ""))
        if code:
            plans_mat_map[code] = md

    # Build plans room map: normalized floor code -> list of rooms
    plans_room_map: dict[str, list[str]] = {}
    all_plan_floor_codes = set()
    for ra in room_assignments:
        floor_code_raw = ra.get("floor_code", "")
        # Handle multiple codes like "V-06 / C-05"
        codes = re.split(r'[/,;]', floor_code_raw)
        for code_str in codes:
            code = normalize_code(code_str)
            if code and len(code) >= 2:
                all_plan_floor_codes.add(code)
                room_label = ra.get("room_name", "") or ra.get("room_number", "")
                if ra.get("room_number"):
                    room_label = f"{ra.get('room_name', '')} #{ra['room_number']}"
                if code not in plans_room_map:
                    plans_room_map[code] = []
                plans_room_map[code].append(room_label)

    # Also add legend codes to the plans set
    for code_raw in material_legends:
        code = normalize_code(code_raw)
        if code:
            all_plan_floor_codes.add(code)

    # Get all unique codes from both sources
    all_estimate_codes = set(estimate_map.keys())
    all_plans_codes = all_plan_floor_codes | set(plans_mat_map.keys())

    # Handle aliases
    def resolve_alias(code: str) -> str:
        return CODE_ALIASES.get(code, code)

    result = CrossRefResult()

    # Check estimate codes against plans
    matched_plans_codes = set()
    for est_code in sorted(all_estimate_codes):
        est_mat = estimate_map[est_code]

        # Try direct match or alias match
        plans_code = None
        if est_code in all_plans_codes:
            plans_code = est_code
        elif resolve_alias(est_code) in all_plans_codes:
            plans_code = resolve_alias(est_code)

        if plans_code:
            matched_plans_codes.add(plans_code)
            if resolve_alias(est_code) != est_code:
                matched_plans_codes.add(resolve_alias(est_code))

            # Found in both - compare details
            plans_def = plans_mat_map.get(plans_code, {})
            rooms = plans_room_map.get(plans_code, [])

            # Also check alias rooms
            if resolve_alias(plans_code) in plans_room_map:
                rooms = rooms + plans_room_map[resolve_alias(plans_code)]

            disc = Discrepancy(
                finish_code=est_mat.product_code,
                estimate_vendor=est_mat.vendor,
                estimate_product=est_mat.selection,
                estimate_color=est_mat.color,
                estimate_size=est_mat.size,
                plans_manufacturer=plans_def.get("manufacturer", ""),
                plans_product=plans_def.get("product", ""),
                plans_color=plans_def.get("color", ""),
                plans_dimensions=plans_def.get("dimensions", ""),
                rooms=list(set(rooms))[:20],  # Limit room list
            )

            # Compare fields if we have plans material data
            if plans_def:
                field_diffs = {}
                if not fuzzy_match(est_mat.vendor, plans_def.get("manufacturer", "")):
                    if plans_def.get("manufacturer"):
                        field_diffs["vendor"] = (est_mat.vendor, plans_def["manufacturer"])

                if not fuzzy_match(est_mat.selection, plans_def.get("product", "")):
                    if plans_def.get("product"):
                        field_diffs["product"] = (est_mat.selection, plans_def["product"])

                if not fuzzy_match(est_mat.color, plans_def.get("color", "")):
                    if plans_def.get("color"):
                        field_diffs["color"] = (est_mat.color, plans_def["color"])

                if not fuzzy_match(est_mat.size, plans_def.get("dimensions", "")):
                    if plans_def.get("dimensions"):
                        field_diffs["size"] = (est_mat.size, plans_def["dimensions"])

                disc.field_diffs = field_diffs

                if field_diffs:
                    disc.status = "DISCREPANCY"
                    diff_fields = ", ".join(field_diffs.keys())
                    disc.notes = f"Differs in: {diff_fields}"
                    result.discrepancies.append(disc)
                else:
                    disc.status = "MATCH"
                    result.matches.append(disc)
            else:
                # Code found in room assignments but no material definition to compare
                disc.status = "MATCH"
                disc.notes = "Code found in plans (room assignments) but no detailed material spec to compare"
                result.matches.append(disc)
        else:
            # Estimate only
            disc = Discrepancy(
                finish_code=est_mat.product_code,
                status="ESTIMATE_ONLY",
                estimate_vendor=est_mat.vendor,
                estimate_product=est_mat.selection,
                estimate_color=est_mat.color,
                estimate_size=est_mat.size,
                notes="In estimate but not found in plans - verify scope",
            )
            result.estimate_only.append(disc)

    # Check plans codes not in estimate
    for plans_code in sorted(all_plans_codes - matched_plans_codes):
        # Skip non-flooring codes (paint, wallcovering, etc.)
        if not _is_flooring_code(plans_code):
            continue

        plans_def = plans_mat_map.get(plans_code, {})
        rooms = plans_room_map.get(plans_code, [])
        legend_desc = material_legends.get(plans_code, "")

        disc = Discrepancy(
            finish_code=plans_code,
            status="PLANS_ONLY",
            plans_manufacturer=plans_def.get("manufacturer", ""),
            plans_product=plans_def.get("product", "") or legend_desc,
            plans_color=plans_def.get("color", ""),
            plans_dimensions=plans_def.get("dimensions", ""),
            rooms=list(set(rooms))[:20],
            notes="In plans but NOT in estimate - may need to be added to scope",
        )
        result.plans_only.append(disc)

    return result


def _is_flooring_code(code: str) -> bool:
    """Check if a finish code is a flooring code we care about."""
    flooring_prefixes = [
        'CPT', 'LVT', 'LVP', 'TL', 'CWT', 'SWT',
        'C-', 'T-', 'TB-', 'V-', 'RF-', 'RT-',
        'LMN-', 'WM-', 'RB-', 'EF-', 'CN-', 'SC-',
    ]
    code_upper = code.upper()
    return any(code_upper.startswith(p) for p in flooring_prefixes)
