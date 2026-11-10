"""All API routes under /api/v1, with authorization applied per router.

- health and auth: public (auth routes check the caller themselves where needed);
- users, settings/auth, audit: admin;
- everything else: viewer for reads (GET/HEAD), editor for changes.

New routers must be added with one of these dependency sets; a test asserts every route that
is not public rejects anonymous requests.
"""

from fastapi import APIRouter, Depends

from app.api.routes import (
    audit,
    auth,
    auth_settings,
    datasets,
    endpoints,
    evaluation,
    health,
    projects,
    reports,
    runs,
    tools,
    users,
)
from app.auth.dependencies import authorize, require_admin

PUBLIC = (health.router, auth.router)
ADMIN = (users.router, auth_settings.router, audit.router)
PROTECTED = (
    projects.router,
    endpoints.router,
    datasets.router,
    runs.router,
    evaluation.router,
    reports.router,
    tools.router,
)

api_router = APIRouter(prefix="/api/v1")
for router in PUBLIC:
    api_router.include_router(router)
for router in ADMIN:
    api_router.include_router(router, dependencies=[Depends(require_admin)])
for router in PROTECTED:
    api_router.include_router(router, dependencies=[Depends(authorize)])
