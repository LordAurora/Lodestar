// Typed client for the Bookshelf HTTP API, used by the browser front end.

export interface Book {
  id: number;
  title: string;
  author: string;
  isbn: string;
  copiesAvailable: number;
}

const TOKEN_KEY = "bookshelf.token";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function saveToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`/api${path}`, { ...init, headers });
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 401) clearToken();
    throw new ApiError(res.status, data.error ?? "Request failed");
  }
  return data as T;
}

export async function signIn(email: string, password: string): Promise<void> {
  const { token } = await request<{ token: string }>("/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  saveToken(token);
}

export const searchBooks = (q: string) =>
  request<{ books: Book[] }>(`/books?q=${encodeURIComponent(q)}`).then((r) => r.books);

export const borrow = (bookId: number) =>
  request<{ loan_id: number }>("/loans", {
    method: "POST",
    body: JSON.stringify({ book_id: bookId }),
  });
