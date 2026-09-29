"""SQLite persistence for users, books and loans."""

import sqlite3
from datetime import date

from bookshelf.models import Book, Loan, User

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT,
    password_hash TEXT, is_admin INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS books (id INTEGER PRIMARY KEY, title TEXT, author TEXT, isbn TEXT,
    copies_total INTEGER, copies_available INTEGER);
CREATE TABLE IF NOT EXISTS loans (id INTEGER PRIMARY KEY, user_id INTEGER, book_id INTEGER,
    borrowed_on TEXT, due_on TEXT, returned_on TEXT);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


class UserRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def find_by_email(self, email: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return User(**dict(row)) if row else None

    def create(self, email: str, name: str, password_hash: str) -> User:
        cur = self.conn.execute(
            "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
            (email, name, password_hash),
        )
        self.conn.commit()
        return User(cur.lastrowid, email, name, password_hash)


class BookRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def search(self, text: str) -> list[Book]:
        like = f"%{text}%"
        rows = self.conn.execute(
            "SELECT * FROM books WHERE title LIKE ? OR author LIKE ? ORDER BY title", (like, like)
        ).fetchall()
        return [Book(**dict(r)) for r in rows]

    def get(self, book_id: int) -> Book | None:
        row = self.conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return Book(**dict(row)) if row else None

    def change_available(self, book_id: int, delta: int) -> None:
        self.conn.execute(
            "UPDATE books SET copies_available = copies_available + ? WHERE id = ?",
            (delta, book_id),
        )


class LoanRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def active_for_user(self, user_id: int) -> list[Loan]:
        rows = self.conn.execute(
            "SELECT * FROM loans WHERE user_id = ? AND returned_on IS NULL", (user_id,)
        ).fetchall()
        return [_loan(r) for r in rows]

    def overdue(self, today: date) -> list[Loan]:
        rows = self.conn.execute(
            "SELECT * FROM loans WHERE returned_on IS NULL AND due_on < ?", (today.isoformat(),)
        ).fetchall()
        return [_loan(r) for r in rows]

    def create(self, user_id: int, book_id: int, borrowed_on: date, due_on: date) -> int:
        cur = self.conn.execute(
            "INSERT INTO loans (user_id, book_id, borrowed_on, due_on) VALUES (?, ?, ?, ?)",
            (user_id, book_id, borrowed_on.isoformat(), due_on.isoformat()),
        )
        return cur.lastrowid

    def mark_returned(self, loan_id: int, when: date) -> None:
        self.conn.execute(
            "UPDATE loans SET returned_on = ? WHERE id = ?", (when.isoformat(), loan_id)
        )


def _loan(row: sqlite3.Row) -> Loan:
    return Loan(
        id=row["id"],
        user_id=row["user_id"],
        book_id=row["book_id"],
        borrowed_on=date.fromisoformat(row["borrowed_on"]),
        due_on=date.fromisoformat(row["due_on"]),
        returned_on=date.fromisoformat(row["returned_on"]) if row["returned_on"] else None,
    )
