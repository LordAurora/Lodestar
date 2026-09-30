# Bookshelf

A tiny book-lending service used as a demo repository for Lodestar.

- `bookshelf/` – Python API: users, authentication, books and loans.
- `web/` – TypeScript client used by the browser front end.
- `worker/` – Go worker that sends overdue-loan reminder emails.

## What Insights finds here

Open this repository in Lodestar, run **Re-analyze**, and the Insights pages have something to show:

- **Duplicates:** `overdue_report` and `long_loans_report` in `bookshelf/reports.py` are near-duplicates.
- **Docstrings:** the functions in `reports.py` have no docstrings, so the Docstrings page lists them.
- **Impact:** changing `late_fee` reaches `overdue_report`, then `fees_for_user` and `month_summary`, and the tests in `tests/test_reports.py`.
- **Config, Tech debt, API endpoints, Diagrams:** environment variables in `bookshelf/config.py`, the `TODO`/`FIXME` comments, the FastAPI and Express routes, and the module graph.
