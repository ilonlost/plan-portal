import { useState } from "react";
import { api } from "./api";

export function DirectoryAvatar({ username, name, service = false }: { username: string; name: string; service?: boolean }) {
  const [failed, setFailed] = useState(false);
  const initials = name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase() || "?";
  return <span className="directory-avatar" aria-hidden="true">
    {initials}
    {!service && !failed && <img key={username} src={api.directoryPhotoUrl(username)} alt="" loading="lazy" onError={() => setFailed(true)} />}
  </span>;
}
