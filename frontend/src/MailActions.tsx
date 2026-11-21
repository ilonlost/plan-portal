import { useState } from "react";
import { api } from "./api";
import type { AdminOverview, MailAction, MailRule } from "./types";
import "./MailActions.css";

const groups = {
  planning: "Планирование и исполнение",
  production_management: "Производственные настройки и импорт",
  it: "Администрирование и обратная связь",
  manual: "Отправка плана вручную",
};

function MailActionRow({ action, lines, onSaved }: { action: MailAction; lines: AdminOverview["lines"]; onSaved: () => Promise<void> }) {
  const [editing, setEditing] = useState<{ previous: MailRule; draft: MailRule } | null>(null);
  const draft = editing?.draft ?? action.rule;
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const changed = draft.mode !== action.rule.mode || (draft.mode === "custom" && draft.recipients !== action.rule.recipients);
  function edit(next: MailRule) {
    setEditing({ previous: editing?.previous ?? action.rule, draft: next });
    setFeedback("");
  }
  async function save() {
    setBusy(true);
    try {
      await api.updateMailAction(action.key, draft, editing?.previous ?? action.rule);
      await onSaved();
      setEditing(null);
      setFeedback("Сохранено");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Не удалось сохранить правило");
    } finally {
      setBusy(false);
    }
  }
  return <div className="mail-action-row">
    <div><strong>{action.title}</strong><p>{action.content}</p></div>
    <div className="mail-action-controls">
      <select aria-label={`Правило — ${action.title}`} value={draft.mode} disabled={busy}
        onChange={e => edit({ ...draft, mode: e.target.value as MailRule["mode"] })}>
        <option value="inherit">По общим настройкам</option>
        <option value="custom">Только указанные адреса</option>
        <option value="off">Отключить письмо</option>
      </select>
      {draft.mode === "custom" && <textarea rows={2} maxLength={4000} aria-label={`Получатели — ${action.title}`}
        placeholder="Адреса через запятую" disabled={busy} value={draft.recipients}
        onChange={e => edit({ ...draft, recipients: e.target.value })} />}
      {draft.mode === "inherit" && <div className="mail-action-addresses">
        <span>{action.inherited.join(", ") || "Постоянные адреса не заданы"}</span>
        {action.group === "manual" && <small>{action.key === "plan_email_production"
          ? "Также получают адресаты выбранных линий и адреса, добавленные при отправке."
          : "Можно добавить адреса при отправке."}</small>}
        {action.key === "plan_email_production" && lines.length > 0 && <details>
          <summary>Адреса действующих линий</summary>
          {lines.map(line => <div key={line.id}><b>{line.workshop_name} · {line.name}</b> — {line.mail_recipients}</div>)}
        </details>}
      </div>}
      {changed && <button className="button primary" disabled={busy} onClick={() => void save()}>Сохранить правило</button>}
      {feedback && <small role="status">{feedback}</small>}
    </div>
  </div>;
}

export function MailActions({ actions, lines, enabled, onSaved }: { actions: MailAction[]; lines: AdminOverview["lines"]; enabled: boolean; onSaved: () => Promise<void> }) {
  const [group, setGroup] = useState("");
  const [query, setQuery] = useState("");
  const search = query.trim().toLocaleLowerCase("ru");
  const addressedLines = lines.filter(line => line.mail_recipients?.trim());
  const visible = actions.filter(action => (!group || action.group === group) &&
    [action.title, action.content, action.rule.mode === "custom" ? action.rule.recipients : action.rule.mode === "inherit" ? action.inherited.join(" ") : "",
     action.key === "plan_email_production" && action.rule.mode === "inherit" ? addressedLines.map(line => line.mail_recipients).join(" ") : ""]
      .some(text => text.toLocaleLowerCase("ru").includes(search)));
  return <section className="card admin-panel mail-actions">
    <header><h2>Рассылки по действиям</h2><p>Для каждого действия можно выбрать получателей или отключить письмо.</p></header>
    {!enabled && <p className="integration-state warn">Общая отправка выключена. Включить её можно во вкладке «SMTP и шаблон».</p>}
    <div className="form-grid"><label>Поиск<input type="search" placeholder="Действие или адрес" value={query} onChange={e => setQuery(e.target.value)} /></label>
      <label>Группа<select value={group} onChange={e => setGroup(e.target.value)}><option value="">Все группы</option>
        {Object.entries(groups).map(([key, title]) => <option key={key} value={key}>{title}</option>)}</select></label></div>
    {Object.entries(groups).map(([key, title]) => {
      const rows = visible.filter(action => action.group === key);
      return rows.length > 0 && <section key={key}><h3>{title}</h3>
        {rows.map(action => <MailActionRow key={action.key} action={action} lines={addressedLines} onSaved={onSaved} />)}</section>;
    })}
    {!visible.length && <p>Действий по этому запросу нет.</p>}
  </section>;
}
