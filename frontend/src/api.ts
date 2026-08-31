import type {
  AdminOverview, CatalogData, CsbRun, ImportPreview, LineData, MatrixData,
  LineScheduleData, MailConfiguration, PlanData, ScheduleTemplate, SessionMode, UserProfile, WorkshopData,
} from "./types";

const API = import.meta.env.VITE_API_URL || "/api";

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API}${path}`, { ...options, credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Ошибка сервера" }));
    throw new ApiError(body.detail || "Ошибка сервера", response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

async function optional<T>(path: string): Promise<T | null> {
  try { return await request<T>(path); }
  catch (reason) { if (reason instanceof ApiError && reason.status === 404) return null; throw reason; }
}

export const api = {
  sessionMode: () => request<SessionMode>("/session/mode"),
  login: (username: string, password: string) => request<{ user: UserProfile; auth_mode: string }>("/session/login", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }),
  }),
  logout: () => request<{ ok: boolean }>("/session/logout", { method: "POST" }),
  me: () => request<UserProfile>("/session/me"),
  activePlan: () => optional<PlanData>("/plans/active"),
  matrix: (params = "") => optional<MatrixData>(`/plans/active/matrix${params ? `?${params}` : ""}`),
  lines: () => request<LineData[]>("/lines"),
  lineSchedule: (lineId: number, start: string, days = 14) => request<LineScheduleData>(`/lines/${lineId}/schedule?start=${encodeURIComponent(start)}&days=${days}`),
  updateLineSchedule: (lineId: number, data: { schedule_code: string; anchor_date: string; template_id?: number | null; mail_recipients?: string; csb_line_code?: string; csb_t5?: string; csb_t55?: string; slots: { capacity_date: string; day_hours: number; night_hours: number; note?: string | null }[] }) => request<{ ok: boolean; plan_recalculated: boolean }>(`/lines/${lineId}/schedule`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  }),
  scheduleTemplates: () => request<ScheduleTemplate[]>("/lines/schedule-templates"),
  createScheduleTemplate: (data: { name: string; description?: string; pattern: { day_hours: number; night_hours: number }[] }) => request<ScheduleTemplate>("/lines/schedule-templates", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }),
  workshops: () => request<WorkshopData[]>("/lines/workshops"),
  catalog: (params = "") => request<CatalogData>(`/catalog${params ? `?${params}` : ""}`),
  updateCapability: (id: number, data: object) => request<{ ok: boolean }>(`/catalog/capabilities/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  }),
  updateItem: (planId: number, itemId: number, data: object) => request<PlanData>(`/plans/${planId}/items/${itemId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  }),
  updateExecution: (planId: number, itemId: number, status: string, note?: string) => request<PlanData>(`/plans/${planId}/items/${itemId}/execution-status`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status, note }),
  }),
  createEvent: (planId: number, data: object) => request<PlanData>(`/plans/${planId}/events`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  }),
  createManualTask: (planId: number, data: object) => request<PlanData>(`/plans/${planId}/items`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }),
  manualProducts: (lineId: number) => request<{ product_id: number; sku: string; name: string; speed_kg_hour: number }[]>(`/catalog/manual-products?line_id=${lineId}`),
  previewImport: async (file: File) => {
    const data = new FormData(); data.append("file", file);
    return request<ImportPreview>("/imports/preview", { method: "POST", body: data });
  },
  confirmImport: (preview: ImportPreview) => request<{ order_id: number; plan: PlanData | null; reference_updated?: number }>("/imports/confirm", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preview, create_plan: true, merge_into_active: true }),
  }),
  adminOverview: () => request<AdminOverview>("/admin/overview"),
  updateUserAccess: (userId: number, role: string, lineId: number | null, active = true) => request<{ ok: boolean }>(`/admin/users/${userId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role, line_id: lineId, active }),
  }),
  createUserAccess: (data: { username: string; display_name: string; email: string; role: string; line_id: number | null; active: boolean }) => request<{ ok: boolean; id: number }>("/admin/users", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  }),
  updateMailConfiguration: (configuration: MailConfiguration) => request<{ ok: boolean; configuration: MailConfiguration }>("/admin/mail-configuration", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ configuration }),
  }),
  mailPreview: (start: string, end: string, lineIds: number[] = []) => request<{ html: string; item_count: number; start: string; end: string }>(`/admin/mail-preview?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}${lineIds.map(value => `&line_ids=${value}`).join("")}`),
  emailPlan: (planId: number, recipients: string[], start: string, end: string, lineIds: number[] = []) => request<{ ok: boolean; status: string; recipients: string[]; item_count: number; error: string | null }>(`/plans/${planId}/email`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ recipients, start, end, line_ids: lineIds }),
  }),
  deletePlanData: (confirmation: string) => request<{ ok: boolean; plans_deleted: number; schedule_items_deleted: number }>("/admin/delete-plan-data", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation }),
  }),
  sendCsbNextDay: (targetDate?: string) => request<CsbRun>("/integrations/csb/next-day", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target_date: targetDate || null }),
  }),
  csbDownloadUrl: (targetDate?: string) => `${API}/integrations/csb/download${targetDate ? `?target_date=${encodeURIComponent(targetDate)}` : ""}`,
  exportUrl: (planId: number) => `${API}/plans/${planId}/export.xlsx`,
};
