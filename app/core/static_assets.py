"""A deterministic bundle version, computed once per application startup."""

import hashlib
from pathlib import Path

from jinja2 import pass_context


def static_bundle_version(directory: Path) -> str:
    """Version CSS/JS contents, including additions/removals, without Git or secrets."""
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix in {".css", ".js"}:
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(64 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()[:20]


@pass_context
def static_asset_url(context, path: str):
    request = context["request"]
    return request.url_for("static", path=path).include_query_params(
        v=request.app.state.static_asset_version
    )
