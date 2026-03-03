# Architecture Roadmap

## Current
- `timesheet-service`: owns timesheet APIs and automation tasks.
- `chat endpoint`: uses Gemini to map user text into domain actions and handles follow-up questions for missing fields.
- `redis`: queue + task result backend.

## Near-term expansion
- `project-service`: project/task metadata synchronization.
- `employee-service`: employee/user mapping and validation.
- `invoice-service`: convert validated timesheets to billable invoices.

## Integration pattern
- Sync command path: REST endpoint per service.
- Long-running path: enqueue Celery task from API, process in worker.
- Shared concerns: auth, retries, logging, metrics.

## Contract guidance
- Keep each service focused on one Odoo domain model set.
- Prefer async jobs for bulk operations.
- Use API gateway only after multiple public APIs need a single entry point.
