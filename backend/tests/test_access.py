def _create_doc(client):
    res = client.post("/api/documents")
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_owner_can_crud_others_cannot_see(client, login, seed_users):
    login("alice@example.com")
    doc_id = _create_doc(client)

    assert client.get(f"/api/documents/{doc_id}").status_code == 200
    patched = client.patch(f"/api/documents/{doc_id}", json={"title": "Alice's Doc"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "Alice's Doc"

    # Bob has no role on the doc -> 404 (not 403), so existence is not leaked.
    login("bob@example.com")
    assert client.get(f"/api/documents/{doc_id}").status_code == 404
    assert client.patch(f"/api/documents/{doc_id}", json={"title": "hax"}).status_code == 404


def test_viewer_reads_only_then_editor_can_edit(client, login, seed_users):
    login("alice@example.com")
    doc_id = _create_doc(client)
    res = client.post(
        f"/api/documents/{doc_id}/shares",
        json={"email": "bob@example.com", "role": "viewer"},
    )
    assert res.status_code == 201

    login("bob@example.com")
    assert client.get(f"/api/documents/{doc_id}").status_code == 200  # can read
    assert (
        client.patch(
            f"/api/documents/{doc_id}", json={"content_html": "<p>x</p>"}
        ).status_code
        == 403
    )  # cannot edit
    assert client.delete(f"/api/documents/{doc_id}").status_code == 403  # cannot delete

    # Alice upgrades Bob to editor: re-sharing upserts the role and returns 200.
    login("alice@example.com")
    res = client.post(
        f"/api/documents/{doc_id}/shares",
        json={"email": "bob@example.com", "role": "editor"},
    )
    assert res.status_code == 200

    login("bob@example.com")
    edited = client.patch(f"/api/documents/{doc_id}", json={"content_html": "<p>hi</p>"})
    assert edited.status_code == 200
    assert edited.json()["content_html"] == "<p>hi</p>"
    # Editors still cannot delete — that's owner-only.
    assert client.delete(f"/api/documents/{doc_id}").status_code == 403


def test_listing_separates_owned_and_shared(client, login, seed_users):
    login("alice@example.com")
    doc_id = _create_doc(client)
    client.post(
        f"/api/documents/{doc_id}/shares",
        json={"email": "bob@example.com", "role": "viewer"},
    )

    login("bob@example.com")
    listing = client.get("/api/documents").json()
    assert all(d["id"] != doc_id for d in listing["owned"])
    shared = next((d for d in listing["shared"] if d["id"] == doc_id), None)
    assert shared is not None
    assert shared["role"] == "viewer"
    assert shared["owner"]["name"] == "Alice"


def test_share_errors_for_unknown_email_and_self(client, login, seed_users):
    login("alice@example.com")
    doc_id = _create_doc(client)
    assert (
        client.post(
            f"/api/documents/{doc_id}/shares",
            json={"email": "nobody@example.com", "role": "viewer"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/documents/{doc_id}/shares",
            json={"email": "alice@example.com", "role": "viewer"},
        ).status_code
        == 400
    )
