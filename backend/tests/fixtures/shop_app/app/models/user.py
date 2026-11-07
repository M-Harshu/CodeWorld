from dataclasses import dataclass


@dataclass
class User:
    """A registered shop customer."""
    id: int
    email: str
    password_hash: str
    is_admin: bool = False
