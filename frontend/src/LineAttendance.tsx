import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type LineAttendance } from "./api";

const Attendance = createContext<LineAttendance | undefined>(undefined);
const moscowDate = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow" });

export function LineAttendanceProvider({ children }: { children: ReactNode }) {
  const [attendance, setAttendance] = useState<LineAttendance>();
  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    const refresh = async () => {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const result = await api.lineAttendance(controller.signal);
        if (!controller.signal.aborted) setAttendance(result);
      } catch {
        if (!controller.signal.aborted) setAttendance(previous => previous && { ...previous, status: "unavailable" });
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 60_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);
  return <Attendance.Provider value={attendance}>{children}</Attendance.Provider>;
}

export function LineAttendanceCount({ lineId }: { lineId: number }) {
  const data = useContext(Attendance);
  if (!data || !(lineId in data.counts)) return null;
  const count = data.counts[lineId];
  let value = "нет данных";
  if (data.status === "ok" && data.date === moscowDate.format(new Date()) && count != null) value = `${count} чел.`;
  return <small title="Сотрудники линии, вошедшие сегодня через санпропускник по данным СКУД">Пришли сегодня · {value}</small>;
}
