import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api";
import type { LineCommentEntry, UserProfile } from "./types";

export function LineConversation({ lineId, lineName, date, user, comments, onSaved }: { lineId: number; lineName: string; date: string; user: UserProfile; comments: LineCommentEntry[]; onSaved: () => Promise<void> }) {
  const [open, setOpen] = useState(false); const [text, setText] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const [sent, setSent] = useState<LineCommentEntry[]>([]); const endRef = useRef<HTMLDivElement>(null); const triggerRef = useRef<HTMLButtonElement>(null); const modalRef = useRef<HTMLElement>(null);
  const messages = useMemo(() => [...new Map([...comments, ...sent].map(row => [row.id, row])).values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.split("-")[0].localeCompare(b.id.split("-")[0]) || Number(a.id.split("-")[1]) - Number(b.id.split("-")[1])), [comments, sent]);
  const mayWrite = user.role !== "viewer" && (user.role !== "master" || user.line_name === lineName);
  useEffect(() => { if (open) endRef.current?.scrollIntoView({ block: "nearest" }); }, [open, messages.length]);
  useEffect(() => {
    if (!open) return;
    modalRef.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); triggerRef.current?.focus(); }
      if (event.key === "Tab") {
        const controls = Array.from(modalRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), textarea:not(:disabled), [tabindex="0"]') || []);
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === modalRef.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [open]);
  const close = () => { setOpen(false); triggerRef.current?.focus(); };
  const send = async () => {
    if (!text.trim() || busy) return;
    setBusy(true); setError("");
    try { const result = await api.saveLineComment(lineId, date, text); setSent(current => [...current, result.comment]); setText(""); await onSaved().catch(() => setError("Сообщение отправлено. Не удалось обновить переписку.")); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось отправить комментарий"); }
    finally { setBusy(false); }
  };
  return <div className="line-comment-box line-conversation-preview">
    <button ref={triggerRef} className="conversation-trigger" onClick={() => setOpen(true)}>{mayWrite ? "+ Добавить комментарий" : "Комментарии"}{messages.length > 0 && <span>{messages.length}</span>}</button>
    {messages.length ? <button className="conversation-last" onClick={() => setOpen(true)}><b>{messages[messages.length - 1].author_name}</b><p>{messages[messages.length - 1].text}</p><small>Открыть переписку</small></button> : <small>Комментариев пока нет</small>}
    {open && createPortal(<div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}><section ref={modalRef} tabIndex={-1} className="modal line-conversation-modal" role="dialog" aria-modal="true" aria-labelledby={`conversation-title-${lineId}`}><header><div><small>ПЕРЕПИСКА ПО ЛИНИИ</small><h2 id={`conversation-title-${lineId}`}>{lineName}</h2><p>{new Date(`${date}T12:00:00`).toLocaleDateString("ru-RU")} · {messages.length} сообщений</p></div><button aria-label="Закрыть переписку" onClick={close}>×</button></header>
      <div className="conversation-messages">{messages.length ? messages.map(row => <article key={row.id} className={row.author_username === user.username ? "own" : ""}><header><b>{row.author_name}</b><time dateTime={row.created_at}>{new Date(row.created_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</time></header><p>{row.text}</p></article>) : <p className="conversation-empty">Начните обсуждение работы линии за этот день.</p>}<div ref={endRef} /></div>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {mayWrite ? <form className="conversation-compose" onSubmit={event => { event.preventDefault(); void send(); }}><label>Комментарий<textarea maxLength={2000} value={text} onChange={event => setText(event.target.value)} placeholder="Напишите сообщение…" rows={3} /></label><footer><small>{text.length} / 2000</small><button className="button primary" disabled={busy || !text.trim()}>{busy ? "Отправка…" : "Отправить"}</button></footer></form> : <p className="conversation-read-only">Переписка доступна для просмотра.</p>}
    </section></div>, document.body)}
  </div>;
}
