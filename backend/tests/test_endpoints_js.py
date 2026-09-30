"""Express, Fastify, Koa and NestJS route detection."""

from __future__ import annotations


def test_express_routes_and_mounts_across_files(endpoints_of):
    assert endpoints_of(
        {
            "routes/users.js": """
            const express = require("express");
            const router = express.Router();
            router.get("/", list);
            router.post("/:id", auth, (req, res) => {});
            router.route("/profile").get(showProfile).put(updateProfile);
            module.exports = router;
            """,
            "routes/books.ts": """
            import { Router } from "express";
            const books = Router();
            books.delete("/:id", controller.remove);
            export default books;
            """,
            "server.js": """
            const express = require("express");
            const users = require("./routes/users");
            import booksRouter from "./routes/books";
            const app = express();
            app.get("/health", (req, res) => {});
            app.use("/api/users", users);
            app.use("/api/books", booksRouter);
            app.use("/inline", require("./routes/users"));
            app.use(express.json());
            """,
        }
    ) == {
        ("GET", "/health", "(inline)", False),
        ("GET", "/api/users/", "list", False),
        ("POST", "/api/users/:id", "(inline)", False),
        ("GET", "/api/users/profile", "showProfile", False),
        ("PUT", "/api/users/profile", "updateProfile", False),
        ("DELETE", "/api/books/:id", "controller.remove", False),
    }  # /inline mounts the same router a second time: the first mount wins


def test_calls_that_only_look_like_routes_are_ignored(endpoints_of):
    assert (
        endpoints_of(
            {
                "client.js": """
                const express = require("express");
                const axios = require("axios");
                const cache = new Map();
                axios.get("/api/users");
                cache.get("/key");
                request(app).get("/tested");
                const app = express();
                app.get("named-not-a-path", handler);
                """
            }
        )
        == set()
    )


def test_fastify_routes_and_plugins(endpoints_of):
    assert endpoints_of(
        {
            "server.js": """
            const fastify = require("fastify")();
            fastify.get("/ping", async (req) => 1);
            fastify.route({ method: ["GET", "POST"], url: "/multi", handler: multi });
            fastify.register(require("./plugin"), { prefix: "/api" });
            """,
            "plugin.js": """
            module.exports = async function (fastify, options) {
              fastify.get("/users", listUsers);
              fastify.post("/users", { schema: {} }, createUser);
            };
            """,
        }
    ) == {
        ("GET", "/ping", "(inline)", False),
        ("GET", "/multi", "multi", False),
        ("POST", "/multi", "multi", False),
        ("GET", "/api/users", "listUsers", False),
        ("POST", "/api/users", "createUser", False),
    }


def test_koa_router_with_a_prefix(endpoints_of):
    assert endpoints_of(
        {
            "app.js": """
            const Koa = require("koa");
            const Router = require("koa-router");
            const app = new Koa();
            const router = new Router({ prefix: "/api" });
            router.get("/books/:id", getBook);
            router.post("/books", async (ctx) => {});
            app.use(router.routes());
            """
        }
    ) == {
        ("GET", "/api/books/:id", "getBook", False),
        ("POST", "/api/books", "(inline)", False),
    }


def test_nestjs_controllers(endpoints_of):
    assert endpoints_of(
        {
            "cats.controller.ts": """
            import { Controller, Get, Post, Delete } from "@nestjs/common";

            @Controller("cats")
            export class CatsController {
              @Get(":id")
              findOne() {}

              @Post()
              create() {}

              @Delete(":id")
              async remove() {}

              helper() {}
            }

            @Controller({ path: "dogs" })
            class DogsController {
              @Get()
              list() {}
            }
            """
        }
    ) == {
        ("GET", "/cats/:id", "CatsController.findOne", False),
        ("POST", "/cats", "CatsController.create", False),
        ("DELETE", "/cats/:id", "CatsController.remove", False),
        ("GET", "/dogs", "DogsController.list", False),
    }


def test_computed_paths_are_partial(endpoints_of):
    found = endpoints_of(
        {
            "a.js": """
            const express = require("express");
            const app = express();
            const router = express.Router();
            router.get("/x", h);
            app.use(BASE_PATH, router);
            """
        }
    )
    assert ("GET", "/x", "h", True) in found
