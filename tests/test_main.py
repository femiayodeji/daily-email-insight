from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_home():
    response = client.get("/")
    assert response.status_code == 200
    assert "<html" in response.text.lower()

def test_login_redirect():
    response = client.get("/login")

    assert response.status_code in (302, 307)
    assert "accounts.google.com" in response.headers.get("location", "")

def test_logout():
    with client as c:

        c.cookies.set("session", "dummy")
        response = c.get("/logout")
        assert response.status_code in (302, 307)
        assert response.headers["location"] == "/"
