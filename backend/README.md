# Backend

The Flask application is exposed from the repository-root `app.py` for
backwards compatibility with existing local commands and tests. Domain
services are modularized in `core/` and are consumed by the API layer.

New server-side modules belong here or in `core/`, grouped by responsibility
(API routes, provider settings, pipeline services, and persistence).
