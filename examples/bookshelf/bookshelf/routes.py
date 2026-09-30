"""HTTP routes for Bookshelf, written FastAPI-style. Each route hands off to bookshelf.api."""

from fastapi import APIRouter, FastAPI

from bookshelf import api

app = FastAPI(title="Bookshelf")
router = APIRouter(prefix="/books")


@router.get("/")
def search_books(q: str = ""):
    """Search the catalogue by title or author."""
    return api.handle_search({"q": q})


@router.post("/{book_id}/borrow")
def borrow(book_id: int, authorization: str = ""):
    return api.handle_borrow({"Authorization": authorization}, {"book_id": book_id})


@app.post("/login")
def login(body: dict):
    return api.handle_login(body)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(router, prefix="/api")
