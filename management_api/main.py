"""Entry point: `uvicorn management_api.main:app --reload`

Serves the JSON API under /api/* and the static test dashboard
(management_ui/) at /.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from management_api.config import PROJECT_ROOT
from management_api.routers import fabric, health, keys, services, tests

app = FastAPI(
    title="PQC Protocol Tool - Management API",
    description="Wraps the quantum key generation scripts and docker-compose "
                "service topology so they're reachable over HTTP instead of "
                "ad-hoc shell commands.",
    version="0.1.0",
)

# The dashboard is served from the same origin by default (see StaticFiles
# mount below), but CORS is opened up for local development where the UI
# might be served separately (e.g. `python -m http.server` in management_ui/).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(services.router, prefix="/api")
app.include_router(keys.router, prefix="/api")
app.include_router(tests.router, prefix="/api")
app.include_router(fabric.router, prefix="/api")

_ui_dir = PROJECT_ROOT / "management_ui"
if _ui_dir.exists():
    app.mount("/", StaticFiles(directory=str(_ui_dir), html=True), name="ui")
