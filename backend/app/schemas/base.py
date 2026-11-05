"""Schema 基类。"""

from pydantic import BaseModel, ConfigDict


class SchemaModel(BaseModel):
    """统一的 Schema 基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
