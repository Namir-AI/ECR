"""Safe command-line MySQL connectivity check."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError

from app.db.session import get_engine


def main() -> int:
    """Execute a minimal query without displaying connection credentials."""
    try:
        with get_engine().connect() as connection:
            result = connection.execute(
                text(
                    "SELECT 1, VERSION(), "
                    "@@character_set_connection, @@character_set_database, "
                    "@@collation_database"
                )
            ).one()
    except (SQLAlchemyError, ValidationError, ValueError):
        print("MySQL connection: FAILED")
        return 1

    probe, version, connection_charset, database_charset, database_collation = result
    if (
        probe != 1
        or not str(version).startswith("8.")
        or connection_charset != "utf8mb4"
        or database_charset != "utf8mb4"
        or not str(database_collation).startswith("utf8mb4_")
    ):
        print("MySQL connection: FAILED")
        print(f"MySQL version: {version}")
        print(f"MySQL connection charset: {connection_charset}")
        print(f"MySQL database charset: {database_charset}")
        print(f"MySQL database collation: {database_collation}")
        return 1

    print("MySQL connection: PASS")
    print(f"MySQL version: {version}")
    print(f"MySQL connection charset: {connection_charset}")
    print(f"MySQL database charset: {database_charset}")
    print(f"MySQL database collation: {database_collation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
