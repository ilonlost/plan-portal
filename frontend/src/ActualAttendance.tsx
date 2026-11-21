import { useState } from "react";
import { api } from "./api";
import type { LineActualAttendance } from "./types";
import "./ActualAttendance.css";

const moscowDay = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow" });
const mailMessage: Record<string, string> = {
  sent: "Сохранено. Письмо отправлено.",
  partial: "Сохранено. Почта приняла письмо для части получателей.",
  failed: "Сохранено. Не удалось отправить письмо.",
  skipped: "Сохранено. Рассылка отключена или нет получателей.",
};

type Props = {
  lineId: number;
  date: string;
  canEdit: boolean;
  record?: LineActualAttendance;
};

export function ActualAttendance({ lineId, date, canEdit, record }: Props) {
  const [draft, setDraft] = useState<string | null>(null);
  const [saved, setSaved] = useState<LineActualAttendance>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const latest = saved && (!record || Date.parse(saved.updated_at) > Date.parse(record.updated_at)) ? saved : record;
  const value = draft ?? latest?.headcount.toString() ?? "";
  const headcount = Number(value);
  const valid = value !== "" && Number.isInteger(headcount) && headcount >= 0 && headcount <= 2147483647;
  const editable = canEdit && date <= moscowDay.format(new Date());
  if (!editable && !latest) return null;

  async function save() {
    if (!editable || !valid || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.saveActualAttendance(lineId, date, headcount);
      setSaved(result.attendance);
      setDraft(null);
      setNotice(mailMessage[result.mail_status] ?? "Сохранено.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить явку");
    } finally {
      setBusy(false);
    }
  }

  return <div className="actual-attendance">
    {editable ? <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <label>Пришли по факту, чел.
        <input type="number" required min={0} max={2147483647} step={1} value={value}
          disabled={busy} onChange={event => { setDraft(event.target.value); setNotice(""); }} />
      </label>
      <button className="button secondary" disabled={!valid || busy || latest?.headcount === headcount}>
        {busy ? "Сохранение…" : "Сохранить"}
      </button>
    </form> : <b>Пришли по факту · {latest?.headcount} чел.</b>}
    {latest && <small>Заполнил: {latest.author_name}</small>}
    {notice && <small role="status">{notice}</small>}
    {error && <small className="bad" role="alert">{error}</small>}
  </div>;
}
