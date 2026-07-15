def _new_doc(client):
    res = client.post("/api/documents")
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_export_markdown_does_not_duplicate_title(client, login, seed_users):
    login("alice@example.com")
    doc_id = _new_doc(client)
    client.patch(
        f"/api/documents/{doc_id}",
        json={"title": "My Doc", "content_html": "<h1>My Doc</h1><p>Body text</p>"},
    )
    res = client.get(f"/api/documents/{doc_id}/export?format=md")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert 'filename="My_Doc.md"' in res.headers["content-disposition"]
    body = res.text
    assert "Body text" in body
    # The heading must appear exactly once — the export must not prepend a second
    # copy of the title on top of the document's own leading heading.
    assert body.count("# My Doc") == 1


def test_export_empty_document_falls_back_to_title(client, login, seed_users):
    login("alice@example.com")
    doc_id = _new_doc(client)
    client.patch(f"/api/documents/{doc_id}", json={"title": "Empty One"})
    res = client.get(f"/api/documents/{doc_id}/export?format=md")
    assert res.status_code == 200
    assert res.text.strip() == "# Empty One"


def test_export_rejects_unknown_format(client, login, seed_users):
    login("alice@example.com")
    doc_id = _new_doc(client)
    assert client.get(f"/api/documents/{doc_id}/export?format=pdf").status_code == 400


def test_export_allowed_for_viewer(client, login, seed_users):
    # A viewer can read, so a viewer can export.
    login("alice@example.com")
    doc_id = _new_doc(client)
    client.patch(f"/api/documents/{doc_id}", json={"content_html": "<p>hello</p>"})
    client.post(
        f"/api/documents/{doc_id}/shares",
        json={"email": "bob@example.com", "role": "viewer"},
    )
    login("bob@example.com")
    assert client.get(f"/api/documents/{doc_id}/export?format=md").status_code == 200
