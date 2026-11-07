from dataclasses import dataclass, field


@dataclass
class Order:
    """An order placed by a user."""
    id: int
    user_id: int
    items: list = field(default_factory=list)
    total: float = 0.0
