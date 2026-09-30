"""Spring (Java) and ASP.NET (C#) route detection."""

from __future__ import annotations


def test_spring_controller_prefixes_and_mappings(endpoints_of):
    assert endpoints_of(
        {
            "src/BookController.java": """
            @RestController
            @RequestMapping("/api/books")
            public class BookController {
              @GetMapping("/{id}")
              public Book get() { return null; }

              @PostMapping
              public void create() {}

              @RequestMapping(value = "/x", method = RequestMethod.POST)
              public void x() {}

              @GetMapping(path = {"/a", "/b"})
              public void two() {}

              @RequestMapping(value = "/any", method = {RequestMethod.GET, RequestMethod.PUT})
              public void many() {}

              @DeleteMapping(Routes.ITEM)
              public void constant() {}

              public void helper() {}
            }
            """,
            "src/Health.java": """
            @Controller
            public class Health {
              @GetMapping("/health")
              public String ok() { return "ok"; }
            }
            """,
        }
    ) == {
        ("GET", "/api/books/{id}", "BookController.get", False),
        ("POST", "/api/books", "BookController.create", False),
        ("POST", "/api/books/x", "BookController.x", False),
        ("GET", "/api/books/a", "BookController.two", False),
        ("GET", "/api/books/b", "BookController.two", False),
        ("GET", "/api/books/any", "BookController.many", False),
        ("PUT", "/api/books/any", "BookController.many", False),
        ("DELETE", "/api/books", "BookController.constant", True),  # a constant path is unknown
        ("GET", "/health", "Health.ok", False),
    }


def test_spring_class_with_a_computed_prefix_is_partial(endpoints_of):
    found = endpoints_of(
        {
            "A.java": """
            @RestController
            @RequestMapping(Routes.BASE)
            class A {
              @GetMapping("/x")
              void x() {}
            }
            """
        }
    )
    assert ("GET", "/x", "A.x", True) in found


def test_aspnet_controllers_and_tokens(endpoints_of):
    assert endpoints_of(
        {
            "Controllers/BooksController.cs": """
            [ApiController]
            [Route("api/[controller]")]
            public class BooksController : ControllerBase {
              [HttpGet("{id}")]
              public IActionResult Get(int id) { return Ok(); }

              [HttpPost]
              public IActionResult Create() { return Ok(); }

              [HttpGet("/health")]
              public IActionResult Health() { return Ok(); }

              [HttpDelete]
              [Route("[action]")]
              public IActionResult Purge() { return Ok(); }

              public IActionResult NotAnAction() { return Ok(); }
            }
            """
        }
    ) == {
        ("GET", "/api/Books/{id}", "BooksController.Get", False),
        ("POST", "/api/Books", "BooksController.Create", False),
        ("GET", "/health", "BooksController.Health", False),  # absolute: ignores the prefix
        ("DELETE", "/api/Books/Purge", "BooksController.Purge", False),
    }


def test_aspnet_minimal_apis_and_groups(endpoints_of):
    assert endpoints_of(
        {
            "Program.cs": """
            var builder = WebApplication.CreateBuilder(args);
            var app = builder.Build();
            var api = app.MapGroup("/api");
            var v1 = api.MapGroup("/v1");
            app.MapGet("/", () => "hello");
            api.MapPost("/books", CreateBook);
            v1.MapGet("/ping", () => "pong");
            app.MapDelete("/books/{id}", Handlers.Remove);
            """
        }
    ) == {
        ("GET", "/", "(inline)", False),
        ("POST", "/api/books", "CreateBook", False),
        ("GET", "/api/v1/ping", "(inline)", False),
        ("DELETE", "/books/{id}", "Handlers.Remove", False),
    }
