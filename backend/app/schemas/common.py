from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProductBrief(ORMModel):
    id: int
    sku: str
    name: str
    unit: str
    unit_weight_kg: Decimal | None = None
    units_per_box: Decimal | None = None
    box_weight_kg: Decimal | None = None


class LineBrief(ORMModel):
    id: int
    code: str
    name: str


class LineOut(ORMModel):
    id: int
    code: str
    name: str
    workshop_code: str
    workshop_name: str
    status: str
    working_hours: Decimal
    default_capacity: Decimal
    capacity_unit: str
    priority: int
    comments: str | None = None
    product_count: int = 0
    today_load: float = 0


class CapacityOut(ORMModel):
    id: int
    line_id: int
    line_name: str
    capacity_date: date
    shift: str
    available_hours: Decimal
    max_units: Decimal | None = None
    available: bool
    note: str | None = None
    load_percent: float = 0


class ScheduleItemOut(ORMModel):
    id: int
    sequence: int = 0
    production_date: date | None
    shift: str = "day"
    line_id: int | None
    line_code: str | None
    line_name: str | None
    workshop_code: str | None = None
    workshop_name: str | None = None
    product_id: int | None
    product_name: str
    sku: str
    mono_group: str | None = None
    quantity: Decimal
    source_quantity: Decimal | None = None
    source_unit: str = "кг"
    quantity_units: Decimal | None = None
    quantity_kg: Decimal | None = None
    box_count: Decimal | None = None
    batch_count: Decimal | None = None
    schedule_kind: str = "production"
    duration_hours: Decimal | None = None
    reason: str | None = None
    actual_quantity_kg: Decimal | None = None
    source_kind: str = "generic"
    marking_date: date | None = None
    execution_status: str = "not_started"
    execution_note: str | None = None
    reported_by: str | None = None
    reported_at: datetime | None = None
    required_hours: Decimal
    load_percent: Decimal
    status: str
    source: str
    locked: bool
    excluded: bool
    due_date: date | None
    warnings: list[str] = Field(default_factory=list)


class PlanOut(ORMModel):
    id: int
    name: str
    status: str
    horizon_start: date
    horizon_end: date
    updated_at: datetime
    version: int
    items: list[ScheduleItemOut]
    summary: dict


class ScheduleItemUpdate(BaseModel):
    production_date: date | None = None
    line_id: int | None = None
    shift: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    locked: bool | None = None
    excluded: bool | None = None
    comment: str | None = None


class ScheduleEventCreate(BaseModel):
    line_id: int
    production_date: date
    shift: str = "day"
    schedule_kind: str
    duration_hours: Decimal = Field(gt=0, le=24)
    reason: str = Field(min_length=2, max_length=500)


class PlanApprovalRequest(BaseModel):
    comment: str | None = None


class ExecutionStatusUpdate(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=500)


class ImportRow(BaseModel):
    row_number: int
    sku: str = ""
    product_name: str = ""
    quantity: Decimal | None = None
    source_quantity: Decimal | None = None
    source_unit: str = "кг"
    quantity_kg: Decimal | None = None
    unit_weight_kg: Decimal | None = None
    units_per_box: Decimal | None = None
    box_weight_kg: Decimal | None = None
    box_count: Decimal | None = None
    production_week: int | None = None
    exact_date: bool = False
    line_hint: str | None = None
    speed_kg_hour: Decimal | None = None
    batch_quantum_kg: Decimal | None = None
    min_order_kg: Decimal | None = None
    capacity_type: str | None = None
    restrictions: str | None = None
    available_hours: Decimal | None = None
    line_status: str | None = None
    state: str | None = None
    category: str | None = None
    short_name: str | None = None
    advance_marking: bool = False
    source_plan_date: date | None = None
    marking_date: date | None = None
    legacy_quantum_units: Decimal | None = None
    legacy_daily_capacity_units: Decimal | None = None
    legacy_capacity_unit: str | None = None
    recipe_component_count: int = 0
    reference_source: str | None = None
    requested_date: date | None = None
    due_date: date | None = None
    priority: int = 100
    customer: str | None = None
    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ImportPreview(BaseModel):
    file_name: str
    mapping_code: str
    template_type: str = "generic"
    detected_sheet: str | None = None
    notes: list[str] = Field(default_factory=list)
    total_rows: int
    valid_rows: int
    invalid_rows: int
    columns: list[str]
    rows: list[ImportRow]


class ImportConfirmRequest(BaseModel):
    preview: ImportPreview
    create_plan: bool = True
    merge_into_active: bool = True
