import type {
  AdminOverview, CatalogData, CsbRun, ImportPreview, LineData, MatrixData,
  MailConfiguration, PlanData, SessionMode, UserProfile, WorkshopData,
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
  mailPreview: (start: string, end: string) => request<{ html: string; item_count: number; start: string; end: string }>(`/admin/mail-preview?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),
  emailPlan: (planId: number, recipients: string[], start: string, end: string) => request<{ ok: boolean; status: string; recipients: string[]; item_count: number; error: string | null }>(`/plans/${planId}/email`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ recipients, start, end }),
  }),
  deletePlanData: (confirmation: string) => request<{ ok: boolean; plans_deleted: number; schedule_items_deleted: number }>("/admin/delete-plan-data", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation }),
  }),
  sendCsbNextDay: (targetDate?: string) => request<CsbRun>("/integrations/csb/next-day", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target_date: targetDate || null }),
  }),
  exportUrl: (planId: number) => `${API}/plans/${planId}/export.xlsx`,
};
