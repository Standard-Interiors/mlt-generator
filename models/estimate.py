from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class ProjectInfo:
    customer_name: str = ""
    project_name: str = ""
    address: str = ""
    quote_number: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class EstimateMaterial:
    product_code: str = ""
    location: str = ""
    vendor: str = ""
    selection: str = ""
    color: str = ""
    size: str = ""
    thickness: str = ""
    grout_color: str = "N/A"
    grout_joint_size: str = "N/A"
    adhesive: str = ""
    install_type: str = ""
    install_pattern: str = ""
    quantity: float = 0.0
    unit: str = "SF"
    dollar_amount: float = 0.0
    notes: str = ""
    section: str = ""
    box_qty: float = 0.0
    box_qty_unit: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EstimateMaterial":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class ParsedEstimate:
    project: ProjectInfo = field(default_factory=ProjectInfo)
    materials: list[EstimateMaterial] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self):
        return {
            "project": self.project.to_dict(),
            "materials": [m.to_dict() for m in self.materials],
        }
