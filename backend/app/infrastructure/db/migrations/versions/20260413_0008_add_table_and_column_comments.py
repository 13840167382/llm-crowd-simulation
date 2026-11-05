"""为现有表和字段补充数据库注释。"""

from __future__ import annotations

from alembic import op

from backend.app.infrastructure.db.models import Base

revision = "20260413_0008"
down_revision = "20260413_0007"
branch_labels = None
depends_on = None


def _quote_comment(value: str) -> str:
    return value.replace("'", "''")


def _table_identifier(preparer, table) -> str:
    if table.schema:
        return f"{preparer.quote_schema(table.schema)}.{preparer.quote(table.name)}"
    return preparer.quote(table.name)


def _apply_comments(clear: bool = False) -> None:
    bind = op.get_bind()
    preparer = bind.dialect.identifier_preparer

    for table in Base.metadata.sorted_tables:
        table_identifier = _table_identifier(preparer, table)

        if clear:
            bind.exec_driver_sql(f"COMMENT ON TABLE {table_identifier} IS NULL")
        elif table.comment:
            bind.exec_driver_sql(
                f"COMMENT ON TABLE {table_identifier} IS '{_quote_comment(table.comment)}'"
            )

        for column in table.columns:
            column_identifier = preparer.quote(column.name)
            target = f"{table_identifier}.{column_identifier}"

            if clear:
                bind.exec_driver_sql(f"COMMENT ON COLUMN {target} IS NULL")
            elif column.comment:
                bind.exec_driver_sql(
                    f"COMMENT ON COLUMN {target} IS '{_quote_comment(column.comment)}'"
                )


def upgrade() -> None:
    """执行升级迁移。"""

    _apply_comments(clear=False)


def downgrade() -> None:
    """执行回滚迁移。"""

    _apply_comments(clear=True)
