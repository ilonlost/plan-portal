export type Status = "planned" | "warning" | "conflict" | "unscheduled";

export interface UserProfile {
  username: string; display_name: string; role: "admin" | "planner" | "master" | "viewer";
  email: string; workshop_code: string | null; line_name: string | null;
  access_label: string; auth_mode: string;
}

export interface SessionMode { auth_mode: string; mock_hint: string | null; }

export interface ScheduleItem {
  id: number; sequence: number; production_date: string | null; marking_date: string | null; line_id: number | null;
  line_code: string | null; line_name: string | null; workshop_code: string | null; workshop_name: string | null;
  product_id: number | null; product_name: string; sku: string; quantity: number; required_hours: number;
  load_percent: number; shift: string; source_quantity: number | null; source_unit: string;
  quantity_kg: number | null; quantity_units: number | null; box_count: number | null; batch_count: number | null;
  schedule_kind: "production" | "cleaning" | "downtime" | "maintenance" | "trial"; duration_hours: number | null;
  mono_group: string | null;
  reason: string | null; actual_quantity_kg: number | null; status: Status; source_kind: "ohl" | "zam" | "generic";
  source: string; locked: boolean; excluded: boolean; due_date: string | null; warnings: string[];
  execution_status: "not_started" | "in_progress" | "completed" | "partially_shipped" | "not_shipped";
  execution_note: string | null; reported_by: string | null; reported_at: string | null;
}

export interface PlanData {
  id: number; name: string; status: string; horizon_start: string; horizon_end: string; updated_at: string; version: number;
  items: ScheduleItem[]; summary: { total: number; planned: number; warnings: number; conflicts: number; unscheduled: number };
}

export interface MatrixCell {
  date: string; planned_hours: number; capacity_hours: number; load_percent: number; gap_hours: number;
  ohl_kg: number; zam_kg: number; items: ScheduleItem[];
}
export interface MatrixLine { id: number; code: string; name: string; cells: MatrixCell[]; }
export interface MatrixWorkshop { code: string; name: string; lines: MatrixLine[]; }
export interface MatrixData {
  plan: { id: number; name: string; status: string; version: number };
  dates: string[]; workshops: MatrixWorkshop[];
}

export interface LineData {
  id: number; code: string; name: string; workshop_code: string; workshop_name: string; status: string;
  working_hours: number; default_capacity: number; capacity_unit: string; priority: number;
  comments?: string; schedule_code: string; schedule_label: string; schedule_anchor_date: string | null;
  schedule_template_id: number | null; production_day_start_hour: number; mail_recipients: string | null;
  csb_line_code: string | null; csb_t5: string; csb_t55: string | null;
  product_count: number; today_load: number;
}
export interface WorkshopData { code: string; name: string; lines: { id: number; code: string; name: string; status: string }[]; }

export interface CatalogRow {
  capability_id: number; product_id: number; sku: string; product_name: string; state: string | null; category: string | null;
  unit_weight_kg: number | null; units_per_box: number | null; box_weight_kg: number | null;
  workshop_code: string; workshop_name: string; line_id: number; line_name: string;
  speed_kg_hour: number; batch_quantum_kg: number | null; min_order_kg: number | null;
  capacity_type: string | null; restrictions: string | null; legacy_quantum_units: number | null;
  legacy_daily_capacity_units: number | null; legacy_capacity_unit: string | null;
  recipe_component_count: number; reference_source: string | null;
  mono_group: string | null;
}

export interface LineScheduleSlot {
  capacity_date: string; day_hours: number; night_hours: number; manual_override: boolean; note: string | null;
}
export interface LineScheduleData {
  line_id: number; line_name: string; workshop_code: string; schedule_code: string; schedule_label: string;
  anchor_date: string; patterns: Record<string, string>; template_id: number | null; production_day_start_hour: number;
  mail_recipients: string; csb_line_code: string; csb_t5: string; csb_t55: string;
  templates: ScheduleTemplate[]; slots: LineScheduleSlot[];
}
export interface ScheduleTemplate { id: number; name: string; description: string | null; pattern: { day_hours: number; night_hours: number }[]; }
export interface SourceFile {
  id: number; file_name: string; template_type: string; status: string; total_rows: number;
  valid_rows: number; invalid_rows: number; imported_at: string;
}
export interface CatalogData {
  summary: { products: number; capabilities: number; lines: number; with_recipes: number };
  rows: CatalogRow[]; sources: SourceFile[];
}

export interface ImportRow {
  row_number: number; sku: string; product_name: string; quantity: number | null; requested_date: string | null;
  due_date: string | null; priority: number; customer: string | null; valid: boolean; errors: string[]; warnings: string[];
  source_quantity: number | null; source_unit: string; quantity_kg: number | null; unit_weight_kg: number | null;
  units_per_box: number | null; box_weight_kg: number | null; box_count: number | null; production_week: number | null;
  exact_date: boolean; line_hint: string | null; speed_kg_hour: number | null; batch_quantum_kg: number | null;
  min_order_kg: number | null; capacity_type: string | null; restrictions: string | null;
  advance_marking: boolean; marking_date: string | null; legacy_quantum_units: number | null;
  legacy_daily_capacity_units: number | null; recipe_component_count: number;
}
export interface ImportPreview {
  file_name: string; mapping_code: string; template_type: string; detected_sheet: string | null; notes: string[];
  total_rows: number; valid_rows: number; invalid_rows: number; columns: string[]; rows: ImportRow[];
}

export interface AuditRow {
  id: number; username: string; action: string; entity_type: string; entity_id: string | null;
  details: Record<string, unknown>; created_at: string;
}
export interface NotificationRow {
  id: number; event_type: string; recipients: string[]; subject: string; status: string;
  error: string | null; created_at: string;
}
export interface IntegrationRow {
  id: number; integration: string; operation: string; target_date: string | null; status: string;
  test_mode: boolean; item_count: number; response: Record<string, unknown>; created_by: string; created_at: string;
}
export interface AdminOverview {
  counts: { plans: number; schedule_items: number; products: number; lines: number };
  active_plan: { id: number; name: string; status: string } | null;
  ldap: { status: string; configured: boolean };
  email: { enabled: boolean; configured: boolean; host: string };
  mail_configuration: MailConfiguration;
  smtp_password_configured: boolean;
  csb: { test_mode: boolean; configured: boolean };
  users: { id: number; username: string; display_name: string; email: string | null; role: string; workshop_code: string | null; line_name: string | null; active: boolean; last_login_at: string | null }[];
  lines: { id: number; workshop_code: string; workshop_name: string; name: string }[];
  recent_audit: AuditRow[]; recent_notifications: NotificationRow[]; recent_integrations: IntegrationRow[];
}
export interface MailConfiguration {
  enabled: boolean; smtp_host: string; smtp_port: number; smtp_from: string; smtp_from_name: string;
  smtp_reply_to: string; smtp_secure: boolean; smtp_require_tls: boolean; notification_emails: string;
  plan_subject: string; plan_intro: string; plan_footer: string; accent_color: string; button_label: string;
}
export interface CsbRun {
  run_id: number; target_date: string; item_count: number; status: string;
  response: { accepted: boolean; mode: string; message: string };
}
