import { useState } from "react";
import { api } from "./api";

export function ServiceAccountCreate({ onSaved }: { onSaved: (id: number) => Promise<void> }) {
  const [login, setLogin] = useState("svc."); const [name, setName] = useState(""); const [password, setPassword] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  return <form className="access-create service-account-form" onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); try { const result = await api.createServiceAccount({ username: login, display_name: name, password }); setPassword(""); await onSaved(result.id); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось создать учётную запись"); } finally { setBusy(false); } }}>
    <div className="full"><h3>Сервисная учётная запись без AD</h3><p>Только просмотр. Учётка действует в PLAN PORTAL; корпоративный вход AD / ART PORTAL сохраняется.</p></div><label>Логин<input required value={login} autoComplete="off" maxLength={64} onChange={e => setLogin(e.target.value)} /></label><label>Название<input value={name} required maxLength={160} onChange={e => setName(e.target.value)} placeholder="Экран производства" /></label><label>Пароль, от 12 символов<input type="password" autoComplete="new-password" minLength={12} maxLength={256} required value={password} onChange={e => setPassword(e.target.value)} /></label>{error && <p className="inline-error full" role="alert">{error}</p>}<button className="button primary" disabled={busy}>Создать учётку для просмотра</button>
  </form>;
}

export function ServicePasswordReset({ userId }: { userId: number }) {
  const [password, setPassword] = useState(""); const [notice, setNotice] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  return <form className="service-password-reset" onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); setNotice(""); try { await api.resetServicePassword(userId, password); setPassword(""); setNotice("Пароль изменён. Все прежние сервисные сессии завершены."); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось изменить пароль"); } finally { setBusy(false); } }}><label>Новый пароль сервисной учётки<input type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={password} onChange={e => setPassword(e.target.value)} /></label><button className="button secondary" disabled={busy}>Сменить пароль и завершить сессии</button>{notice && <p className="good" role="status">{notice}</p>}{error && <p className="inline-error">{error}</p>}</form>;
}
