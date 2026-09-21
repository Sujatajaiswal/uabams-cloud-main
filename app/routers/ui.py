from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from pathlib import Path
from urllib.parse import parse_qs
from passlib.hash import bcrypt
from datetime import datetime, timezone, timedelta

from app.database import db, settings
from app.utils import (
    is_operator_authenticated, 
    is_admin_authenticated, 
    render_login_page,
    OPERATOR_COOKIE_NAME,
    OPERATOR_SESSION_HOURS,
    create_operator_session,
    operator_session_payload
)

router = APIRouter()

@router.get("/")
async def root():
    return {"message": "UABAMS Cloud Running", "dashboard": "/dashboard", "login": "/login", "docs": "/docs"}

@router.get("/health")
async def health_check(request: Request):
    # Require authentication — unauthenticated users should not see health info
    if not is_operator_authenticated(request):
        return JSONResponse(
            status_code=401,
            content={
                "status": "unauthorized",
                "message": "Please login using your username and password to access this endpoint."
            }
        )

    # To check global startup_error, we can't easily without importing main or it being in utils.
    # We will assume startup_error is not tracked here or we just check the pool.
    if settings.get("database_type") == "postgres" and db.pg_pool is None:
        return {
            "status": "unhealthy",
            "database_type": "postgres",
            "connection": "failed"
        }
    try:
        # Replaced db_wrapper with asyncpg
        await db.pg_pool.fetchrow("SELECT 1 FROM gateway_auth WHERE gateway_id = $1", "health_check_test_id")
        return {
            "status": "healthy",
            "database_type": settings.get("database_type"),
            "connection": "connected"
        }
    except Exception as exc:
        return {
            "status": "unhealthy",
            "database_type": settings.get("database_type"),
            "connection": "failed",
            "error": str(exc)
        }

@router.get("/login")
async def login_page(request: Request):
    # Only auto-redirect to dashboard if accessing via cookie (no Authorization header).
    # This allows a new tab to log in as a different user even when another tab is authenticated.
    has_auth_header = bool(
        request.headers.get("Authorization") or request.headers.get("X-Session-Token")
    )
    if not has_auth_header and is_operator_authenticated(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render_login_page()

@router.post("/login")
async def login_submit(request: Request):
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body, keep_blank_values=True)
    username = form.get("username", [""])[0]
    password = form.get("password", [""])[0]
    is_ajax = "application/json" in (request.headers.get("Accept") or "")

    user_record = None
    if db.pg_pool:
        async with db.pg_pool.acquire() as conn:
            user_record = await conn.fetchrow(
                "SELECT id, password_hash, role, can_configure_thresholds, can_manage_users, can_view_alerts, failed_login_attempts, locked_until "
                "FROM users WHERE username = $1 AND is_active = TRUE",
                username
            )

    if user_record:
        if user_record['locked_until'] and user_record['locked_until'] > datetime.now(timezone.utc):
            msg = "Account is temporarily locked due to multiple failed login attempts. Try again later."
            if is_ajax:
                return JSONResponse({"status": "error", "message": msg}, status_code=401)
            return render_login_page(msg)

        try:
            is_valid = bcrypt.verify(password, user_record['password_hash'])
        except Exception:
            is_valid = False

        if is_valid:
            if db.pg_pool:
                await db.pg_pool.execute("UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = $1", user_record['id'])
            
            role = user_record['role'].lower()
            perms = {
                "can_configure_thresholds": user_record.get("can_configure_thresholds", False),
                "can_manage_users": user_record.get("can_manage_users", False),
                "can_view_alerts": user_record.get("can_view_alerts", True),
                "can_view_archives": user_record.get("can_view_archives", False),
                "can_reset_session": user_record.get("can_reset_session", False),
                "can_view_logs": user_record.get("can_view_logs", False),
                "can_view_reports": user_record.get("can_view_reports", False)
            }
        else:
            if db.pg_pool:
                new_attempts = user_record['failed_login_attempts'] + 1
                locked_until = None
                if new_attempts >= 5:
                    locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
                
                await db.pg_pool.execute(
                    "UPDATE users SET failed_login_attempts = $1, locked_until = $2 WHERE id = $3",
                    new_attempts, locked_until, user_record['id']
                )

            if is_ajax:
                return JSONResponse({"status": "error", "message": "Invalid username or password"}, status_code=401)
            return render_login_page("Invalid username or password")
    else:
        if is_ajax:
            return JSONResponse({"status": "error", "message": "Invalid username or password"}, status_code=401)
        return render_login_page("Invalid username or password")

    token = create_operator_session(username, role, perms)

    if is_ajax:
        # Return token as JSON for per-tab sessionStorage storage
        response = JSONResponse({
            "status": "success",
            "token": token,
            "role": role,
            "username": username,
            "redirect": f"/dashboard?session_token={token}"
        })
        response.set_cookie(
            OPERATOR_COOKIE_NAME,
            token,
            max_age=OPERATOR_SESSION_HOURS * 60 * 60,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
        )
        return response

    # Standard form submission: set cookie + redirect (single-tab / no-JS fallback)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        OPERATOR_COOKIE_NAME,
        token,
        max_age=OPERATOR_SESSION_HOURS * 60 * 60,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return response

async def _serve_dashboard(request: Request, token: str | None = None) -> FileResponse:
    """Serve the main SPA index.html with optional session token cookie."""
    response = FileResponse(Path("app/static/index.html"), headers={"Cache-Control": "no-store"})
    if token:
        response.set_cookie(
            OPERATOR_COOKIE_NAME,
            token,
            max_age=OPERATOR_SESSION_HOURS * 60 * 60,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
        )
    return response


# ── Tab routes — each serves the same SPA so the JS can read the path ─────────
_TAB_ROUTES = [
    "/dashboard",
    "/calibration",
    "/routes_config",
    "/alerts",
    "/archives",
    "/reset",
    "/logs",
    "/repeated_alarm",
    "/alarm_log_reports",
    "/alert_graph",
    "/users",
]

for _path in _TAB_ROUTES:
    @router.get(_path, name=f"tab_{_path.lstrip('/')}", include_in_schema=False)
    async def _tab_page(request: Request, _p: str = _path):
        payload = operator_session_payload(request)
        if not payload:
            return RedirectResponse("/login", status_code=303)
            
        role = payload.get("role", "operator").lower()
        tab_id = _p.lstrip("/")
        
        if role != "admin":
            admin_only_tabs = ["calibration", "archives", "reset", "logs", "alarm_log_reports"]
            if tab_id in admin_only_tabs:
                return RedirectResponse("/dashboard", status_code=303)
                
            if tab_id == "users" and not payload.get("can_manage_users"):
                return RedirectResponse("/dashboard", status_code=303)
                
            if tab_id == "alerts" and not payload.get("can_view_alerts"):
                return RedirectResponse("/dashboard", status_code=303)

        token = request.query_params.get("session_token")
        return await _serve_dashboard(request, token)


@router.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html(request: Request):
    if not is_admin_authenticated(request):
        if is_operator_authenticated(request):
            return RedirectResponse(url="/dashboard", status_code=303)
        return RedirectResponse(url="/login", status_code=303)
    token = request.query_params.get("session_token")
    openapi_url = f"/openapi.json?session_token={token}" if token else "/openapi.json"
    return get_swagger_ui_html(openapi_url=openapi_url, title="UABAMS Cloud API - Swagger")

@router.get("/openapi.json", include_in_schema=False)
async def get_open_api_endpoint(request: Request):
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin access required for API documentation")
    return JSONResponse(get_openapi(title=request.app.title, version=request.app.version, routes=request.app.routes))

@router.get('/logout')
async def logout(request: Request):
    response = RedirectResponse('/login', status_code=303)
    response.delete_cookie(OPERATOR_COOKIE_NAME)
    return response

