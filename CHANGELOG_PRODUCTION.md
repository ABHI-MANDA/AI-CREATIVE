# Production Upgrade Changelog

## 2026-09-23

### Architecture
- Added PostgreSQL persistence adapter with JSON fallback.
- Added Supabase Storage adapter for durable generated/reference media.
- Added production Dockerfile with FFmpeg and OpenCV runtime dependencies.
- Added Render production Blueprint and deployment documentation.

### Creative intelligence
- Added structured Creative DNA extraction from campaign briefs and scraped references.
- Added optional vision-based creative-direction enrichment.
- Creative DNA is now included in the prompt synthesis context.

### Post-production quality
- Added deterministic output evaluation for image, video and copy deliverables.
- Evaluation metadata is attached to generated outputs before persistence.

### Data safety
- Removed local `.env`, `.git`, virtual environment and generated media from the deliverable.
- Production secrets are represented only by example environment files.

### Migration
- Added `scripts_migrate_json_to_db.py` for one-time JSON-to-PostgreSQL migration.
- Added `supabase_schema.sql` for the next relational SaaS migration.
