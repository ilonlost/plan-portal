from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PFMappingItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["pf", "filling"] = "pf"
    line_id: int | None = Field(default=None, gt=0)
    speed_kg_hour: Decimal | None = Field(default=None, gt=0, le=1000000)
    lead_days: int = Field(default=2, ge=0, le=365, strict=True)
    note: str = Field(default="", max_length=1000)
    enabled: bool = True


class PFMappingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    gp_sku: str = Field(min_length=1, max_length=80)
    gp_line_id: int | None = Field(default=None, gt=0)
    gp_speed_kg_hour: Decimal | None = Field(default=None, gt=0, le=1000000)
    revision: int = Field(ge=0)
    items: list[PFMappingItem] = Field(max_length=100)
