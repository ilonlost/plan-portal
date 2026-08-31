from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PlanStatus(str, enum.Enum):
    DRAFT = "draft"
    CALCULATED = "calculated"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    EXPORTED = "exported"
    ARCHIVED = "archived"


class ScheduleStatus(str, enum.Enum):
    PLANNED = "planned"
    WARNING = "warning"
    CONFLICT = "conflict"
    UNSCHEDULED = "unscheduled"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(40), default="planner")
    email: Mapped[str | None] = mapped_column(String(200))
    workshop_code: Mapped[str | None] = mapped_column(String(20))
    line_name: Mapped[str | None] = mapped_column(String(160))
    ldap_groups: Mapped[list] = mapped_column(JSON, default=list)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20), default="шт")
    unit_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    units_per_box: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    box_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    state: Mapped[str | None] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(120))
    short_name: Mapped[str | None] = mapped_column(String(200))
    legacy_quantum_units: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    legacy_daily_capacity_units: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    legacy_capacity_unit: Mapped[str | None] = mapped_column(String(40))
    recipe_component_count: Mapped[int] = mapped_column(Integer, default=0)
    reference_source: Mapped[str | None] = mapped_column(String(240))
    mono_group: Mapped[str | None] = mapped_column(String(160), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    capabilities: Mapped[list[LineCapability]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductionLine(Base):
    __tablename__ = "production_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    workshop_code: Mapped[str] = mapped_column(String(20), default="UNASSIGNED", index=True)
    workshop_name: Mapped[str] = mapped_column(String(80), default="Не распределено")
    status: Mapped[str] = mapped_column(String(30), default="active")
    working_hours: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=8)
    default_capacity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    capacity_unit: Mapped[str] = mapped_column(String(30), default="часов/день")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    comments: Mapped[str | None] = mapped_column(Text)
    schedule_code: Mapped[str] = mapped_column(String(40), default="day_daily")
    schedule_anchor_date: Mapped[date | None] = mapped_column(Date)
    schedule_template_id: Mapped[int | None] = mapped_column(ForeignKey("line_schedule_templates.id", ondelete="SET NULL"))
    custom_schedule_pattern: Mapped[list] = mapped_column(JSON, default=list)
    production_day_start_hour: Mapped[int] = mapped_column(Integer, default=0)
    mail_recipients: Mapped[str | None] = mapped_column(Text)
    csb_line_code: Mapped[str | None] = mapped_column(String(40))
    csb_t5: Mapped[str] = mapped_column(String(20), default="4")
    csb_t55: Mapped[str | None] = mapped_column(String(80))
    capabilities: Mapped[list[LineCapability]] = relationship(back_populates="line", cascade="all, delete-orphan")
    capacities: Mapped[list[LineCapacity]] = relationship(back_populates="line", cascade="all, delete-orphan")


class LineScheduleTemplate(Base):
    __tablename__ = "line_schedule_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str | None] = mapped_column(String(500))
    pattern: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LineCapability(Base):
    __tablename__ = "line_capabilities"
    __table_args__ = (UniqueConstraint("line_id", "product_id", name="uq_line_product"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("production_lines.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    units_per_hour: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    speed_unit: Mapped[str] = mapped_column(String(30), default="кг/час")
    min_batch: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    max_batch: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    batch_quantum_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    min_order_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    capacity_type: Mapped[str | None] = mapped_column(String(80))
    restrictions: Mapped[str | None] = mapped_column(Text)
    technological_constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    line: Mapped[ProductionLine] = relationship(back_populates="capabilities")
    product: Mapped[Product] = relationship(back_populates="capabilities")


class LineCapacity(Base):
    __tablename__ = "line_capacities"
    __table_args__ = (UniqueConstraint("line_id", "capacity_date", "shift", name="uq_line_capacity_slot"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("production_lines.id", ondelete="CASCADE"), index=True)
    capacity_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(30), default="day")
    available_hours: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=8)
    max_units: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(String(300))
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    line: Mapped[ProductionLine] = relationship(back_populates="capacities")


class ProductionCalendar(Base):
    __tablename__ = "production_calendar"
    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    is_working_day: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(String(300))


class ImportedOrder(Base):
    __tablename__ = "imported_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(30), default="preview")
    mapping_code: Mapped[str] = mapped_column(String(80), default="default_v1")
    template_type: Mapped[str] = mapped_column(String(50), default="generic")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list[DemandItem]] = relationship(back_populates="order", cascade="all, delete-orphan")


class DemandItem(Base):
    __tablename__ = "demand_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("imported_orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    source_row: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(String(80), index=True)
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    source_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    source_unit: Mapped[str] = mapped_column(String(30), default="кг")
    quantity_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    box_count: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    production_week: Mapped[int | None] = mapped_column(Integer)
    exact_date: Mapped[bool] = mapped_column(Boolean, default=False)
    source_kind: Mapped[str] = mapped_column(String(30), default="generic", index=True)
    source_plan_date: Mapped[date | None] = mapped_column(Date)
    marking_date: Mapped[date | None] = mapped_column(Date)
    advance_production: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_date: Mapped[date] = mapped_column(Date, index=True)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    customer: Mapped[str | None] = mapped_column(String(160))
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    order: Mapped[ImportedOrder] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()


class ProductionPlan(Base):
    __tablename__ = "production_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[PlanStatus] = mapped_column(Enum(PlanStatus), default=PlanStatus.DRAFT)
    horizon_start: Mapped[date] = mapped_column(Date)
    horizon_end: Mapped[date] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    versions: Mapped[list[ProductionPlanVersion]] = relationship(back_populates="plan", cascade="all, delete-orphan")
    schedule_items: Mapped[list[ProductionScheduleItem]] = relationship(back_populates="plan", cascade="all, delete-orphan")


class ProductionPlanVersion(Base):
    __tablename__ = "production_plan_versions"
    __table_args__ = (UniqueConstraint("plan_id", "version_number", name="uq_plan_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("production_plans.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    change_type: Mapped[str] = mapped_column(String(40))
    comment: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    plan: Mapped[ProductionPlan] = relationship(back_populates="versions")


class ProductionScheduleItem(Base):
    __tablename__ = "production_schedule_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("production_plans.id", ondelete="CASCADE"), index=True)
    demand_item_id: Mapped[int | None] = mapped_column(ForeignKey("demand_items.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("production_lines.id"), index=True)
    production_date: Mapped[date | None] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(30), default="day")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    source_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    source_unit: Mapped[str] = mapped_column(String(30), default="кг")
    quantity_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    box_count: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    batch_count: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    schedule_kind: Mapped[str] = mapped_column(String(30), default="production")
    duration_hours: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    reason: Mapped[str | None] = mapped_column(Text)
    actual_quantity_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    source_kind: Mapped[str] = mapped_column(String(30), default="generic", index=True)
    marking_date: Mapped[date | None] = mapped_column(Date)
    execution_status: Mapped[str] = mapped_column(String(30), default="not_started")
    execution_note: Mapped[str | None] = mapped_column(Text)
    reported_by: Mapped[str | None] = mapped_column(String(120))
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    required_hours: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=0)
    load_percent: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=0)
    status: Mapped[ScheduleStatus] = mapped_column(Enum(ScheduleStatus), default=ScheduleStatus.PLANNED)
    source: Mapped[str] = mapped_column(String(40), default="auto")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    plan: Mapped[ProductionPlan] = relationship(back_populates="schedule_items")
    product: Mapped[Product | None] = relationship()
    line: Mapped[ProductionLine | None] = relationship()
    demand_item: Mapped[DemandItem | None] = relationship()


class PlanningRule(Base):
    __tablename__ = "planning_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)


class ImportFile(Base):
    __tablename__ = "import_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    imported_order_id: Mapped[int | None] = mapped_column(ForeignKey("imported_orders.id"))
    original_name: Mapped[str] = mapped_column(String(240))
    stored_path: Mapped[str | None] = mapped_column(String(500))
    checksum: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExportFile(Base):
    __tablename__ = "export_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("production_plans.id"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("production_plan_versions.id"))
    original_name: Mapped[str] = mapped_column(String(240))
    stored_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthAuditEvent(Base):
    __tablename__ = "auth_audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(120), index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    ip_address: Mapped[str | None] = mapped_column(String(80))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    auth_method: Mapped[str] = mapped_column(String(30), default="ldap")
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    message_id: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntegrationRun(Base):
    __tablename__ = "integration_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    integration: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30))
    test_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PortalSetting(Base):
    __tablename__ = "portal_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
