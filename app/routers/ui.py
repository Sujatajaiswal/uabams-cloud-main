from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from pathlib import Path
from urllib.parse import parse_qs
from passlib.hash import bcrypt

from app.database import db, settings
from app.utils import (
    is_operator_authenticated, 
    is_admin_authenticated, 
    render_login_page,
    OPERATOR_COOKIE_NAME,
    OPERATOR_SESSION_HOURS,
    create_operator_session
)

router = APIRouter()

@router.get("/")
async def root():
    return {"message": "UABAMS Cloud Running", "dashboard": "/dashboard", "login": "/login", "docs": "/docs"}

@router.get("/health")
async def health_check():
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
    if is_operator_authenticated(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render_login_page()

@router.post("/login")
async def login_submit(request: Request):
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body, keep_blank_values=True)
    username = form.get("username", [""])[0]
    password = form.get("password", [""])[0]
    
    user_record = None
    if db.pg_pool:
        async with db.pg_pool.acquire() as conn:
            user_record = await conn.fetchrow(
                "SELECT password_hash, role, can_configure_thresholds, can_manage_users, can_view_alerts "
                "FROM users WHERE username = $1 AND is_active = TRUE", 
                username
            )
            
    if user_record:
        try:
            is_valid = bcrypt.verify(password, user_record['password_hash'])
        except Exception:
            is_valid = False
            
        if is_valid:
            role = user_record['role'].lower()
            perms = {
                "can_configure_thresholds": user_record["can_configure_thresholds"],
                "can_manage_users": user_record["can_manage_users"],
                "can_view_alerts": user_record["can_view_alerts"]
            }
        else:
            return render_login_page("Invalid username or password")
    else:
        return render_login_page("Invalid username or password")

    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        OPERATOR_COOKIE_NAME,
        create_operator_session(username, role, perms),
        max_age=OPERATOR_SESSION_HOURS * 60 * 60,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return response

@router.get("/dashboard")
async def dashboard_page(request: Request):
    if not is_operator_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(Path("app/static/index.html"), headers={"Cache-Control": "no-store"})

@router.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html(request: Request):
    if not is_admin_authenticated(request):
        if is_operator_authenticated(request):
            return RedirectResponse(url="/dashboard", status_code=303)
        return RedirectResponse(url="/login", status_code=303)
    return get_swagger_ui_html(openapi_url="/openapi.json", title="UABAMS Cloud API - Swagger")

@router.get("/openapi.json", include_in_schema=False)
async def get_open_api_endpoint(request: Request):
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin access required for API documentation")
    # request.app.routes is used instead of app.routes
    return JSONResponse(get_openapi(title=request.app.title, version=request.app.version, routes=request.app.routes))

@router.get('/logout')
async def logout(request: Request):
    response = RedirectResponse('/login', status_code=303)
    response.delete_cookie(OPERATOR_COOKIE_NAME)
    return response

