"""HTTP API (framework-free request handlers for the demo)."""

from bookshelf.auth import AuthError, hash_password, login, verify_token
from bookshelf.config import settings
from bookshelf.loans import LoanError, borrow_book
from bookshelf.storage import BookRepository, LoanRepository, UserRepository, connect

conn = connect(settings.database_path)
users, books, loans = UserRepository(conn), BookRepository(conn), LoanRepository(conn)


def current_user_id(headers: dict) -> int:
    """Read the ``Authorization: Bearer <token>`` header and return the user id."""
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise AuthError("Missing bearer token")
    return verify_token(auth.removeprefix("Bearer "))


def handle_register(body: dict) -> dict:
    user = users.create(body["email"], body["name"], hash_password(body["password"]))
    return {"status": 201, "id": user.id}


def handle_login(body: dict) -> dict:
    try:
        return {"status": 200, "token": login(users, body["email"], body["password"])}
    except AuthError as exc:
        return {"status": 401, "error": str(exc)}


def handle_search(query: dict) -> dict:
    return {"status": 200, "books": [b.__dict__ for b in books.search(query.get("q", ""))]}


def handle_borrow(headers: dict, body: dict) -> dict:
    try:
        user_id = current_user_id(headers)
        loan_id = borrow_book(books, loans, user_id, int(body["book_id"]))
        conn.commit()
        return {"status": 201, "loan_id": loan_id}
    except AuthError as exc:
        return {"status": 401, "error": str(exc)}
    except LoanError as exc:
        return {"status": 409, "error": str(exc)}
