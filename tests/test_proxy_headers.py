import ast
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


def _echo_base_url_app() -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def echo(request: Request):
        return {"base_url": str(request.base_url)}

    return app


def test_forwarded_proto_is_ignored_without_the_proxy_headers_middleware():
    # Baseline: a plain app with no awareness of the proxy always reports "http",
    # regardless of what the client claims - this is the bug that shipped originally.
    client = TestClient(_echo_base_url_app())

    response = client.get("/", headers={"X-Forwarded-Proto": "https"})

    assert response.json()["base_url"].startswith("http://")


def test_forwarded_proto_is_honored_with_trusted_hosts_star():
    # This is what run.py's proxy_headers=True, forwarded_allow_ips="*" actually
    # configures under the hood - trusting X-Forwarded-Proto from any peer, since the
    # app is only ever reached through Caddy on the internal Docker network, never
    # directly from an untrusted client.
    app = ProxyHeadersMiddleware(_echo_base_url_app(), trusted_hosts="*")
    client = TestClient(app)

    response = client.get("/", headers={"X-Forwarded-Proto": "https"})

    assert response.json()["base_url"].startswith("https://")


def test_forwarded_proto_is_still_ignored_with_default_trusted_hosts():
    # uvicorn's own default (127.0.0.1 only) would NOT have fixed this bug - Caddy
    # reaches the app over the Docker network, not loopback. Confirms "*" is actually
    # doing the necessary work here, not merely redundant with uvicorn's default.
    app = ProxyHeadersMiddleware(_echo_base_url_app())  # default trusted_hosts
    client = TestClient(app)

    response = client.get("/", headers={"X-Forwarded-Proto": "https"})

    assert response.json()["base_url"].startswith("http://")


def test_run_py_enables_proxy_headers_trusting_any_forwarded_peer():
    # Static check on the actual entrypoint: parses run.py's uvicorn.run(...) call and
    # asserts the two keyword arguments the fix depends on are really there, so this
    # test fails loudly if someone edits run.py and drops them again.
    source = Path(__file__).resolve().parent.parent / "run.py"
    tree = ast.parse(source.read_text())
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    )
    kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}

    assert kwargs.get("proxy_headers") is True
    assert kwargs.get("forwarded_allow_ips") == "*"
