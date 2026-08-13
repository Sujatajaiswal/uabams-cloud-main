import os
from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


PROTECTED_GATEWAY_PATHS = (
    "/api/v1/archive",
    "/api/v1/alert",
)


def normalize_gateway_id(gid: str | None) -> str | None:
    if not gid:
        return None
    g = gid.strip().upper()
    if g in ("GW1", "GW01", "GW_01", "1", "GW_UABAMS_BOGIE_01"):
        return "GW_UABAMS_BOGIE_01"
    if g in ("GW2", "GW02", "GW_02", "2", "GW_UABAMS_BOGIE_02"):
        return "GW_UABAMS_BOGIE_02"
    return gid


def gateway_keys() -> dict[str, str | None]:
    return {
        "GW_UABAMS_BOGIE_01": os.getenv("GATEWAY_API_KEY_GW01"),
        "GW_UABAMS_BOGIE_02": os.getenv("GATEWAY_API_KEY_GW02"),
    }


async def async_gateway_id_for_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    api_key_clean = api_key.strip()
    
    # 1. Check environment variables
    for gateway_id, expected_key in gateway_keys().items():
        if expected_key and expected_key.strip() == api_key_clean:
            return gateway_id
            
    default_key = os.getenv("GATEWAY_API_KEY_DEFAULT")
    if default_key and default_key.strip() == api_key_clean:
        return "GW_UABAMS_BOGIE_01"

    # 2. Check database table (gateway_auth)
    try:
        from app.database import db
        auth_doc = await db.gateway_auth.find_one({"secret_key": api_key_clean})
        if not auth_doc:
            auth_doc = await db.gateway_auth.find_one({"apiKey": api_key_clean})
        if auth_doc:
            raw_id = auth_doc.get("gatewayId") or auth_doc.get("gateway_id")
            return normalize_gateway_id(raw_id)
    except Exception as e:
        print(f"Error querying gateway_auth table for API key: {e}")
        
    return None


class GatewayAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path.startswith(PROTECTED_GATEWAY_PATHS):
            supplied_gateway_id = request.headers.get("X-Gateway-Id")
            train_id = request.headers.get("X-Train-Id")
            api_key = request.headers.get("X-Api-Key")
            session_id = request.headers.get("X-Session-Id")

            # For archive uploads, strictly enforce API key authentication
            if request.url.path.startswith("/api/v1/archive") or not session_id:
                if not api_key:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Missing gateway API key header (X-Api-Key)"},
                    )

                gateway_id = await async_gateway_id_for_key(api_key)
                if not gateway_id:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Invalid gateway API key"},
                    )

                request.state.gateway_id = gateway_id
                request.state.train_id = train_id
                request.state.api_key = api_key
            else:
                from app.database import db
                session = await db.handshake_sessions.find_one({"sessionId": session_id})
                if not session or not session.get("verified"):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Invalid or expired session"},
                    )
                gateway_id = normalize_gateway_id(session["gatewayId"])
                request.state.gateway_id = gateway_id
                request.state.train_id = train_id
                request.state.session_id = session_id
                request.state.session_key = bytes.fromhex(session["sessionKeyHex"])

            if supplied_gateway_id:
                norm_supplied = normalize_gateway_id(supplied_gateway_id)
                norm_resolved = normalize_gateway_id(gateway_id)
                if norm_supplied != norm_resolved:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "API key or session does not belong to supplied gateway"},
                    )

        return await call_next(request)