# Установка PLAN PORTAL в Docker

## Linux-сервер

```bash
git clone https://github.com/ilonlost/plan-portal.git
cd plan-portal
cp .env.example .env
nano .env
docker compose config
docker compose up -d --build
docker compose ps
curl -f http://127.0.0.1:15500/api/health
```

Портал откроется на `http://SERVER_IP:15500/`. В файле `.env` обязательно замените
`POSTGRES_PASSWORD` и `SESSION_SECRET`. Заполните LDAP и SMTP-блоки значениями из
одноимённых блоков `.env` проекта ART Portal: названия общих переменных совпадают.
Не копируйте файл ART Portal целиком: у PLAN Portal должны остаться собственные
`POSTGRES_*`, `APP_URL`, `CORS_ORIGINS`, `SMTP_FROM` и `SMTP_FROM_NAME`.

Если в ART Portal задано `LDAP_CA_FILE=/app/certs/agrohold-ca.pem` или
`SMTP_CA_FILE=/app/certs/agrohold-ca.pem`, скопируйте тот же корпоративный CA
в `certs/agrohold-ca.pem` рядом с `docker-compose.yml`. Каталог `certs` уже
монтируется в контейнер по тому же пути `/app/certs`.

Для LDAP установите `AUTH_MODE=ldap`, адрес контроллера, Base DN и один способ
первоначального доступа администратора: `LDAP_ADMIN_GROUP_DN` либо
`PORTAL_ADMIN_LOGINS`. Планеров, мастеров и пользователей просмотра после первого
входа назначайте на странице «Администрирование → Пользователи и права». История
писем и попыток доставки хранится в PostgreSQL и сохраняется при обычном
`docker compose down`.

Если сервер работает за HTTPS reverse proxy, укажите внешний URL в `APP_URL` и
`CORS_ORIGINS`, а также установите `SESSION_COOKIE_SECURE=true`.

## Windows / PowerShell

```powershell
git clone https://github.com/ilonlost/plan-portal.git
Set-Location plan-portal
Copy-Item .env.example .env
notepad .env
docker compose config
docker compose up -d --build
docker compose ps
Invoke-WebRequest http://127.0.0.1:15500/api/health
```

Портал будет доступен на `http://SERVER:15500/`. Чтобы использовать другой порт,
измените `APP_PORT` в `.env`. В рабочем контуре рекомендуется поставить перед
порталом HTTPS reverse proxy и задать `APP_URL=https://plan.company.ru`,
`SESSION_COOKIE_SECURE=true`.

## Обновление

```bash
git pull origin main
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
