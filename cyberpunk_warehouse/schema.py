"""Additive schema upgrades for Cyberpunk Warehouse on existing SQLite installs."""

from sqlalchemy import inspect


_ADDITIVE_COLUMNS = {
    "cyberpunk_warehouse_product": {
        "description": "VARCHAR(600) NOT NULL DEFAULT ''",
        "category_id": "INTEGER REFERENCES cyberpunk_warehouse_category(id) ON DELETE SET NULL",
        "sort_order": "INTEGER NOT NULL DEFAULT 0",
    },
    "cyberpunk_warehouse_customer": {
        "internal_key": "VARCHAR(80) NOT NULL DEFAULT ''",
        "description": "VARCHAR(600) NOT NULL DEFAULT ''",
        "sort_order": "INTEGER NOT NULL DEFAULT 0",
    },
    "cyberpunk_warehouse_asset": {
        "width": "INTEGER",
        "height": "INTEGER",
        "file_size": "INTEGER",
    },
    "cyberpunk_warehouse_level": {
        "warehouse_layout": "JSON NOT NULL DEFAULT '{\"version\":1,\"rows\":1,\"columns\":1,\"shelves\":[]}'",
        "mode_configurations": "JSON NOT NULL DEFAULT '{}'",
        "gameplay_configuration": "JSON NOT NULL DEFAULT '{}'",
        "configuration_version": "INTEGER NOT NULL DEFAULT 1",
        "internal_key": "VARCHAR(100)",
        "sort_order": "INTEGER NOT NULL DEFAULT 0",
        "preview_asset_id": "INTEGER REFERENCES cyberpunk_warehouse_asset(id) ON DELETE SET NULL",
    },
    "cyberpunk_warehouse_order_item": {
        "requested_quantity": "INTEGER NOT NULL DEFAULT 3",
    },
}


def upgrade_cyberpunk_warehouse_schema(engine):
    """Add nullable/defaulted columns in place; never rebuild existing tables."""
    with engine.begin() as connection:
        inspector = inspect(connection)
        added_columns = set()
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
                    added_columns.add((table_name, column_name))

        if ("cyberpunk_warehouse_order_item", "requested_quantity") in added_columns:
            connection.exec_driver_sql(
                "UPDATE cyberpunk_warehouse_order_item SET requested_quantity = quantity"
            )

        if inspect(connection).has_table("cyberpunk_warehouse_customer"):
            connection.exec_driver_sql(
                "UPDATE cyberpunk_warehouse_customer "
                "SET internal_key = 'legacy-customer-' || id "
                "WHERE internal_key IS NULL OR internal_key = ''"
            )
            unique_key_index_exists = any(
                index.get("unique") and index.get("column_names") == ["internal_key"]
                for index in inspect(connection).get_indexes("cyberpunk_warehouse_customer")
            )
            if not unique_key_index_exists:
                connection.exec_driver_sql(
                    'CREATE UNIQUE INDEX "uq_cyberpunk_customer_internal_key" '
                    'ON "cyberpunk_warehouse_customer" ("internal_key")'
                )
        if inspect(connection).has_table("cyberpunk_warehouse_product"):
            connection.exec_driver_sql(
                'CREATE INDEX IF NOT EXISTS "ix_cyberpunk_product_category_id" '
                'ON "cyberpunk_warehouse_product" ("category_id")'
            )
        if inspect(connection).has_table("cyberpunk_warehouse_level"):
            connection.exec_driver_sql(
                "UPDATE cyberpunk_warehouse_level SET internal_key = 'legacy-level-' || id "
                "WHERE internal_key IS NULL OR internal_key = ''"
            )
            connection.exec_driver_sql(
                'CREATE UNIQUE INDEX IF NOT EXISTS "uq_cyberpunk_level_internal_key" '
                'ON "cyberpunk_warehouse_level" ("internal_key")'
            )


