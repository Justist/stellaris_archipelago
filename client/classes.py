from dataclasses import dataclass, field
from typing import List, Optional, Tuple

VALID_AREAS : Tuple[str, ...] = ("physics", "society", "engineering")

# Not frozen: we need TechEntry to be mutable for randomization, and unfrozen cannot inherit from frozen.
@dataclass(order=True, slots=True)
class BaseTech:
    key: str
    tier: int
    area: str
    cost: int = -1  # -1 indicates unknown cost
    prerequisites: List[str] = field(default_factory=list, compare=False)
    
    @property
    def has_known_cost(self) -> bool:
        return self.cost >= 0
    
    def __post_init__(self):
        if self.area not in VALID_AREAS:
            raise ValueError(f"Invalid area '{self.area}' for tech '{self.key}'. Valid areas: {VALID_AREAS}")
        if not 0 <= self.tier <= 5:
            raise ValueError(f"Tier must be between 0 and 5 for tech '{self.key}'. Got: {self.tier}")

@dataclass(order=True, slots=True)
class TechEntry(BaseTech):
    name: str = ""
    category: str = ""
    is_start_tech: bool = False
    is_rare: bool = False
    is_dangerous: bool = False
    dlc: Optional[str] = None
    source_file: str = ""
    randomize: bool = False