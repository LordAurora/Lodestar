"""Lending rules: borrowing and returning books."""

from datetime import date, timedelta

from bookshelf.config import settings
from bookshelf.storage import BookRepository, LoanRepository


class LoanError(Exception):
    """Raised when a loan is not allowed."""


def borrow_book(books: BookRepository, loans: LoanRepository, user_id: int, book_id: int) -> int:
    """Lend a book to a user.

    A user may hold at most ``max_active_loans`` books at once, and a book can
    only be borrowed while it has an available copy. The loan is due after
    ``loan_period_days`` days.
    """
    if len(loans.active_for_user(user_id)) >= settings.max_active_loans:
        raise LoanError("Loan limit reached")
    book = books.get(book_id)
    if book is None:
        raise LoanError("No such book")
    if book.copies_available <= 0:
        raise LoanError("No copies available")
    today = date.today()
    due = today + timedelta(days=settings.loan_period_days)
    loan_id = loans.create(user_id, book_id, today, due)
    books.change_available(book_id, -1)
    return loan_id


def return_book(books: BookRepository, loans: LoanRepository, loan_id: int, book_id: int) -> None:
    """Mark a loan as returned and put the copy back on the shelf."""
    loans.mark_returned(loan_id, date.today())
    books.change_available(book_id, +1)


def late_fee(days_late: int) -> float:
    """Late fee: 0.50 per day for the first week, then 1.00 per day, capped at 20."""
    if days_late <= 0:
        return 0.0
    fee = min(days_late, 7) * 0.5 + max(days_late - 7, 0) * 1.0
    return min(fee, 20.0)
