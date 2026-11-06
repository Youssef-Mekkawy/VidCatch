import pytest
from fastapi.testclient import TestClient

from vidcatch.server.app import create_app

TOKEN = "test-token-123"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def raw_client():
    """A client with no session cookie."""
    return TestClient(create_app(TOKEN, PORT), base_url=BASE)


def test_ping_is_public(raw_client):
    response = raw_client.get("/api/ping")
    assert response.status_code == 200
    assert response.json()["app"] == "vidcatch"


def test_api_needs_the_session_cookie(raw_client):
    assert raw_client.get("/api/status").status_code == 401
    raw_client.headers["cookie"] = "ytdl_session=wrong"
    assert raw_client.get("/api/status").status_code == 401
    raw_client.headers["cookie"] = f"ytdl_session={TOKEN}"
    assert raw_client.get("/api/status").status_code == 200


def test_launch_link_sets_cookie_and_redirects(raw_client):
    response = raw_client.get(f"/?t={TOKEN}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert f"ytdl_session={TOKEN}" in cookie
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_wrong_launch_token_sets_no_cookie(raw_client):
    response = raw_client.get("/?t=nope", follow_redirects=False)
    assert response.status_code == 200
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1:9999", "localhost"])
def test_unknown_host_is_rejected(raw_client, host):
    """DNS rebinding: the attacker's domain resolves to 127.0.0.1 but sends its own Host."""
    response = raw_client.get("/api/ping", headers={"host": host})
    assert response.status_code == 400


def test_localhost_names_are_accepted(raw_client):
    for host in (f"localhost:{PORT}", f"[::1]:{PORT}"):
        assert raw_client.get("/api/ping", headers={"host": host}).status_code == 200


def test_cross_origin_requests_are_rejected(raw_client):
    raw_client.headers["cookie"] = f"ytdl_session={TOKEN}"
    response = raw_client.post(
        "/api/shutdown", headers={"origin": "https://evil.example"}
    )
    assert response.status_code == 403
    same = raw_client.get("/api/status", headers={"origin": BASE})
    assert same.status_code == 200


def test_security_headers_on_every_response(raw_client):
    for path in ("/", "/api/ping"):
        headers = raw_client.get(path).headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"


def test_api_responses_are_not_cached(raw_client):
    assert raw_client.get("/api/ping").headers["cache-control"] == "no-store"


def test_static_page_is_served(raw_client):
    response = raw_client.get("/")
    assert response.status_code == 200
    assert "VidCatch" in response.text
