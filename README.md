# EmailTrackingServer V2

EmailTrackingServer V2 is a standalone FastAPI service for email tracking.
It is configured as an independent Version 2 application with its own
PostgreSQL database and its own Render service.

Version 2 must use a dedicated PostgreSQL database named:

```text
email_tracking_v2
```

Do not point this project at a Version 1 database, Render service, or
configuration.

The application dual-writes open events to `data/EmailTracking.xlsx` and
PostgreSQL.
Excel support remains active and tracking continues when PostgreSQL is
temporarily unavailable.

The project does not send email or implement a dashboard, authentication,
reports, scheduler, or desktop integration.

## Requirements

- Python 3.12
- FastAPI and Uvicorn
- openpyxl and Pillow
- SQLAlchemy 2 ORM
- psycopg 3 PostgreSQL driver
- PostgreSQL

All Python dependencies are pinned in `requirements.txt`. Render selects Python
3.12 from the root `.python-version` file.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `APP_NAME` | Recommended | Deployment identity for logs and Swagger; use `EmailTrackingServer-V2` |
| `APP_ENV` | Recommended | Deployment environment label, for example `production` |
| `PUBLIC_BASE_URL` | After deployment | Actual V2 public URL assigned/configured in Render; no URL is hardcoded in source |
| `DATABASE_URL` | Yes on Render | Version 2 PostgreSQL connection string; must point to `email_tracking_v2` |
| `EXPECTED_DATABASE_NAME` | Recommended | Database-name safety check; defaults to `email_tracking_v2` |
| `PORT` | Yes on Render | Port exposed by the web service |
| `LOG_LEVEL` | No | Logging level; defaults to `INFO` |
| `DATA_FOLDER` | No | Excel folder; defaults locally to `data/` |

Never commit `DATABASE_URL` or place it directly in source code. The application
accepts standard `postgresql://` URLs and selects the psycopg 3 driver
automatically. At startup, Version 2 validates PostgreSQL URLs and refuses to
connect unless the database name is `email_tracking_v2`.

For local development, copy `.env.example` to `.env` and fill in the
Version 2-only database URL:

```powershell
Copy-Item .env.example .env
```

The `.env` file is ignored by Git and is loaded automatically for local runs.
Render environment variables remain authoritative in production.

## Local setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PORT = "8000"
$env:LOG_LEVEL = "INFO"
$env:DATA_FOLDER = "data"
$env:APP_NAME = "EmailTrackingServer-V2"
$env:APP_ENV = "development"
$env:PUBLIC_BASE_URL = ""
$env:EXPECTED_DATABASE_NAME = "email_tracking_v2"
$env:DATABASE_URL = "<VERSION_2_POSTGRESQL_DATABASE_URL_FOR_email_tracking_v2>"
uvicorn main:app --host 0.0.0.0 --port $env:PORT
```

Swagger UI is available at `http://localhost:8000/docs`.

## Automatic database setup

Create a brand-new PostgreSQL database named `email_tracking_v2` before
deploying V2. Set only that database's connection string as `DATABASE_URL`.

At application startup, SQLAlchemy calls `Base.metadata.create_all()` and
`AttachmentBase.metadata.create_all()` using `DATABASE_URL`. This creates the
required V2 tables if they do not exist; no manual SQL or migration command is
required for this phase. Existing tables and rows inside the V2 database are
left intact.

The table contains:

- `id` as its primary key and unique `tracking_id`
- `recipient_email` and `sender_email`
- `open_count` and `click_count`
- `first_open`, `last_open`, `first_click`, and `last_click`
- `created_at` and `updated_at`
- `last_ip` and `user_agent`

The attachment schema also creates these V2 tables when needed:

- `attachments`
- `tracking_attachments`

The `attachments` table includes the nullable `file_data` column used for
PostgreSQL BYTEA attachment storage. Existing metadata columns remain present
for backward compatibility and future V2 features.

For each successful Excel open update, PostgreSQL receives an atomic upsert with
the resulting Excel `OpenCount`. An existing database row retains `first_open`
while `open_count`, `last_open`, `last_ip`, `user_agent`, and `updated_at` are
updated. A database error is logged and never changes the tracking-pixel HTTP
response or rolls back the Excel write.

## Excel storage

The application creates the configured data folder and `EmailTracking.xlsx`
automatically. Daily logs are written to `logs/YYYY-MM-DD.log`. The existing
Excel schema and tracking behavior remain unchanged.

## Click tracking

Tracked links use this endpoint:

```text
GET /email/click/{tracking_id}?url={encoded_original_url}
```

Example:

```text
/email/click/7d19af31-2d65-49db-b52e-2c92b5d39b61?url=https%3A%2F%2Fpowersoft.com
```

The complete flow is:

1. Validate that `tracking_id` contains only URL-safe letters, numbers,
   underscores, or hyphens.
2. Validate that `url` is a complete HTTP or HTTPS URL.
3. Find and lock the existing PostgreSQL row for the tracking ID.
4. Return HTTP 404 without redirecting when the row does not exist.
5. Increment `click_count`, set `first_click` only when it is currently null,
   and update `last_click`, `last_ip`, `user_agent`, and `updated_at` in UTC.
6. Commit the transaction.
7. Return an immediate HTTP 302 redirect to the exact original URL.

Missing or invalid input returns HTTP 400. A redirect is never issued unless
the database transaction succeeds.

## Desktop tracking synchronization

The desktop Email Automation application can retrieve tracking state with:

```text
GET /api/tracking/sync
GET /api/tracking/sync?updated_after=2026-07-01T09:30:00Z
```

Without `updated_after`, the endpoint returns every PostgreSQL tracking record.
With a valid ISO-8601 cursor, it returns only rows where `updated_at` is strictly
later than the cursor. Results are always sorted by `updated_at` ascending.

Each response object contains only:

- `tracking_id`
- `open_count` and `click_count`
- `first_open` and `last_open`
- `first_click` and `last_click`
- `updated_at`

This allows the desktop application to match rows in `mail_list.xlsx` by
`TrackingId`. After a successful synchronization, the application should store
the newest returned `updated_at` value and pass it as the next request's
`updated_after` cursor. Invalid timestamps return HTTP 400.

## Attachment Library

The server-side Attachment Library stores files under the project-root
`attachments/` directory and metadata in the PostgreSQL `attachments` table.
The folder and table are created automatically during application startup.

Upload a file with:

```text
POST /api/attachments/upload
Content-Type: multipart/form-data
Form field: file
```

Files are streamed in chunks and limited to 50 MB. Every stored filename is
unique and exclusively created, so an existing file is never overwritten. The
client filename is retained separately as `original_file_name`. Uploading
another active file with the same original filename returns HTTP 409 with a
friendly validation error.

List active attachments, newest first:

```text
GET /api/attachments/list
```

Soft-delete an attachment:

```text
DELETE /api/attachments/{attachment_id}
```

Deletion only sets `is_active` to false. The physical file is retained and the
record is hidden from the list endpoint. This phase does not require
authentication.

## Attachment download tracking

Download an active attachment through:

```text
GET /download/{tracking_id}/{attachment_id}
```

The server verifies that the `tracking_attachments` mapping already exists and
that the attachment exists and is active. It does not require an
`email_tracking` row, because those rows are intentionally created only after
an open or click. Invalid values return HTTP 404. For a valid request,
PostgreSQL increments `download_count`, preserves the original
`first_download`, and updates `last_download` and `updated_at` in UTC.

After the transaction commits, the server returns the stored file using its
original filename and content type. Attachment download data is intentionally
not included in the synchronization API yet.

## Development endpoints

Temporary debug routes appear in Swagger under **Development / Debug Only**:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/tracking` | List Excel tracking records |
| `GET /api/download-excel` | Download the current workbook |
| `GET /api/debug` | Show application and Excel diagnostics |
| `GET /api/database/status` | Show database connection, table, and row count |

Example database status response:

```json
{
  "database_connected": true,
  "table_exists": true,
  "total_records": 12
}
```

## Deploy to Render

The repository includes `render.yaml` for a separate Render Web Service named:

```text
EmailTrackingServer-V2
```

It uses these commands:

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
```

1. Push the Version 2 project to a new GitHub repository.
2. In Render, create or sync the Blueprint from `render.yaml`.
3. Create or select the dedicated Version 2 PostgreSQL database named
   `email_tracking_v2`.
4. In the V2 Render service Environment page, set `DATABASE_URL` as a secret
   environment variable using only the `email_tracking_v2` connection string.
   Do not reuse any Version 1 database URL.
5. Confirm `EXPECTED_DATABASE_NAME=email_tracking_v2`.
6. Confirm `APP_NAME=EmailTrackingServer-V2` and `APP_ENV=production`.
7. After Render provides the V2 service URL, set `PUBLIC_BASE_URL` to that
   actual V2 URL. Do not hardcode it in source code.
8. Deploy and wait for the `/health` check to pass.
9. Open `/api/database/status` and confirm all three values indicate success.

Render preserves the separately configured `DATABASE_URL` because the Blueprint
marks it as a non-synced secret and does not hardcode it.

## Verification URLs

Use the deployed `PUBLIC_BASE_URL` value:

```text
{PUBLIC_BASE_URL}/health
{PUBLIC_BASE_URL}/email/open/test123
{PUBLIC_BASE_URL}/email/click/test123?url=https%3A%2F%2Fpowersoft.com
{PUBLIC_BASE_URL}/api/database/status
{PUBLIC_BASE_URL}/api/tracking/sync
{PUBLIC_BASE_URL}/docs
```

The `/health` response remains:

```json
{
  "status": "ok"
}
```

## Tests

Install development dependencies and run the click-tracking test suite:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

The endpoint tests use in-memory fake database services and never connect to
PostgreSQL or modify the Excel workbook.
"# emailtrackingserver-v2" 
