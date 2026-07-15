def test_upload_md_creates_sanitized_doc(client, login, seed_users):
    login("alice@example.com")
    content = b"# Title\n\nSome **bold** text.\n\n<script>alert(1)</script>\n"
    res = client.post(
        "/api/documents/upload",
        files={"file": ("notes.md", content, "text/markdown")},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["title"] == "notes"
    assert "<h1>Title</h1>" in body["content_html"]
    assert "<strong>bold</strong>" in body["content_html"]
    assert "<script>" not in body["content_html"]  # sanitized away
    assert body["role"] == "owner"


def test_upload_txt_wraps_paragraphs(client, login, seed_users):
    login("alice@example.com")
    res = client.post(
        "/api/documents/upload",
        files={"file": ("plain.txt", b"line one\n\nline two", "text/plain")},
    )
    assert res.status_code == 201
    html = res.json()["content_html"]
    assert "<p>line one</p>" in html
    assert "<p>line two</p>" in html


def test_upload_rejects_unsupported_type(client, login, seed_users):
    login("alice@example.com")
    res = client.post(
        "/api/documents/upload",
        files={"file": ("data.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert res.status_code == 415


def test_upload_requires_auth(client):
    res = client.post(
        "/api/documents/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert res.status_code == 401
