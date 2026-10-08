"""Bounded private WeasyPrint rendering; no network or arbitrary file fetches.

One renderer at a time per application process, isolated in a short-lived Python
worker (not a browser). 90-second wall/60-second CPU budget and 768 MiB address
space limit are operational safeguards, not limits on stored report data.
"""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from threading import BoundedSemaphore

PDF_SLOT = BoundedSemaphore(1)
APPLICATION_ROOT = Path(__file__).resolve().parents[2]


class PdfRenderError(RuntimeError):
    """Safe operational error, without renderer stderr, paths or private content."""


def resource_fetcher(resources):
    """Exact registered in-memory PNG resources only; no default-fetch fallback."""
    from weasyprint.urls import URLFetcher, URLFetcherResponse

    class PrivateResources(URLFetcher):
        def fetch(self, url, headers=None):
            if url not in resources:
                raise ValueError("PDF resource access denied")
            return URLFetcherResponse(
                url,
                base64.b64decode(resources[url], validate=True),
                {"Content-Type": "image/png"},
            )

    return PrivateResources(fail_on_errors=True)


def render_pdf(html: str, resources: dict[str, str], expected_pages: int) -> bytes:
    if not PDF_SLOT.acquire(blocking=False):
        raise PdfRenderError("PDF renderer is busy. Please retry shortly.")
    try:
        # No DB, session, storage or other production secrets enter the worker env.
        env = {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(
            [sys.executable, "-m", "app.ecr.pdf_renderer"],
            cwd=APPLICATION_ROOT,
            env=env,
            input=json.dumps(
                {"html": html, "resources": resources, "pages": expected_pages}
            ).encode(),
            capture_output=True,
            timeout=90,
            check=False,
        )
        if result.returncode or not result.stdout.startswith(b"%PDF-"):
            raise PdfRenderError(
                "PDF rendering failed. Check the renderer libraries, fonts and report layout."
            )
        return result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PdfRenderError(
            "PDF renderer is unavailable or exceeded its safe runtime budget."
        ) from exc
    finally:
        PDF_SLOT.release()


def worker():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    from weasyprint import HTML

    payload = json.load(sys.stdin)
    document = HTML(
        string=payload["html"], url_fetcher=resource_fetcher(payload["resources"])
    ).render()
    if len(document.pages) != payload["pages"]:
        raise PdfRenderError("Unexpected pagination")
    sys.stdout.buffer.write(document.write_pdf())


if __name__ == "__main__":
    worker()
