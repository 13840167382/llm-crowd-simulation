"""人群类型模板仓储实现。"""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.db.models import CrowdTypeTemplate


class CrowdTypeTemplateRepository:
    """人群类型模板表基础仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, template: CrowdTypeTemplate) -> CrowdTypeTemplate:
        """新增模板记录。"""

        self.session.add(template)
        return template

    def list(
        self,
        *,
        include_builtin: bool = True,
        keyword: str | None = None,
    ) -> list[CrowdTypeTemplate]:
        """查询模板列表。"""

        # 内置模板优先展示，其次按创建时间倒序，符合模板管理页的常见阅读顺序。
        stmt = self._build_base_query(include_builtin=include_builtin, keyword=keyword)
        stmt = stmt.order_by(CrowdTypeTemplate.is_builtin.desc(), CrowdTypeTemplate.created_at.desc())
        return list(self.session.scalars(stmt))

    def count(
        self,
        *,
        include_builtin: bool = True,
        keyword: str | None = None,
    ) -> int:
        """统计模板数量。"""

        stmt: Select[tuple[int]] = (
            select(func.count())
            .select_from(CrowdTypeTemplate)
            .where(*self._build_filters(include_builtin=include_builtin, keyword=keyword))
        )
        return int(self.session.scalar(stmt) or 0)

    def get_by_type_id(self, type_id: str) -> CrowdTypeTemplate | None:
        """按模板标识查询。"""

        stmt = select(CrowdTypeTemplate).where(CrowdTypeTemplate.type_id == type_id)
        return self.session.scalar(stmt)

    def list_by_type_ids(self, type_ids: list[str]) -> list[CrowdTypeTemplate]:
        """批量查询模板。"""

        if not type_ids:
            return []

        stmt = select(CrowdTypeTemplate).where(CrowdTypeTemplate.type_id.in_(type_ids))
        return list(self.session.scalars(stmt))

    def delete(self, template: CrowdTypeTemplate) -> None:
        """删除模板记录。"""

        self.session.delete(template)

    def _build_base_query(
        self,
        *,
        include_builtin: bool,
        keyword: str | None,
    ) -> Select[tuple[CrowdTypeTemplate]]:
        return select(CrowdTypeTemplate).where(*self._build_filters(include_builtin=include_builtin, keyword=keyword))

    def _build_filters(self, *, include_builtin: bool, keyword: str | None) -> list[object]:
        filters: list[object] = []

        if not include_builtin:
            # 某些场景只需要用户可编辑模板，直接过滤掉系统内置模板。
            filters.append(CrowdTypeTemplate.is_builtin.is_(False))

        if keyword:
            pattern = f"%{keyword.strip()}%"
            filters.append(
                or_(
                    CrowdTypeTemplate.type_id.ilike(pattern),
                    CrowdTypeTemplate.display_name.ilike(pattern),
                )
            )

        return filters
