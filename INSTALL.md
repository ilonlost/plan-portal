# Установка PLAN PORTAL в Docker

## Linux-сервер

```bash
git clone --branch codex/production-plan-v2 https://github.com/ilonlost/art-portal.git
cd art-portal/production-planning
cp .env.example .env
nano .env
docker compose config
docker compose up -d --build
docker compose ps
curl -f http://127.0.0.1:18095/api/health
```

Обязательно замените `POSTGRES_PASSWORD` и `SESSION_SECRET`. Для LDAP укажите
`AUTH_MODE=ldap`, адрес контроллера, Base DN и один способ первоначального доступа
администратора: `LDAP_ADMIN_GROUP_DN` либо `PORTAL_ADMIN_LOGINS`.

## Windows / PowerShell

```powershell
git clone --branch codex/production-plan-v2 https://github.com/ilonlost/art-portal.git
Set-Location art-portal\production-planning
Copy-Item .env.example .env
notepad .env
docker compose config
docker compose up -d --build
docker compose ps
Invoke-WebRequest http://127.0.0.1:18095/api/health
```

Портал будет доступен на `http://SERVER:18095/`. Чтобы использовать другой порт,
измените `APP_PORT` в `.env`. В рабочем контуре рекомендуется поставить перед
порталом HTTPS reverse proxy и задать `APP_URL=https://plan.company.ru`,
`SESSION_COOKIE_SECURE=true`.

## Обновление

```bash
git pull origin codex/production-plan-v2
docker compose up -d --build
docker compose ps
```

Миграции базы применяются автоматически при старте backend.

## Диагностика и резервная копия

```bash
docker compose logs --tail=200 ppp-backend
docker compose logs --tail=200 ppp-frontend
docker compose exec ppp-postgres pg_dump -U planner production_planning > plan-portal-backup.sql
```

Остановка без удаления данных:

```bash
docker compose down
```

Не выполняйте `docker compose down -v`, если база и загруженные планы должны сохраниться.
