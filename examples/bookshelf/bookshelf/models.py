"""Domain models."""

from dataclasses import dataclass
from datetime import date


@dataclass
class User:
    id: int
    email: str
    name: str
    password_hash: str
    is_admin: bool = False


@dataclass
class Book:
    id: int
    title: str
    author: str
    isbn: str
    copies_total: int
    copies_available: int


@dataclass
class Loan:
    id: int
    user_id: int
    book_id: int
    borrowed_on: date
    due_on: date
    returned_on: date | None = None

    @property
    def is_overdue(self) -> bool:
        return self.returned_on is None and self.due_on < date.today()
