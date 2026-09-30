"""Reports for librarians: who is late and what is popular."""

from bookshelf.loans import late_fee


def overdue_report(loans, today):
    lines = []
    total = 0.0
    for loan in loans:
        if loan.returned_on is None and loan.due_on < today:
            days = (today - loan.due_on).days
            fee = late_fee(days)
            total += fee
            lines.append(f"{loan.book_id}: {days} days late, fee {fee:.2f}")
    lines.append(f"total fees: {total:.2f}")
    return "\n".join(lines)


def popular_report(loans, since):
    lines = []
    total = 0
    for loan in loans:
        if loan.returned_on is not None and loan.borrowed_on >= since:
            days = (loan.returned_on - loan.borrowed_on).days
            total += days
            lines.append(f"{loan.book_id}: kept {days} days, lent on {loan.borrowed_on}")
    lines.append(f"total days lent: {total}")
    return "\n".join(lines)


def fees_for_user(loans, user_id, today):
    mine = [loan for loan in loans if loan.user_id == user_id]
    return overdue_report(mine, today)


def month_summary(loans, today):
    late = overdue_report(loans, today)
    popular = popular_report(loans, today.replace(day=1))
    return late + "\n\n" + popular


def clamp_days(days):
    return max(0, min(days, 365))
