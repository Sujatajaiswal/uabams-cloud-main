from datetime import UTC, datetime, timedelta
import json
import os
import urllib.parse
import urllib.request
import urllib.error
import jwt
from typing import Any
from fastapi import Request
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from hashlib import sha256

from app.database import db, settings

OPERATOR_COOKIE_NAME = "uabams_operator_session"
OPERATOR_SESSION_HOURS = 12
SPATIAL_RETENTION_DAYS = 30
TIME_DOMAIN_RETENTION_DAYS = 7
TIME_DOMAIN_DIR = os.environ.get("TIME_DOMAIN_DIR", "/app/time_domain")

def utc_now() -> datetime:
    return datetime.now(UTC)

def serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    return value

def client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else ""

def create_operator_session(username: str, role: str = "operator", perms: dict = None) -> str:
    now = utc_now()
    if perms is None:
        perms = {}
    payload = {
        "sub": username,
        "role": role,
        "can_configure_thresholds": perms.get("can_configure_thresholds", False),
        "can_manage_users": perms.get("can_manage_users", False),
        "can_view_alerts": perms.get("can_view_alerts", True),
        "iat": now,
        "exp": now + timedelta(hours=OPERATOR_SESSION_HOURS),
    }
    return jwt.encode(payload, settings["jwt_secret"], algorithm=settings["jwt_algorithm"])

def operator_session_payload(request: Request) -> dict[str, Any] | None:
    token = None
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    elif request.headers.get("X-Session-Token"):
        token = request.headers.get("X-Session-Token")
    else:
        try:
            token = request.query_params.get("session_token")
        except (KeyError, AttributeError):
            token = None
        if not token:
            token = request.cookies.get(OPERATOR_COOKIE_NAME)

    if not token:
        return None
    try:
        return jwt.decode(token, settings["jwt_secret"], algorithms=[settings["jwt_algorithm"]])
    except jwt.PyJWTError:
        return None

def is_operator_authenticated(request: Request) -> bool:
    return operator_session_payload(request) is not None

def send_sms(mobile_number: str, message: str) -> tuple[bool, str]:
    """
    Mock SMS dispatch. 
    Will be replaced with company API later.
    """
    print(f"[SMS DISPATCH] To {mobile_number}: {message}")
    
    # Mock successful dispatch
    # Later replace this block with the actual company API integration
    success = True
    reason = ""
    
    if success:
        print(f"[SMS] Successfully sent to {mobile_number}")
        return True, ""
    else:
        print(f"[SMS] Failed to send to {mobile_number}")
        return False, "API not implemented"

def is_admin_authenticated(request: Request) -> bool:
    payload = operator_session_payload(request)
    return payload is not None and payload.get("role") == "admin"

def operator_username(request: Request) -> str | None:
    payload = operator_session_payload(request)
    return payload.get("sub") if payload else None

def render_login_page(error: str = ""):
    from fastapi.responses import HTMLResponse
    error_html = f'<div class="alert alert-error">{error}</div>' if error else ""
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UABAMS Login</title>
  <link rel="stylesheet" href="/static/styles.css?v=20260701-login-auth">
</head>
<body class="login-body">
  <div class="login-page">
    <div class="login-container">
      <div class="top-logo-container">
        <img src="/static/railman-logo.png" class="railman-logo" alt="RailMan Logo">
      </div>
      <div class="login-form-container">
        {error_html}
        <form method="post" action="/login">
          <div class="input-group">
            <span class="input-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                <circle cx="12" cy="7" r="4"></circle>
              </svg>
            </span>
            <input name="username" type="text" autocomplete="username" placeholder="Username or Email" required autofocus>
          </div>
          <div class="input-group">
            <span class="input-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
              </svg>
            </span>
            <input id="password" name="password" type="password" autocomplete="current-password" placeholder="••••••••" required>
            <button type="button" class="password-toggle" id="toggle-password">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" id="eye-icon">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                <circle cx="12" cy="12" r="3"></circle>
              </svg>
            </button>
          </div>
          <button class="login-btn" type="submit">Login</button>
        </form>
      </div>
    </div>
    <div class="footer-branding">
      <img src="/static/apna-logo.png" class="apna-logo" alt="Apna Logo">
      <div class="footer-links">&copy; 2026 Privacy Policy | Copyright Policy</div>
    </div>
  </div>
  <script>
    try {
      localStorage.removeItem('activeTab');
      sessionStorage.removeItem('activeTab');
    } catch (e) {}
    const togglePassword = document.querySelector('#toggle-password');
    const password = document.querySelector('#password');
    togglePassword.addEventListener('click', function () {
      const type = password.getAttribute('type') === 'password' ? 'text' : 'password';
      password.setAttribute('type', type);
    });

    const loginForm = document.querySelector('form');
    if (loginForm) {
      loginForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        const btn = loginForm.querySelector('.login-btn');
        if (btn) btn.disabled = true;
        try {
          const formData = new URLSearchParams(new FormData(loginForm));
          const res = await fetch('/login', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
              'Accept': 'application/json',
              'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: formData
          });
          const data = await res.json();
          if (data && data.status === 'success' && data.token) {
            sessionStorage.setItem('uabams_session_token', data.token);
            sessionStorage.setItem('uabams_user_role', data.role || '');
            sessionStorage.setItem('uabams_username', data.username || '');
            sessionStorage.setItem('activeTab', 'overview');
            window.location.href = data.redirect || ('/dashboard?session_token=' + encodeURIComponent(data.token));
          } else {
            let errBox = document.querySelector('.alert-error');
            if (!errBox) {
              errBox = document.createElement('div');
              errBox.className = 'alert alert-error';
              loginForm.parentNode.insertBefore(errBox, loginForm);
            }
            errBox.textContent = (data && (data.message || data.detail)) || 'Invalid username or password';
            if (btn) btn.disabled = false;
          }
        } catch (err) {
          loginForm.submit();
        }
      });
    }
  </script>
</body>
</html>""".replace("{error_html}", error_html)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})

def create_gateway_token(gateway_id: str) -> str:
    payload = {
        "sub": gateway_id,
        "iat": utc_now(),
        "exp": utc_now() + timedelta(hours=12),
    }
    return jwt.encode(payload, settings["jwt_secret"], algorithm=settings["jwt_algorithm"])

from fastapi import HTTPException

def verify_gateway_token(token: str, gateway_id: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings["jwt_secret"], algorithms=[settings["jwt_algorithm"]])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    if payload.get("sub") != gateway_id:
        raise HTTPException(status_code=401, detail="Token does not belong to this gateway")
    return payload

def absolute_cloud_url(request: Request, path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    base_url = settings.get("cloud_public_base_url") or str(request.base_url).rstrip("/")
    return f"{base_url}/{path.lstrip('/')}"

async def resolve_train_id(gateway_id: str, *candidates: str | None) -> str:
    for candidate in candidates:
        if candidate:
            return str(candidate).strip()
    
    # Needs asyncpg
    row = await db.pg_pool.fetchrow("SELECT train_id FROM gateways WHERE gateway_id = $1", gateway_id)
    if row and row['train_id']:
        return str(row['train_id'])
    row = await db.pg_pool.fetchrow("SELECT train_id FROM gateway_status WHERE gateway_id = $1", gateway_id)
    if row and row['train_id']:
        return str(row['train_id'])
    raise HTTPException(status_code=400, detail="Could not resolve train ID for the given gateway")

def location_box(latitude: float, longitude: float, radius_meters: float) -> dict[str, dict[str, float]]:
    radius_degrees = max(radius_meters, 1.0) / 111_320
    return {
        "latitude": {"$gte": latitude - radius_degrees, "$lte": latitude + radius_degrees},
        "longitude": {"$gte": longitude - radius_degrees, "$lte": longitude + radius_degrees},
    }

def _positive_factor(value: Any, default: float = 1.0) -> float:
    try:
        from math import isfinite
        factor = float(value)
        return factor if isfinite(factor) and factor > 0 else default
    except (TypeError, ValueError):
        return default

def apply_wheel_compensation(
    rms_records: list[dict[str, Any]],
    peak_records: list[dict[str, Any]],
    calibration: dict[str, Any] | None,
) -> dict[str, Any]:
    calibration = calibration or {}
    left_factor = _positive_factor(calibration.get("leftWheelFactor"))
    right_factor = _positive_factor(calibration.get("rightWheelFactor"))
    combined_factor = (left_factor + right_factor) / 2.0

    def compensate_position(record: dict[str, Any], field: str) -> None:
        value = record.get(field)
        if value is None:
            return
        raw_field = f"raw{field[0].upper()}{field[1:]}"
        record[raw_field] = value
        record[field] = int(round(float(value) * combined_factor))

    def compensate_speed(record: dict[str, Any]) -> None:
        value = record.get("speedKmph")
        if value is None:
            return
        record["rawSpeedKmph"] = value
        record["speedKmph"] = round(float(value) * combined_factor, 2)

    for record in rms_records:
        compensate_position(record, "positionMm")
        compensate_speed(record)
        record["wheelCompensationFactor"] = round(combined_factor, 6)

    for record in peak_records:
        compensate_position(record, "windowStartMm")
        compensate_position(record, "windowEndMm")
        compensate_position(record, "positionMm")
        compensate_speed(record)
        if record.get("windowStartMm") is not None:
            record["startKm"] = round(record["windowStartMm"] / 1000000.0, 5)
        if record.get("windowEndMm") is not None:
            record["endKm"] = round(record["windowEndMm"] / 1000000.0, 5)
        if record.get("positionMm") is not None:
            record["locationKm"] = round(record["positionMm"] / 1000000.0, 5)
        for spd_field in ("avgSpeedKmph", "minSpeedKmph", "maxSpeedKmph"):
            if record.get(spd_field) is not None:
                record[f"raw{spd_field[0].upper()}{spd_field[1:]}"] = record[spd_field]
                record[spd_field] = round(float(record[spd_field]) * combined_factor, 2)
        for axis in record.get("axes", {}).values():
            compensate_position(axis, "peakPositionMm")
            if axis.get("peakPositionMm") is not None:
                axis["positionMm"] = axis["peakPositionMm"]
                axis["locationKm"] = round(axis["positionMm"] / 1000000.0, 5)
            if axis.get("peakSpeedKmph") is not None:
                axis["rawPeakSpeedKmph"] = axis["peakSpeedKmph"]
                axis["peakSpeedKmph"] = round(float(axis["peakSpeedKmph"]) * combined_factor, 2)
                axis["speedKmph"] = axis["peakSpeedKmph"]
        record["wheelCompensationFactor"] = round(combined_factor, 6)

    return {
        "leftWheelFactor": left_factor,
        "rightWheelFactor": right_factor,
        "combinedFactor": round(combined_factor, 6),
        "calibrationVersion": calibration.get("version"),
        "applied": abs(combined_factor - 1.0) > 1e-9,
    }


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

async def require_gateway_access(
    request: Request,
    gateway_id: str,
    *,
    allow_operator: bool = False,
) -> None:
    from app.middleware.auth import async_gateway_id_for_key, normalize_gateway_id
    if allow_operator and is_operator_authenticated(request):
        return

    authenticated_gateway = getattr(request.state, "gateway_id", None)
    if not authenticated_gateway:
        api_key = request.headers.get("X-Api-Key")
        authenticated_gateway = await async_gateway_id_for_key(api_key)
    if not authenticated_gateway:
        raise HTTPException(status_code=401, detail="Gateway API key is required")
    if normalize_gateway_id(authenticated_gateway) != normalize_gateway_id(gateway_id):
        raise HTTPException(status_code=403, detail="API key does not belong to requested gateway")
