from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class FinishScheduleEntry:
    """A material definition from the architectural finish schedule."""
    code: str = ""                  # e.g., "CPT-U1", "V-06", "T-04"
    finish_type: str = ""           # e.g., "BROADLOOM CARPET", "LVT", "PORCELAIN TILE"
    manufacturer: str = ""          # e.g., "Shaw Contract", "Bedrosians"
    product: str = ""               # e.g., "Esteem Ultraloc MB"
    color: str = ""                 # e.g., "Taupe", "Ample"
    spec_code: str = ""             # e.g., "5A373-73112", "KP5-801"
    dimensions: str = ""            # e.g., "24\" x 48\"", "7\" x 48\""
    material_description: str = ""  # e.g., "MATTE PORCELAIN"
    application_area: str = ""      # e.g., "UNIT FLOOR - BATHROOM"
    install_notes: str = ""         # e.g., "INSTALL OVER PAD PER MFR"
    source_page: int = 0            # Page number in the IFC PDF

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FinishScheduleEntry":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class RoomFinishAssignment:
    """A room's finish code assignments from the plans."""
    room_name: str = ""             # e.g., "LIVING ROOM/LOUNGE"
    room_number: str = ""           # e.g., "015"
    floor_code: str = ""            # e.g., "C-01", "LMN-01"
    base_code: str = ""             # e.g., "B-01; PT-03", "RB-01"
    wall_code: str = ""             # e.g., "PT-02"
    millwork_code: str = ""
    countertop_code: str = ""
    ceiling_code: str = ""
    level: str = ""                 # e.g., "FIRST", "SECOND"
    source_page: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RoomFinishAssignment":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class Discrepancy:
    """A comparison result between estimate and plans for a single finish code."""
    finish_code: str = ""
    status: str = ""                # MATCH, DISCREPANCY, ESTIMATE_ONLY, PLANS_ONLY
    estimate_vendor: str = ""
    estimate_product: str = ""
    estimate_color: str = ""
    estimate_size: str = ""
    plans_manufacturer: str = ""
    plans_product: str = ""
    plans_color: str = ""
    plans_dimensions: str = ""
    rooms: list = field(default_factory=list)  # Rooms using this code
    field_diffs: dict = field(default_factory=dict)  # field -> (estimate_val, plans_val)
    notes: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class CrossRefResult:
    """Full cross-reference comparison result."""
    matches: list[Discrepancy] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    estimate_only: list[Discrepancy] = field(default_factory=list)
    plans_only: list[Discrepancy] = field(default_factory=list)

    def to_dict(self):
        return {
            "matches": [d.to_dict() for d in self.matches],
            "discrepancies": [d.to_dict() for d in self.discrepancies],
            "estimate_only": [d.to_dict() for d in self.estimate_only],
            "plans_only": [d.to_dict() for d in self.plans_only],
            "summary": {
                "total_codes": len(self.matches) + len(self.discrepancies) + len(self.estimate_only) + len(self.plans_only),
                "matched": len(self.matches),
                "discrepancies": len(self.discrepancies),
                "estimate_only": len(self.estimate_only),
                "plans_only": len(self.plans_only),
            }
        }
