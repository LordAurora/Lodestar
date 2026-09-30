from datetime import date, timedelta
from types import SimpleNamespace

from bookshelf.loans import late_fee
from bookshelf.reports import fees_for_user, overdue_report


def loan(user_id, days_late):
    today = date(2026, 3, 1)
    return SimpleNamespace(
        user_id=user_id,
        book_id=7,
        returned_on=None,
        due_on=today - timedelta(days=days_late),
        borrowed_on=today - timedelta(days=days_late + 14),
    )


def test_late_fee_caps_at_twenty():
    assert late_fee(400) == 20.0


def test_overdue_report_lists_late_loans():
    text = overdue_report([loan(1, 3)], date(2026, 3, 1))
    assert "3 days late" in text


def test_fees_for_user_only_counts_that_user():
    text = fees_for_user([loan(1, 3), loan(2, 9)], 1, date(2026, 3, 1))
    assert "9 days late" not in text
