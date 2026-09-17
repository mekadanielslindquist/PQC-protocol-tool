"""Entry point: `uvicorn management_api.main:app --reload`

Serves the JSON API under /api/* and the static test dashboard
(management_ui/) at /.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from management_api.config import PROJECT_ROOT
from management_api.routers import fabric, health, keys, ops, services, tests

app = FastAPI(
    title="PQC Protocol Tool - Management API",
    description="Wraps the quantum key generation scripts and docker-compose "
                "service topology so they're reachable over HTTP instead of "
                "ad-hoc shell commands.",
    version="0.1.0",
    # Swagger UI's default light theme is served through our own custom
    # /docs route below instead (dark-themed to match management_ui/, and
    # embedded as a tab there via iframe) - turning the built-in one off
    # avoids having two different-looking docs pages live at once.
    docs_url=None,
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
app.include_router(ops.router, prefix="/api")

# Swagger UI's generated page is plain white-on-light by default, which
# looks jarring opened next to management_ui/'s dark dashboard - especially
# for a non-technical user clicking into it expecting "more of the same
# tool", not a visually unrelated page. get_swagger_ui_html() builds the
# normal FastAPI docs page (same CDN JS bundle, same OpenAPI spec) and this
# just splices a dark-theme stylesheet into it afterward, so the JS/behavior
# is identical to stock FastAPI docs - only the CSS is ours.
_SWAGGER_DARK_CSS = """
<style>
  html, body { background: #0b0d11 !important; }
  .swagger-ui { background: #0b0d11; color: #e6e9ef; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .swagger-ui .topbar { display: none; }
  .swagger-ui .info .title, .swagger-ui .info li, .swagger-ui .info p, .swagger-ui .info table,
  .swagger-ui .opblock-tag, .swagger-ui .opblock-tag small,
  .swagger-ui .opblock-description-wrapper p, .swagger-ui .opblock-section-header h4,
  .swagger-ui .parameter__name, .swagger-ui .parameter__type, .swagger-ui .parameter__deprecated,
  .swagger-ui table thead tr td, .swagger-ui table thead tr th,
  .swagger-ui .response-col_status, .swagger-ui .response-col_description,
  .swagger-ui .model-title, .swagger-ui .model, .swagger-ui .model-box, .swagger-ui .prop-type,
  .swagger-ui .opblock .opblock-summary-path, .swagger-ui .opblock .opblock-summary-description,
  .swagger-ui .responses-inner h4, .swagger-ui .responses-inner h5, .swagger-ui .tab li,
  .swagger-ui table.model tbody tr td, .swagger-ui section.models h4, .swagger-ui .opblock-title_normal {
    color: #e6e9ef !important;
  }
  .swagger-ui .info a { color: #5b8cff; }
  .swagger-ui .scheme-container { background: #14171d; box-shadow: none; border-bottom: 1px solid #262b33; }
  .swagger-ui select { background: #0d1014; color: #e6e9ef; border-color: #262b33; }
  .swagger-ui .opblock-tag { border-bottom: 1px solid #262b33; }
  .swagger-ui .opblock-tag:hover { background: rgba(255,255,255,0.02); }
  .swagger-ui .opblock { background: #14171d; border-color: #262b33; box-shadow: none; }
  .swagger-ui .opblock .opblock-summary { border-color: #262b33; }
  .swagger-ui .opblock .opblock-summary-method { color: white; }
  .swagger-ui .opblock.opblock-get { background: rgba(91,140,255,0.06); border-color: #5b8cff; }
  .swagger-ui .opblock.opblock-get .opblock-summary-method { background: #5b8cff; }
  .swagger-ui .opblock.opblock-post { background: rgba(62,207,142,0.06); border-color: #3ecf8e; }
  .swagger-ui .opblock.opblock-post .opblock-summary-method { background: #3ecf8e; }
  .swagger-ui .opblock.opblock-delete { background: rgba(232,102,79,0.06); border-color: #e8664f; }
  .swagger-ui .opblock.opblock-delete .opblock-summary-method { background: #e8664f; }
  .swagger-ui .opblock.opblock-put { background: rgba(217,164,65,0.06); border-color: #d9a441; }
  .swagger-ui .opblock.opblock-put .opblock-summary-method { background: #d9a441; }
  .swagger-ui .opblock-section-header { background: #12151a; box-shadow: none; }
  .swagger-ui input[type=text], .swagger-ui input[type=password], .swagger-ui input[type=search], .swagger-ui textarea {
    background: #0d1014; color: #e6e9ef; border-color: #262b33;
  }
  .swagger-ui .btn { background: #262b33; color: #e6e9ef; border-color: #262b33; }
  .swagger-ui .btn.execute { background: #5b8cff; border-color: #5b8cff; color: white; }
  .swagger-ui .btn.authorize { background: transparent; color: #3ecf8e; border-color: #3ecf8e; }
  .swagger-ui .highlight-code, .swagger-ui .microlight, .swagger-ui pre, .swagger-ui .body-param__example {
    background: #0d1014 !important; color: #e6e9ef;
  }
  .swagger-ui section.models { background: #14171d; border-color: #262b33; }
  .swagger-ui section.models .model-container { background: #12151a; border-color: #262b33; }
  .swagger-ui section.models.is-open h4 { border-bottom: 1px solid #262b33; }
  .swagger-ui svg { fill: #8b93a1; }
  .swagger-ui .opblock-summary-control:focus, .swagger-ui .opblock-summary:focus { outline: none; }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: #0b0d11; }
  ::-webkit-scrollbar-thumb { background: #262b33; border-radius: 6px; }
</style>
"""


@app.get("/docs", include_in_schema=False)
def themed_docs() -> HTMLResponse:
    swagger_response = get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} - Docs",
    )
    html = swagger_response.body.decode("utf-8").replace("</head>", _SWAGGER_DARK_CSS + "</head>")
    return HTMLResponse(html)


_ui_dir = PROJECT_ROOT / "management_ui"
if _ui_dir.exists():
    app.mount("/", StaticFiles(directory=str(_ui_dir), html=True), name="ui")
