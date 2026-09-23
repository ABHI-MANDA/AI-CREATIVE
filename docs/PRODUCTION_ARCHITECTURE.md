# Production Architecture

## Recommended deployment

| Layer | Recommendation | Why |
|---|---|---|
| Frontend | Vercel + Next.js (when the UI is split from Flask) | Fast global delivery, preview deployments, CDN, easy Git integration |
| Backend/API | Render Web Service + Gunicorn | Native Flask deployment, background workers, environment secrets, health checks |
| Database | Supabase PostgreSQL | Managed Postgres plus Auth, Storage and Realtime ecosystem |
| Asset storage | Supabase Storage initially; move large media to S3/R2 if volume demands it | Durable object storage instead of ephemeral app disk |
| Realtime | Flask-SocketIO initially; managed realtime later if needed | Preserves current project progress UX |

The current repository remains a Flask full-stack app so it can be deployed as one
service immediately. The code now supports PostgreSQL/Supabase persistence through
`DATABASE_URL` and durable Supabase Storage through the `SUPABASE_*` variables.

## Data ownership

- PostgreSQL: users, brands, campaigns, project state, prompts, reviews and creative memory.
- Object storage: images, videos, reference media and exported campaign files.
- JSON: local fallback only, development fixtures, small configuration/agent payloads.

## Production rule

Do not depend on the container filesystem for durable data. Render/container disks
can be replaced during deploys/restarts; production assets belong in object storage
and persistent application state belongs in PostgreSQL.

## Migration

1. Create the Supabase project and storage bucket.
2. Set `DATABASE_URL` locally.
3. Run `python scripts_migrate_json_to_db.py`.
4. Configure the same `DATABASE_URL` and Supabase storage secrets on the backend host.
5. Verify `/api/health` reports `persistence.backend=postgresql` and a healthy DB.

## Next production milestones

1. Add authentication and tenant IDs to every persistent row.
2. Replace the current JSON-shaped project record with relational `campaigns`,
   `assets`, `generations`, `reviews`, and `brand_profiles` tables.
3. Add a queue (Redis/Celery/RQ or a managed job service) for long video jobs.
4. Add observability: Sentry + structured logs + cost/latency metrics.
5. Split the current HTML UI into Next.js only after the backend API contract is stable.
