"""Go route detection: net/http, gin, chi and echo."""

from __future__ import annotations


def test_net_http(endpoints_of):
    assert endpoints_of(
        {
            "main.go": """
            package main
            import "net/http"
            func main() {
              http.HandleFunc("/health", healthHandler)
              mux := http.NewServeMux()
              mux.HandleFunc("GET /items/{id}", getItem)
              mux.Handle("/static/", fileServer)
              mux.HandleFunc("POST /items", func(w http.ResponseWriter, r *http.Request) {})
            }
            """
        }
    ) == {
        ("ANY", "/health", "healthHandler", False),
        ("GET", "/items/{id}", "getItem", False),
        ("ANY", "/static/", "fileServer", False),
        ("POST", "/items", "(inline)", False),
    }


def test_gin_groups_nest(endpoints_of):
    assert endpoints_of(
        {
            "main.go": """
            package main
            import "github.com/gin-gonic/gin"
            func main() {
              r := gin.Default()
              r.GET("/ping", ping)
              api := r.Group("/api")
              api.GET("/users", middleware.Auth(), listUsers)
              v1 := api.Group("/v1")
              v1.POST("/users", h.Create)
              r.Any("/any", anyHandler)
            }
            """
        }
    ) == {
        ("GET", "/ping", "ping", False),
        ("GET", "/api/users", "listUsers", False),
        ("POST", "/api/v1/users", "h.Create", False),
        ("ANY", "/any", "anyHandler", False),
    }


def test_echo(endpoints_of):
    assert endpoints_of(
        {
            "main.go": """
            package main
            import "github.com/labstack/echo/v4"
            func main() {
              e := echo.New()
              e.GET("/e", getE)
              g := e.Group("/admin", authMiddleware)
              g.DELETE("/users/:id", removeUser)
            }
            """
        }
    ) == {
        ("GET", "/e", "getE", False),
        ("DELETE", "/admin/users/:id", "removeUser", False),
    }


def test_chi_routes_and_nested_closures(endpoints_of):
    assert endpoints_of(
        {
            "main.go": """
            package main
            import "github.com/go-chi/chi/v5"
            func main() {
              r := chi.NewRouter()
              r.Get("/", index)
              r.Route("/v1", func(r chi.Router) {
                r.Get("/things", things)
                r.Route("/nested", func(r chi.Router) {
                  r.Post("/deep", deep)
                })
              })
              r.Method("PUT", "/put", putHandler)
            }
            """
        }
    ) == {
        ("GET", "/", "index", False),
        ("GET", "/v1/things", "things", False),
        ("POST", "/v1/nested/deep", "deep", False),
        ("PUT", "/put", "putHandler", False),
    }


def test_files_without_a_web_framework_are_ignored(endpoints_of):
    assert (
        endpoints_of(
            {
                "util.go": """
                package util
                func f(m map[string]int) { cache.GET("/x", 1); m.Get("k", 2) }
                """
            }
        )
        == set()
    )
