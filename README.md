# Odoo Automation Platform (Timesheet First)

This repository is a microservice-ready starter focused on **Odoo timesheet automation**.

Current service:
- `timesheet-service`: FastAPI API + Celery worker that writes/updates Odoo timesheets (`account.analytic.line`) via Odoo JSON-RPC (`/jsonrpc`).

Designed for scaling:
- Keep each Odoo domain as its own service (`invoice-service`, `purchase-service`, etc.).
- Share infra patterns (Redis broker, container build, env management).
- Expose domain APIs and async jobs independently.
- See [`docs/architecture.md`](docs/architecture.md) for the service roadmap.

## 1. Quick Start

### Prerequisites
- Docker + Docker Compose
- An Odoo instance with timesheets enabled
- Valid Odoo credentials

### Configure environment
1. Copy env template:

```powershell
Copy-Item services/timesheet-service/.env.example services/timesheet-service/.env
```

2. Edit `services/timesheet-service/.env` and set:
- `ODOO_URL`
- `ODOO_DB`
- `ODOO_USERNAME`
- `ODOO_PASSWORD`
- `ODOO_EMPLOYEE_MODEL` (optional, default `hr.employee`)
- `ODOO_EMPLOYEE_USER_FIELD` (optional, default `user_id`)
- `TRANSCRIPTION_URL` (optional, defaults to `https://aqs-shispare-transcript-api.hf.space/voice`)
- `LLM_PROVIDER` (`groq` or `gemini`, default is `groq`)
- `GROQ_API_KEY` (required when `LLM_PROVIDER=groq`)
- `GROQ_USER_AGENT` (optional override if Groq returns Cloudflare 1010)
- `GEMINI_API_KEY` (required when `LLM_PROVIDER=gemini`)

### Run

```powershell
docker compose up --build
```

API base URL: `http://localhost:8000`

## 2. API Endpoints

### Health
- `GET /health`

### Timesheets
- `POST /api/v1/timesheets`
- `PUT /api/v1/timesheets/{entry_id}`
- `GET /api/v1/timesheets?date_from=2026-03-01&date_to=2026-03-31` (`employee_id` optional)

### Voice
- `POST /voice` (multipart `file` forwarded to the configured transcription endpoint)

### Automation
- `POST /api/v1/automation/fill-week` (async, Celery)
- `POST /api/v1/automation/fill-week/sync` (sync, immediate)
- `GET /api/v1/automation/jobs/{task_id}`

### Chat (LLM: Groq/Gemini)
- `POST /api/v1/chat/query`

## 3. Example Requests

### Create one timesheet entry

```bash
curl -X POST http://localhost:8000/api/v1/timesheets \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Bug fixes",
    "date": "2026-03-02",
    "hours": 4,
    "project_id": 12,
    "task_id": 44,
    "user_id": 5
  }'
```

### Auto-fill a week (async)

```bash
curl -X POST http://localhost:8000/api/v1/automation/fill-week \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": 12,
    "task_id": 44,
    "week_start": "2026-03-02",
    "daily_hours": 8,
    "description_template": "Timesheet auto-entry for {date}",
    "weekdays": [0,1,2,3,4],
    "overwrite_existing": false,
    "user_id": 5
  }'
```

### Chat-driven timesheet creation (multi-turn)

First request:

```bash
curl -X POST http://localhost:8000/api/v1/chat/query \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Create timesheet for yesterday for 4 hours"
  }'
```

The API will return a `session_id` and ask for missing fields (typically `project_id`, `task_id`, etc.).
`employee_id` is auto-resolved from your authenticated Odoo user when possible.
Use that exact `session_id` for every follow-up message in the same conversation.
You can also provide names (for example project/task/employee names), and the service will try to resolve them to IDs.
If names are misspelled, it applies fuzzy matching against your Odoo projects/tasks and uses the highest-confidence match.

Follow-up using same session:

```bash
curl -X POST http://localhost:8000/api/v1/chat/query \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "paste-session-id-here",
    "message": "employee is 3, project 12, task 44, description bug fixing"
  }'
```

## 4. Service Layout

```text
services/
  timesheet-service/
    app/
      api/
      clients/
      core/
      models/
      services/
      workers/
```

## 5. Scaling Plan (Next Services)

When you expand beyond timesheets:
1. Create a new service folder under `services/`.
2. Reuse this structure (`api`, `clients`, `services`, `workers`).
3. Give each service its own Odoo model integration and queue tasks.
4. Add an API gateway only when you have multiple external consumers.
