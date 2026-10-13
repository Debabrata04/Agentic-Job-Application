from dataclasses import dataclass
from typing import Optional


@dataclass
class NormalizedJob:
    """Common shape every platform adapter must produce, regardless of its native JSON structure."""

    source: str  # "greenhouse", "lever", "smartrecruiters"
    external_id: str
    company: str
    title: str
    description: Optional[str]
    location: Optional[str]
    url: Optional[str]
