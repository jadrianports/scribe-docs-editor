def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_me_requires_auth(client):
    res = client.get("/api/auth/me")
    assert res.status_code == 401


def test_login_rejects_bad_password(client, seed_users):
    res = client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "wrong"}
    )
    assert res.status_code == 401


def test_login_then_me(client, seed_users):
    res = client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "pw"}
    )
    assert res.status_code == 200
    assert res.json()["email"] == "alice@example.com"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["name"] == "Alice"
