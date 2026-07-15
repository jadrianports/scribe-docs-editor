import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..access import DocAccess, require_reader
from ..content import html_to_markdown

router = APIRouter(tags=["export"])


def _safe_filename(title: str) -> str:
    slug = re.sub(r"[^\w\- ]+", "", title).strip().replace(" ", "_")
    return slug or "document"


@router.get("/documents/{doc_id}/export")
def export_document(format: str = "md", acc: DocAccess = Depends(require_reader)):
    if format != "md":
        # PDF is produced client-side (print-to-PDF); only Markdown is served here.
        raise HTTPException(status_code=400, detail="Only 'md' export is supported by the API")
    body = f"# {acc.document.title}\n\n" + html_to_markdown(acc.document.content_html)
    filename = _safe_filename(acc.document.title) + ".md"
    return Response(
        content=body,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
