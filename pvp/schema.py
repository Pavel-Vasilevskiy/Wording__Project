"""Additive SQLite schema upgrades for installations predating PvP settings/presence."""

from sqlalchemy import inspect

from models import db


_ADDITIVE_COLUMNS = {
    "pvp_lobby": {
        "words_per_match": "INTEGER NOT NULL DEFAULT 5",
        "mode": "VARCHAR(20) NOT NULL DEFAULT 'timer'",
        "timer_seconds": "INTEGER NOT NULL DEFAULT 120",
        "settings_version": "INTEGER NOT NULL DEFAULT 0",
    },
    "pvp_lobby_member": {
        "is_ready": "BOOLEAN NOT NULL DEFAULT 0",
        # Constant defaults are required by SQLite ALTER TABLE on populated tables.
        # Existing participants receive a fresh heartbeat grace window at upgrade.
        "last_seen_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
    },
}


def upgrade_pvp_schema(engine=None):
    """Add missing PvP columns in place and retain all existing lobby data."""
    engine = engine or db.engine
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table_name, columns in _ADDITIVE_COLUMNS.items():
            if not inspector.has_table(table_name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, definition in columns.items():
                if column_name not in existing:
                    connection.exec_driver_sql(
                        f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {definition}'
                    )
                    existing.add(column_name)

        if inspect(connection).has_table("pvp_lobby_member"):
            columns = {column["name"] for column in inspect(connection).get_columns("pvp_lobby_member")}
            if "last_seen_at" in columns:
                connection.exec_driver_sql(
                    "UPDATE pvp_lobby_member "
                    "SET last_seen_at = CURRENT_TIMESTAMP "
                    "WHERE last_seen_at = '1970-01-01 00:00:00'"
                )
