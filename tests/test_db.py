"""SQLAlchemy foundation tests that do not open a database connection."""

from sqlalchemy.dialects.mysql.pymysql import MySQLDialect_pymysql

from app.db.session import build_engine
from tests.test_config import build_settings


def test_engine_uses_mysql_dialect_and_safe_pooling() -> None:
    engine = build_engine(build_settings())

    try:
        assert isinstance(engine.dialect, MySQLDialect_pymysql)
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()
