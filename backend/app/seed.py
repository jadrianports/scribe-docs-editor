"""Idempotent demo data.

Runs on startup: if there are no users yet, create three demo accounts plus a
couple of documents and shares so the sharing flow is demonstrable on first
login. All demo accounts use the password `demo1234`.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_password
from .models import Document, Share, User

DEMO_PASSWORD = "demo1234"

WELCOME_HTML = (
    "<h1>Welcome to Scribe</h1>"
    "<p>This is a <strong>rich-text</strong> document. Try <em>editing</em> it, or "
    "<u>create</u> a new one.</p>"
    "<h2>What you can do</h2>"
    "<ul><li>Bold, italic, underline</li><li>Headings and lists</li>"
    "<li>Upload a .txt or .md file</li><li>Share with a teammate</li></ul>"
)
ROADMAP_HTML = (
    "<h1>Project Roadmap</h1>"
    "<h2>This quarter</h2>"
    "<ol><li>Ship the editor</li><li>Ship sharing</li><li>Polish</li></ol>"
    "<p>Shared with the team below.</p>"
)
NOTES_HTML = "<h2>Meeting Notes</h2><p>Standup action items go here.</p>"


def seed(db: Session) -> None:
    if db.execute(select(User)).first():
        return

    alice = User(email="alice@example.com", name="Alice", password_hash=hash_password(DEMO_PASSWORD))
    bob = User(email="bob@example.com", name="Bob", password_hash=hash_password(DEMO_PASSWORD))
    carol = User(email="carol@example.com", name="Carol", password_hash=hash_password(DEMO_PASSWORD))
    db.add_all([alice, bob, carol])
    db.flush()

    welcome = Document(title="Welcome to Scribe", content_html=WELCOME_HTML, owner_id=alice.id)
    roadmap = Document(title="Project Roadmap", content_html=ROADMAP_HTML, owner_id=alice.id)
    notes = Document(title="Bob's Meeting Notes", content_html=NOTES_HTML, owner_id=bob.id)
    db.add_all([welcome, roadmap, notes])
    db.flush()

    db.add_all(
        [
            Share(document_id=roadmap.id, user_id=bob.id, role="editor"),
            Share(document_id=roadmap.id, user_id=carol.id, role="viewer"),
        ]
    )
    db.commit()
