"""Backward-compatible entry point for initial Superadmin creation."""

from app.users.create_superadmin import main, run_interactive

__all__ = ["main", "run_interactive"]


if __name__ == "__main__":
    raise SystemExit(main())
