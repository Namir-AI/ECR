"""Deployment-only dotenv and template helpers.

Secrets are accepted through stdin and are never written to stdout.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key


def _stdin_pairs() -> dict[str, str]:
    fields = sys.stdin.buffer.read().split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise SystemExit("Invalid deployment input.")
    decoded = [field.decode("utf-8") for field in fields]
    return dict(zip(decoded[0::2], decoded[1::2], strict=True))


def write_env(target: Path, template: Path) -> None:
    if not target.exists():
        shutil.copyfile(template, target)
    for key, value in _stdin_pairs().items():
        set_key(target, key, value, quote_mode="always")


def read_env(target: Path, keys: list[str]) -> None:
    values = dotenv_values(target, interpolate=False)
    for key in keys:
        value = values.get(key)
        if value is None:
            raise SystemExit(f"Missing deployment setting: {key}")
        sys.stdout.buffer.write(str(value).encode("utf-8") + b"\0")


def render_template(source: Path, target: Path) -> None:
    rendered = source.read_text(encoding="utf-8")
    for key, value in _stdin_pairs().items():
        rendered = rendered.replace(f"@@{key}@@", value)
    if "@@" in rendered:
        raise SystemExit("Deployment template contains unresolved placeholders.")
    target.write_text(rendered, encoding="utf-8")


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("Invalid deployment helper invocation.")
    command = sys.argv[1]
    if command == "write" and len(sys.argv) == 4:
        write_env(Path(sys.argv[2]), Path(sys.argv[3]))
    elif command == "read" and len(sys.argv) >= 4:
        read_env(Path(sys.argv[2]), sys.argv[3:])
    elif command == "render" and len(sys.argv) == 4:
        render_template(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit("Invalid deployment helper invocation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
