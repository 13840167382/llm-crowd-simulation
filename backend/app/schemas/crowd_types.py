"""人群类型模板相关 Schema 定义。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, StringConstraints, model_validator

from backend.app.schemas.base import SchemaModel

TypeId = Annotated[
    str,
    StringConstraints(
        min_length=2,
        max_length=32,
        pattern=r"^[a-z0-9_]+$",
    ),
]
DisplayName = Annotated[str, StringConstraints(min_length=1, max_length=32)]
ColorHex = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]


class AttractionWeights(SchemaModel):
    """各功能区吸引力权重。"""

    exhibit: float = Field(..., ge=0.0, le=1.0, description="展陈区吸引权重")
    commercial: float = Field(..., ge=0.0, le=1.0, description="商业区吸引权重")
    rest: float = Field(..., ge=0.0, le=1.0, description="休息区吸引权重")
    service: float = Field(..., ge=0.0, le=1.0, description="服务区吸引权重")
    plaza: float = Field(..., ge=0.0, le=1.0, description="广场区吸引权重")
    passage: float = Field(..., ge=0.0, le=1.0, description="通道区吸引权重")
    exit_final: float = Field(..., ge=0.0, le=1.0, description="最终出口吸引权重")


class CrowdTypeMutableFields(SchemaModel):
    """可由客户端配置的人群类型字段。"""

    display_name: DisplayName = Field(..., description="显示名称")
    color_hex: ColorHex = Field(..., description="视觉标识色")
    base_speed: float = Field(..., ge=0.1, le=5.0, description="基础移动速度")
    social_distance: float = Field(..., ge=0.3, le=5.0, description="社交避让半径，单位米")
    tolerance_duration_ms: int = Field(
        ...,
        ge=1000,
        le=300000,
        description="基础停留容忍时长，单位毫秒",
    )
    attraction_weights: AttractionWeights = Field(..., description="各功能区吸引权重")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展自定义字段")


class CrowdTypeTemplate(CrowdTypeMutableFields):
    """人群类型模板详情。"""

    type_id: TypeId = Field(..., description="模板唯一标识")
    is_builtin: bool = Field(..., description="是否为系统内置模板")
    created_at_ms: int = Field(..., ge=0, description="创建时间戳，毫秒")
    updated_at_ms: int = Field(..., ge=0, description="更新时间戳，毫秒")


class CrowdTypeListData(SchemaModel):
    """人群类型模板列表响应数据。"""

    total_count: int = Field(..., ge=0, description="模板总数")
    templates: list[CrowdTypeTemplate] = Field(default_factory=list, description="模板列表")


class CrowdTypeCreateRequest(CrowdTypeMutableFields):
    """创建人群类型模板请求。"""

    type_id: TypeId = Field(..., description="模板唯一标识，仅允许小写字母、数字和下划线")


class CrowdTypeUpdateRequest(SchemaModel):
    """更新人群类型模板请求。"""

    display_name: DisplayName | None = Field(default=None, description="显示名称")
    color_hex: ColorHex | None = Field(default=None, description="视觉标识色")
    base_speed: float | None = Field(default=None, ge=0.1, le=5.0, description="基础移动速度")
    social_distance: float | None = Field(default=None, ge=0.3, le=5.0, description="社交避让半径")
    tolerance_duration_ms: int | None = Field(
        default=None,
        ge=1000,
        le=300000,
        description="基础停留容忍时长，单位毫秒",
    )
    attraction_weights: AttractionWeights | None = Field(default=None, description="各功能区吸引权重")
    metadata: dict[str, Any] | None = Field(default=None, description="扩展自定义字段")

    @model_validator(mode="after")
    def validate_not_empty(self) -> "CrowdTypeUpdateRequest":
        """禁止提交空更新。"""

        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError("更新请求至少需要包含一个字段")
        return self


class CrowdTypeCloneRequest(SchemaModel):
    """复制人群类型模板请求。"""

    new_type_id: TypeId = Field(..., description="新模板唯一标识")
    new_display_name: DisplayName | None = Field(
        default=None,
        description='新模板显示名称，不填时默认使用"{原名称}-副本"',
    )


class CrowdTypeDeleteData(SchemaModel):
    """删除人群类型模板响应数据。"""

    type_id: TypeId = Field(..., description="被删除的模板标识")
    deleted: bool = Field(..., description="是否删除成功")
