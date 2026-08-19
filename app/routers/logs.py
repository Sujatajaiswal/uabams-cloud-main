from fastapi import APIRouter, Depends, HTTPException, Request, Header
from typing import Annotated, Any

from app.database import db
from app.utils import (
    operator_username,
    is_operator_authenticated,
    client_ip,
    utc_now,
    serialize
)
from app.models import ActivityLogRequest

router = APIRouter()

@router.post("/api/v1/logs")
async def create_activity_log(data: ActivityLogRequest, request: Request):
    username = operator_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="Login required")
    
    now = utc_now()
    ip_addr = client_ip(request)
    user_agent = request.headers.get("user-agent", "")
    
    # DB Rewrite
    await db.pg_pool.execute(
        """
        INSERT INTO activity_logs (username, page, action, error_message, ip_address, latitude, longitude, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        username, data.page, data.action, data.errorMessage, ip_addr, data.latitude, data.longitude, now
    )
    
    document = {
        "username": username,
        "page": data.page,
        "action": data.action,
        "message": data.message,
        "errorMessage": data.errorMessage,
        "latitude": data.latitude,
        "longitude": data.longitude,
        "ipAddress": ip_addr,
        "userAgent": user_agent,
        "createdAt": now,
    }
    return {"status": "success", "log": serialize(document)}

@router.get("/api/v1/logs")
async def list_activity_logs(request: Request, username: str | None = None, page: str | None = None, limit: int = 100):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    capped_limit = min(max(limit, 1), 500)
    
    # DB Rewrite
    query = "SELECT id, username, page, action, error_message AS \"errorMessage\", ip_address AS \"ipAddress\", latitude, longitude, created_at AS \"createdAt\" FROM activity_logs"
    conditions = []
    params = []
    param_idx = 1
    
    if username:
        conditions.append(f"username = ${param_idx}")
        params.append(username)
        param_idx += 1
    if page:
        conditions.append(f"page = ${param_idx}")
        params.append(page)
        param_idx += 1
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    query += f" ORDER BY created_at DESC LIMIT ${param_idx}"
    params.append(capped_limit)
    
    rows = await db.pg_pool.fetch(query, *params)
    logs = [dict(r) for r in rows]
    return {"logs": serialize(logs)}
