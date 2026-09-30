"""FastAPI and Flask route detection, including routers and blueprints mounted across files."""

from __future__ import annotations


def test_fastapi_routes_and_router_prefixes(endpoints_of):
    assert endpoints_of(
        {
            "main.py": """
            from fastapi import FastAPI, APIRouter
            app = FastAPI()
            router = APIRouter(prefix="/users")

            @app.get("/health")
            def health(): pass

            @router.get("/{id}")
            async def get_user(id: int): pass

            @router.post("/")
            def create(): pass

            @app.api_route("/multi", methods=["GET", "POST"])
            def multi(): pass

            @app.websocket("/ws")
            async def ws(): pass

            app.include_router(router, prefix="/api")
            """
        }
    ) == {
        ("GET", "/health", "health", False),
        ("GET", "/api/users/{id}", "get_user", False),
        ("POST", "/api/users/", "create", False),
        ("GET", "/multi", "multi", False),
        ("POST", "/multi", "multi", False),
        ("WS", "/ws", "ws", False),
    }


def test_fastapi_routers_mounted_from_other_files(endpoints_of):
    assert endpoints_of(
        {
            "app/routers/users.py": """
            from fastapi import APIRouter
            router = APIRouter(prefix="/users")

            @router.get("/")
            def list_users(): pass
            """,
            "app/routers/items.py": """
            from fastapi import APIRouter
            router = APIRouter(prefix="/items")

            @router.delete("/{item_id}")
            def remove(item_id: int): pass
            """,
            "app/routers/admin.py": """
            from fastapi import APIRouter
            from .items import router as items_router
            router = APIRouter(prefix="/admin")
            router.include_router(items_router, prefix="/v1")

            @router.get("/stats")
            def stats(): pass
            """,
            "app/main.py": """
            from fastapi import FastAPI
            from app.routers import users
            from app.routers.admin import router as admin_router

            app = FastAPI()
            app.include_router(users.router, prefix="/api")
            app.include_router(admin_router)
            """,
        }
    ) == {
        ("GET", "/api/users/", "list_users", False),
        ("GET", "/admin/stats", "stats", False),
        ("DELETE", "/admin/v1/items/{item_id}", "remove", False),  # a router mounted in a router
    }


def test_unresolvable_prefixes_are_marked_partial(endpoints_of):
    found = endpoints_of(
        {
            "orphan.py": """
            from fastapi import APIRouter
            router = APIRouter(prefix="/orphan")

            @router.get("/x")
            def x(): pass
            """,
            "dyn.py": """
            from fastapi import FastAPI, APIRouter
            app = FastAPI()
            router = APIRouter(prefix=settings.PREFIX)
            @router.get("/y")
            def y(): pass
            @app.get(PATH)
            def z(): pass
            app.include_router(router)
            """,
        }
    )
    assert ("GET", "/orphan/x", "x", True) in found  # nothing mounts this router
    assert ("GET", "/y", "y", True) in found  # a prefix computed at run time
    assert ("GET", "/", "z", True) in found  # a computed path


def test_flask_routes_and_blueprints(endpoints_of):
    assert endpoints_of(
        {
            "shop/views.py": """
            from flask import Blueprint
            bp = Blueprint("shop", __name__, url_prefix="/shop")

            @bp.route("/cart", methods=["GET", "POST"])
            def cart(): pass

            @bp.get("/items")
            def items(): pass
            """,
            "app.py": """
            from flask import Flask
            from shop.views import bp

            app = Flask(__name__)
            app.register_blueprint(bp, url_prefix="/v2")

            @app.route("/")
            def index(): pass

            app.add_url_rule("/about", view_func=about, methods=["GET"])
            """,
        }
    ) == {
        ("GET", "/v2/cart", "cart", False),  # the registration prefix replaces the blueprint's
        ("POST", "/v2/cart", "cart", False),
        ("GET", "/v2/items", "items", False),
        ("GET", "/", "index", False),
        ("GET", "/about", "about", False),
    }


def test_flask_blueprint_keeps_its_own_prefix_without_an_override(endpoints_of):
    assert endpoints_of(
        {
            "views.py": """
            from flask import Blueprint
            bp = Blueprint("b", __name__, url_prefix="/b")
            @bp.route("/x")
            def x(): pass
            """,
            "app.py": """
            from flask import Flask
            from views import bp
            app = Flask(__name__)
            app.register_blueprint(bp)
            """,
        }
    ) == {("GET", "/b/x", "x", False)}


def test_decorators_that_are_not_routes_are_ignored(endpoints_of):
    assert (
        endpoints_of(
            {
                "tests_helper.py": """
                import functools
                import pytest

                @pytest.mark.get("/not-a-route")
                def a(): pass

                @functools.lru_cache(maxsize=1)
                def b(): pass

                @something.get("/x")
                def c(): pass
                """
            }
        )
        == set()
    )
