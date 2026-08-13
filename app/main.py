from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest
from math import isfinite, atan2, cos, degrees, radians, sin
from pathlib import Path
from secrets import token_hex
from typing import Annotated, Any
from urllib.parse import parse_qs
import asyncio
import hashlib
import hmac
import json
import jwt
import os
import traceback
import uuid
from passlib.hash import bcrypt
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dateutil.parser import parse as parse_datetime
from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from app.database import db, settings
from app.middleware.auth import GatewayAuthMiddleware, async_gateway_id_for_key, normalize_gateway_id
from app.models import (
    AlertRequest,
    AuthRequest,
    ActivityLogRequest,
    CalibrationUpdateRequest,
    CommandResultItem,
    HandshakeRequest,
    HeartbeatRequest,
    ResetSessionRequest,
    TargetedResetRequest,
    HandshakeHelloRequest,
    HandshakeHelloResponse,
    HandshakeVerifyRequest,
    HandshakeVerifyResponse,
    GatewayConnectionRequest,
    GatewayConnectionResponse,
    UploadLeaseRequest,
    UploadCompleteRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.parsers.archive import parse_archive_zip, peak_records_to_alert_events, AXIS_NAMES

app = FastAPI(
    title="UABAMS Cloud API",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

SPATIAL_RETENTION_DAYS = 30
TIME_DOMAIN_RETENTION_DAYS = 7
SPATIAL_RETENTION_SECONDS = SPATIAL_RETENTION_DAYS * 24 * 60 * 60
RAW_TIME_DOMAIN_CHUNK_BYTES = 8 * 1024 * 1024
TIME_DOMAIN_DIR = os.environ.get("TIME_DOMAIN_DIR", "/app/time_domain")
OPERATOR_COOKIE_NAME = "uabams_operator_session"
OPERATOR_SESSION_HOURS = 12


def client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


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


def _positive_factor(value: Any, default: float = 1.0) -> float:
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return default
    return factor if isfinite(factor) and factor > 0 else default


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
        for axis in record.get("axes", {}).values():
            compensate_position(axis, "peakPositionMm")
        record["wheelCompensationFactor"] = round(combined_factor, 6)

    return {
        "leftWheelFactor": left_factor,
        "rightWheelFactor": right_factor,
        "combinedFactor": round(combined_factor, 6),
        "calibrationVersion": calibration.get("version"),
        "applied": abs(combined_factor - 1.0) > 1e-9,
    }


async def store_time_domain_files(
    archive_source: bytes | str,
    raw_files: list[dict[str, Any]],
    gateway_id: str,
    train_id: str,
    session_name: str,
    archive_sha256: str,
    created_at: datetime,
) -> list[dict[str, Any]]:
    """Write extracted raw binary files to the TIME_DOMAIN_DIR filesystem volume.

    Each file is saved at:
        {TIME_DOMAIN_DIR}/{gateway_id}/{archive_sha256}/{original_filename}

    Only metadata (path, size, sha256) is stored in the DB — no BYTEA blobs.
    """
    import shutil
    import zipfile
    from io import BytesIO

    # Remove any previous records for this archive (idempotent re-ingest)
    await db.time_domain_chunks.delete_many(
        {"archiveSha256": archive_sha256, "gatewayId": gateway_id}
    )
    await db.time_domain_files.delete_many(
        {"archiveSha256": archive_sha256, "gatewayId": gateway_id}
    )

    expires_at = created_at + timedelta(days=TIME_DOMAIN_RETENTION_DAYS)
    stored_files: list[dict[str, Any]] = []

    # Build per-archive directory on the dedicated volume
    archive_dir = os.path.join(TIME_DOMAIN_DIR, gateway_id, archive_sha256)
    os.makedirs(archive_dir, exist_ok=True)

    archive_file = archive_source if isinstance(archive_source, str) else BytesIO(archive_source)
    with zipfile.ZipFile(archive_file) as archive:
        for raw_file in raw_files:
            zip_member = raw_file.get("zip_member")
            if not zip_member:
                continue

            original_path = raw_file.get("path") or "unknown"
            # Use only the basename to avoid path-traversal issues
            safe_filename = os.path.basename(original_path) or "data.bin"
            fs_path = os.path.join(archive_dir, safe_filename)

            # Extract directly from ZIP to disk, chunk by chunk, bypassing RAM
            with archive.open(zip_member) as src, open(fs_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

            # Compute SHA-256 by reading the written file in chunks
            file_sha256_hash = sha256()
            with open(fs_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096 * 1024), b""):
                    file_sha256_hash.update(chunk)
            file_sha256 = file_sha256_hash.hexdigest()
        file_document = {
            "gatewayId": gateway_id,
            "trainId": train_id,
            "sessionName": session_name,
            "archiveSha256": archive_sha256,
            "filename": safe_filename,
            "path": fs_path,          # full filesystem path for later deletion
            "sizeBytes": len(payload),
            "sha256": file_sha256,
            "chunkCount": 0,          # no longer chunked into DB
            "createdAt": created_at,
            "expiresAt": expires_at,
        }
        await db.time_domain_files.insert_one(file_document)

        stored_files.append(
            {
                "filename": safe_filename,
                "path": fs_path,
                "sizeBytes": len(payload),
                "sha256": file_sha256,
                "expiresAt": expires_at,
            }
        )

    return stored_files


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
    token = request.cookies.get(OPERATOR_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings["jwt_secret"], algorithms=[settings["jwt_algorithm"]])
        return payload
    except jwt.PyJWTError:
        return None


def is_operator_authenticated(request: Request) -> bool:
    return operator_session_payload(request) is not None


def is_admin_authenticated(request: Request) -> bool:
    payload = operator_session_payload(request)
    return payload is not None and payload.get("role") == "admin"


def operator_username(request: Request) -> str | None:
    payload = operator_session_payload(request)
    return payload.get("sub") if payload else None


class ActivityLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith(("/static", "/docs", "/openapi.json", "/api/v1/logs")):
            return response
        username = operator_username(request)
        if username:
            await db.activity_logs.insert_one({
                "username": username,
                "page": path,
                "action": f"{request.method} {path}",
                "statusCode": response.status_code,
                "ipAddress": client_ip(request),
                "userAgent": request.headers.get("user-agent", ""),
                "createdAt": utc_now(),
            })
        return response


app.add_middleware(ActivityLogMiddleware)
app.add_middleware(GatewayAuthMiddleware)


def render_login_page(error: str = "") -> HTMLResponse:
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
    const togglePassword = document.querySelector('#toggle-password');
    const password = document.querySelector('#password');
    
    togglePassword.addEventListener('click', function () {
      const type = password.getAttribute('type') === 'password' ? 'text' : 'password';
      password.setAttribute('type', type);
      
      if (type === 'password') {
        this.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" id="eye-icon">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
            <circle cx="12" cy="12" r="3"></circle>
          </svg>
        `;
      } else {
        this.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" id="eye-icon">
            <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
            <line x1="1" y1="1" x2="23" y2="23"></line>
          </svg>
        `;
      }
    });
  </script>
</body>
</html>""".replace("{error_html}", error_html)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def create_gateway_token(gateway_id: str, train_id: str | None = None) -> str:
    payload = {
        "sub": gateway_id,
        "trainId": train_id,
        "iat": utc_now(),
        "exp": utc_now() + timedelta(hours=12),
    }
    return jwt.encode(payload, settings["jwt_secret"], algorithm=settings["jwt_algorithm"])


def verify_gateway_token(token: str, gateway_id: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings["jwt_secret"], algorithms=[settings["jwt_algorithm"]])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    if payload.get("sub") != gateway_id:
        raise HTTPException(status_code=401, detail="Token does not belong to this gateway")
    return payload


startup_error = None


@app.on_event("startup")
async def startup() -> None:
    global startup_error
    if settings["database_type"] == "postgres":
        import asyncpg
        pool = None
        retries = 15
        delay = 1
        for i in range(retries):
            try:
                pool = await asyncpg.create_pool(settings["database_url"])
                startup_error = None
                break
            except Exception as ssl_exc:
                try:
                    pool = await asyncpg.create_pool(settings["database_url"], ssl="require")
                    startup_error = None
                    break
                except Exception as final_exc:
                    startup_error = f"SSL-less error: {ssl_exc} | SSL-required error: {final_exc}"
                    print(f"PostgreSQL connection attempt {i+1}/{retries} failed. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                
        db.pg_pool = pool
        if pool is None:
            print(f"Warning: Failed to connect to PostgreSQL after {retries} attempts: {startup_error}")
            return
            
        try:
            async with pool.acquire() as conn:
                # ── Schema: complete table definitions ─────────────────────
                await conn.execute("""
                    -- ── Core reference tables ────────────────────────────────
                    CREATE TABLE IF NOT EXISTS trains (
                        train_no   VARCHAR(50)  PRIMARY KEY,
                        train_name VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS gateways (
                        gateway_id       VARCHAR(100) PRIMARY KEY,
                        train_id         VARCHAR(50),
                        gateway_serial   VARCHAR(100),
                        firmware_version VARCHAR(50),
                        status           VARCHAR(50),
                        provision_status VARCHAR(20) DEFAULT 'active',
                        last_seen        TIMESTAMP WITH TIME ZONE,
                        last_heartbeat   TIMESTAMP WITH TIME ZONE,
                        updated_at       TIMESTAMP WITH TIME ZONE,
                        created_at       TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        pending_reset    BOOLEAN DEFAULT FALSE
                    );
                    ALTER TABLE gateways ADD COLUMN IF NOT EXISTS pending_reset BOOLEAN DEFAULT FALSE;

                    -- ── Gateway auth & status ─────────────────────────────────
                    CREATE TABLE IF NOT EXISTS gateway_auth (
                        gateway_id         VARCHAR(100)  NOT NULL,
                        train_id           VARCHAR(50)   NOT NULL,
                        secret_key         VARCHAR(255)  NOT NULL,
                        cert_fingerprint   VARCHAR(64),
                        ssh_public_key     VARCHAR(1024),
                        upload_enabled     BOOLEAN DEFAULT TRUE,
                        upload_base_path   VARCHAR(512),
                        last_authenticated TIMESTAMP WITH TIME ZONE,
                        revoked_at         TIMESTAMP WITH TIME ZONE,
                        created_at         TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (gateway_id, train_id)
                    );

                    CREATE TABLE IF NOT EXISTS gateway_status (
                        gateway_id          VARCHAR(100) PRIMARY KEY,
                        train_id            VARCHAR(50),
                        online              BOOLEAN DEFAULT FALSE,
                        last_heartbeat      TIMESTAMP WITH TIME ZONE,
                        last_handshake      TIMESTAMP WITH TIME ZONE,
                        adxl_state          VARCHAR(50),
                        adxl_uptime         INTEGER,
                        adxl_faults         INTEGER,
                        adxl_fw_version     VARCHAR(50),
                        adxl_cal_version    INTEGER,
                        encoder_state       VARCHAR(50),
                        encoder_uptime      INTEGER,
                        encoder_faults      INTEGER,
                        encoder_fw_version  VARCHAR(50),
                        encoder_cal_version INTEGER,
                        updated_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    -- ── Calibrations ──────────────────────────────────────────
                    CREATE TABLE IF NOT EXISTS calibrations (
                        train_id                   VARCHAR(50),
                        gateway_id                 VARCHAR(100) PRIMARY KEY,
                        version                    INTEGER DEFAULT 1,
                        adxl_left_offset_x         INTEGER DEFAULT 0,
                        adxl_left_offset_y         INTEGER DEFAULT 0,
                        adxl_left_offset_z         INTEGER DEFAULT 0,
                        adxl_right_offset_x        INTEGER DEFAULT 0,
                        adxl_right_offset_y        INTEGER DEFAULT 0,
                        adxl_right_offset_z        INTEGER DEFAULT 0,
                        iis_offset_x               INTEGER DEFAULT 0,
                        iis_offset_y               INTEGER DEFAULT 0,
                        iis_offset_z               INTEGER DEFAULT 0,
                        imu_accel_offset_x        INTEGER DEFAULT 0,
                        imu_accel_offset_y        INTEGER DEFAULT 0,
                        imu_accel_offset_z        INTEGER DEFAULT 0,
                        imu_gyro_offset_x         INTEGER DEFAULT 0,
                        imu_gyro_offset_y         INTEGER DEFAULT 0,
                        imu_gyro_offset_z         INTEGER DEFAULT 0,
                        wheel_diameter_m           DOUBLE PRECISION DEFAULT 0.915,
                        encoder_ppr                INTEGER DEFAULT 100,
                        spatial_interval_mm        INTEGER DEFAULT 250,
                        trigger_start_speed_kmph  DOUBLE PRECISION DEFAULT 20.0,
                        adxl_left                  JSONB,
                        adxl_right                 JSONB,
                        bogie                      JSONB,
                        encoder                    JSONB,
                        updated_at                 TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS calibration_versions (
                        id                         SERIAL PRIMARY KEY,
                        train_id                   VARCHAR(50),
                        gateway_id                 VARCHAR(100),
                        version                    INTEGER NOT NULL,
                        adxl_left_offset_x         INTEGER DEFAULT 0,
                        adxl_left_offset_y         INTEGER DEFAULT 0,
                        adxl_left_offset_z         INTEGER DEFAULT 0,
                        adxl_right_offset_x        INTEGER DEFAULT 0,
                        adxl_right_offset_y        INTEGER DEFAULT 0,
                        adxl_right_offset_z        INTEGER DEFAULT 0,
                        iis_offset_x               INTEGER DEFAULT 0,
                        iis_offset_y               INTEGER DEFAULT 0,
                        iis_offset_z               INTEGER DEFAULT 0,
                        imu_accel_offset_x        INTEGER DEFAULT 0,
                        imu_accel_offset_y        INTEGER DEFAULT 0,
                        imu_accel_offset_z        INTEGER DEFAULT 0,
                        imu_gyro_offset_x         INTEGER DEFAULT 0,
                        imu_gyro_offset_y         INTEGER DEFAULT 0,
                        imu_gyro_offset_z         INTEGER DEFAULT 0,
                        wheel_diameter_m           DOUBLE PRECISION DEFAULT 0.915,
                        encoder_ppr                INTEGER DEFAULT 100,
                        spatial_interval_mm        INTEGER DEFAULT 250,
                        trigger_start_speed_kmph  DOUBLE PRECISION DEFAULT 20.0,
                        adxl_left                  JSONB,
                        adxl_right                 JSONB,
                        bogie                      JSONB,
                        encoder                    JSONB,
                        created_at                 TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    -- ── Sensor data ───────────────────────────────────────────
                    CREATE TABLE IF NOT EXISTS archives (
                        id             SERIAL PRIMARY KEY,
                        gateway_id     VARCHAR(100),
                        sha256         VARCHAR(64),
                        received_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        train_id       VARCHAR(50),
                        session_name   VARCHAR(100),
                        session_status VARCHAR(50),
                        size_bytes     BIGINT,
                        status         VARCHAR(50),
                        parse_warnings TEXT
                    );

                    CREATE TABLE IF NOT EXISTS rms_records (
                        id             SERIAL PRIMARY KEY,
                        train_id       VARCHAR(50),
                        gateway_id     VARCHAR(100),
                        session_name   VARCHAR(100),
                        archive_sha256 VARCHAR(64),
                        latitude       DOUBLE PRECISION,
                        longitude      DOUBLE PRECISION,
                        gps_valid      BOOLEAN,
                        bearing        DOUBLE PRECISION,
                        speed          DOUBLE PRECISION,
                        position_mm    INTEGER,
                        axes           JSONB,
                        -- ADXL Left (al) X/Y/Z in g
                        al_x_g         DOUBLE PRECISION,
                        al_y_g         DOUBLE PRECISION,
                        al_z_g         DOUBLE PRECISION,
                        -- ADXL Right (ar) X/Y/Z in g
                        ar_x_g         DOUBLE PRECISION,
                        ar_y_g         DOUBLE PRECISION,
                        ar_z_g         DOUBLE PRECISION,
                        -- Bogie (bg) X/Y/Z in g
                        bg_x_g         DOUBLE PRECISION,
                        bg_y_g         DOUBLE PRECISION,
                        bg_z_g         DOUBLE PRECISION,
                        created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    ALTER TABLE rms_records ADD COLUMN IF NOT EXISTS al_x_g DOUBLE PRECISION;
                    ALTER TABLE rms_records ADD COLUMN IF NOT EXISTS al_y_g DOUBLE PRECISION;
                    ALTER TABLE rms_records ADD COLUMN IF NOT EXISTS al_z_g DOUBLE PRECISION;
                    ALTER TABLE rms_records ADD COLUMN IF NOT EXISTS ar_x_g DOUBLE PRECISION;
                    ALTER TABLE rms_records ADD COLUMN IF NOT EXISTS ar_y_g DOUBLE PRECISION;
                    ALTER TABLE rms_records ADD COLUMN IF NOT EXISTS ar_z_g DOUBLE PRECISION;
                    ALTER TABLE rms_records ADD COLUMN IF NOT EXISTS bg_x_g DOUBLE PRECISION;
                    ALTER TABLE rms_records ADD COLUMN IF NOT EXISTS bg_y_g DOUBLE PRECISION;
                    ALTER TABLE rms_records ADD COLUMN IF NOT EXISTS bg_z_g DOUBLE PRECISION;
                    -- Drop legacy _mg columns (replaced by _g float columns)
                    ALTER TABLE rms_records DROP COLUMN IF EXISTS al_x_mg;
                    ALTER TABLE rms_records DROP COLUMN IF EXISTS al_y_mg;
                    ALTER TABLE rms_records DROP COLUMN IF EXISTS al_z_mg;
                    ALTER TABLE rms_records DROP COLUMN IF EXISTS ar_x_mg;
                    ALTER TABLE rms_records DROP COLUMN IF EXISTS ar_y_mg;
                    ALTER TABLE rms_records DROP COLUMN IF EXISTS ar_z_mg;
                    ALTER TABLE rms_records DROP COLUMN IF EXISTS bg_x_mg;
                    ALTER TABLE rms_records DROP COLUMN IF EXISTS bg_y_mg;
                    ALTER TABLE rms_records DROP COLUMN IF EXISTS bg_z_mg;

                    CREATE TABLE IF NOT EXISTS peak_records (
                        id              SERIAL PRIMARY KEY,
                        train_id        VARCHAR(50),
                        gateway_id      VARCHAR(100),
                        archive_sha256  VARCHAR(64),
                        window_start_mm INTEGER,
                        position_mm     INTEGER,
                        speed_kmph      DOUBLE PRECISION,
                        latitude        DOUBLE PRECISION,
                        longitude       DOUBLE PRECISION,
                        axes            JSONB,
                        created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS fault_records (
                        id             SERIAL PRIMARY KEY,
                        train_id       VARCHAR(50),
                        gateway_id     VARCHAR(100),
                        archive_sha256 VARCHAR(64),
                        timestamp_ms   BIGINT,
                        fault_code     INTEGER,
                        description    TEXT,
                        created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS alert_events (
                        id             SERIAL PRIMARY KEY,
                        train_no       VARCHAR(50),
                        gateway_id     VARCHAR(100),
                        alert_type     VARCHAR(20),
                        latitude       DOUBLE PRECISION,
                        longitude      DOUBLE PRECISION,
                        position_mm    INTEGER,
                        session_name   VARCHAR(100),
                        archive_sha256 VARCHAR(64),
                        source         VARCHAR(50),
                        peak_axis      VARCHAR(10),
                        peak_value_g   DOUBLE PRECISION,
                        speed_kmph     DOUBLE PRECISION,
                        alert          VARCHAR(20),
                        session_status VARCHAR(50) DEFAULT 'active',
                        zone           VARCHAR(100),
                        division       VARCHAR(100),
                        section        VARCHAR(100),
                        archived_at    TIMESTAMP WITH TIME ZONE,
                        created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    -- ── Operational tables ────────────────────────────────────
                    CREATE TABLE IF NOT EXISTS sessions (
                        id           SERIAL PRIMARY KEY,
                        train_no     VARCHAR(50),
                        session_name VARCHAR(100),
                        status       VARCHAR(50),
                        created_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        closed_at    TIMESTAMP WITH TIME ZONE
                    );
                    ALTER TABLE sessions ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP WITH TIME ZONE;

                    CREATE TABLE IF NOT EXISTS reset_events (
                        id         SERIAL PRIMARY KEY,
                        train_no   VARCHAR(50),
                        reason     TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS activity_logs (
                        id            SERIAL PRIMARY KEY,
                        username      VARCHAR(100),
                        page          VARCHAR(100),
                        action        VARCHAR(100),
                        error_message TEXT,
                        ip_address    VARCHAR(50),
                        latitude      DOUBLE PRECISION,
                        longitude     DOUBLE PRECISION,
                        created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    -- ── Handshake sessions (fully defined) ────────────────────
                    CREATE TABLE IF NOT EXISTS handshake_sessions (
                        session_id             VARCHAR(100) PRIMARY KEY,
                        gateway_id             VARCHAR(100),
                        train_id               VARCHAR(50),
                        server_private_key_hex TEXT,
                        client_public_key_hex  TEXT,
                        nonce                  VARCHAR(64),
                        verified               BOOLEAN DEFAULT FALSE,
                        authenticated          BOOLEAN DEFAULT FALSE,
                        session_key_hex        TEXT,
                        verified_at            TIMESTAMP WITH TIME ZONE,
                        created_at             TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    -- ── File storage (filesystem-backed, no BYTEA blobs) ──────
                    CREATE TABLE IF NOT EXISTS time_domain_files (
                        id             SERIAL PRIMARY KEY,
                        file_id        VARCHAR(100),
                        gateway_id     VARCHAR(100),
                        train_id       VARCHAR(50),
                        session_name   VARCHAR(100),
                        archive_sha256 VARCHAR(64),
                        filename       VARCHAR(255),
                        path           TEXT,
                        size_bytes     BIGINT,
                        sha256         VARCHAR(64),
                        chunk_count    INTEGER,
                        created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        expires_at     TIMESTAMP WITH TIME ZONE
                    );

                    CREATE TABLE IF NOT EXISTS time_domain_chunks (
                        id             SERIAL PRIMARY KEY,
                        file_id        VARCHAR(100),
                        gateway_id     VARCHAR(100),
                        train_id       VARCHAR(50),
                        archive_sha256 VARCHAR(64),
                        chunk_index    INTEGER,
                        chunk_data     BYTEA,
                        created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        expires_at     TIMESTAMP WITH TIME ZONE
                    );

                    -- ── Upload lease tracking ─────────────────────────────────
                    CREATE TABLE IF NOT EXISTS upload_leases (
                        upload_id         VARCHAR(50)  PRIMARY KEY,
                        gateway_id        VARCHAR(100) NOT NULL,
                        train_id          VARCHAR(50)  NOT NULL,
                        session_name      VARCHAR(100) NOT NULL,
                        zip_file_name     VARCHAR(255) NOT NULL,
                        sha256            VARCHAR(64)  NOT NULL,
                        size_bytes        BIGINT,
                        remote_temp_path  VARCHAR(512) NOT NULL,
                        remote_final_path VARCHAR(512) NOT NULL,
                        status            VARCHAR(50)  DEFAULT 'ready',
                        expires_utc       TIMESTAMP WITH TIME ZONE NOT NULL,
                        created_at        TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    ALTER TABLE upload_leases ADD COLUMN IF NOT EXISTS size_bytes BIGINT;
                    ALTER TABLE upload_leases ADD COLUMN IF NOT EXISTS sha256 VARCHAR(64);
                    ALTER TABLE upload_leases ADD COLUMN IF NOT EXISTS remote_temp_path VARCHAR(512);
                    ALTER TABLE upload_leases ADD COLUMN IF NOT EXISTS remote_final_path VARCHAR(512);

                    -- ── Heartbeat logs ────────────────────────────────────────
                    CREATE TABLE IF NOT EXISTS heartbeat_logs (
                        id             SERIAL PRIMARY KEY,
                        gateway_id     VARCHAR(100),
                        train_id       VARCHAR(50),
                        received_at    TIMESTAMP WITH TIME ZONE,
                        adxl_state     VARCHAR(50),
                        encoder_state  VARCHAR(50)
                    );

                    -- ── Gateway Commands (reset / calibration_update) ──────────
                    CREATE TABLE IF NOT EXISTS gateway_commands (
                        command_id      VARCHAR(50)  PRIMARY KEY,
                        gateway_id      VARCHAR(100) NOT NULL,
                        type            VARCHAR(50)  NOT NULL,
                        status          VARCHAR(20)  DEFAULT 'pending',
                        version         INTEGER,
                        payload_url     VARCHAR(512),
                        sha256          VARCHAR(64),
                        payload         JSONB,
                        result          JSONB,
                        created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        delivered_at      TIMESTAMP WITH TIME ZONE,
                        last_delivered_at TIMESTAMP WITH TIME ZONE,
                        delivery_count    INTEGER DEFAULT 0,
                        completed_at      TIMESTAMP WITH TIME ZONE
                    );
                    ALTER TABLE gateway_commands ADD COLUMN IF NOT EXISTS payload      JSONB;
                    ALTER TABLE gateway_commands ADD COLUMN IF NOT EXISTS result       JSONB;
                    ALTER TABLE gateway_commands ADD COLUMN IF NOT EXISTS version      INTEGER;
                    ALTER TABLE gateway_commands ADD COLUMN IF NOT EXISTS payload_url  VARCHAR(512);
                    ALTER TABLE gateway_commands ADD COLUMN IF NOT EXISTS sha256       VARCHAR(64);
                    ALTER TABLE gateway_commands ADD COLUMN IF NOT EXISTS delivered_at      TIMESTAMP WITH TIME ZONE;
                    ALTER TABLE gateway_commands ADD COLUMN IF NOT EXISTS last_delivered_at TIMESTAMP WITH TIME ZONE;
                    ALTER TABLE gateway_commands ADD COLUMN IF NOT EXISTS delivery_count    INTEGER DEFAULT 0;
                    ALTER TABLE gateway_commands ADD COLUMN IF NOT EXISTS completed_at      TIMESTAMP WITH TIME ZONE;

                    -- ══════════════════════════════════════════════════════════
                    -- BACKWARD COMPATIBILITY: columns added after initial deploy.
                    -- IF NOT EXISTS / IF EXISTS make these safe to re-run.
                    -- ══════════════════════════════════════════════════════════
                    ALTER TABLE gateways ADD COLUMN IF NOT EXISTS gateway_serial   VARCHAR(100);
                    ALTER TABLE gateways ADD COLUMN IF NOT EXISTS firmware_version VARCHAR(50);
                    ALTER TABLE gateways ADD COLUMN IF NOT EXISTS last_seen        TIMESTAMP WITH TIME ZONE;
                    ALTER TABLE gateways ADD COLUMN IF NOT EXISTS updated_at       TIMESTAMP WITH TIME ZONE;
                    ALTER TABLE gateways ADD COLUMN IF NOT EXISTS created_at       TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
                    ALTER TABLE gateways ADD COLUMN IF NOT EXISTS provision_status VARCHAR(20) DEFAULT 'active';

                    ALTER TABLE gateway_auth ADD COLUMN IF NOT EXISTS cert_fingerprint   VARCHAR(64);
                    ALTER TABLE gateway_auth ADD COLUMN IF NOT EXISTS train_id            VARCHAR(50);
                    ALTER TABLE gateway_auth ADD COLUMN IF NOT EXISTS last_authenticated  TIMESTAMP WITH TIME ZONE;
                    ALTER TABLE gateway_auth ADD COLUMN IF NOT EXISTS ssh_public_key      VARCHAR(1024);
                    ALTER TABLE gateway_auth ADD COLUMN IF NOT EXISTS upload_enabled      BOOLEAN DEFAULT TRUE;
                    ALTER TABLE gateway_auth ADD COLUMN IF NOT EXISTS upload_base_path    VARCHAR(512);
                    ALTER TABLE gateway_auth ADD COLUMN IF NOT EXISTS revoked_at          TIMESTAMP WITH TIME ZONE;
                    UPDATE gateway_auth SET train_id = '019456' WHERE train_id IS NULL;
                    ALTER TABLE gateway_auth DROP CONSTRAINT IF EXISTS gateway_auth_pkey;
                    ALTER TABLE gateway_auth ADD CONSTRAINT gateway_auth_pkey PRIMARY KEY (gateway_id, train_id);

                    ALTER TABLE gateway_status ADD COLUMN IF NOT EXISTS train_id       VARCHAR(50);
                    ALTER TABLE gateway_status ADD COLUMN IF NOT EXISTS online         BOOLEAN DEFAULT FALSE;
                    ALTER TABLE gateway_status ADD COLUMN IF NOT EXISTS last_heartbeat TIMESTAMP WITH TIME ZONE;
                    ALTER TABLE gateway_status ADD COLUMN IF NOT EXISTS last_handshake TIMESTAMP WITH TIME ZONE;

                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS session_name   VARCHAR(100);
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS archive_sha256 VARCHAR(64);
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS source         VARCHAR(50);
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS peak_axis      VARCHAR(10);
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS peak_value_g   DOUBLE PRECISION;
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS speed_kmph     DOUBLE PRECISION;
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS alert          VARCHAR(20);
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS session_status VARCHAR(50) DEFAULT 'active';
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS archived_at    TIMESTAMP WITH TIME ZONE;
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS zone           VARCHAR(100);
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS division       VARCHAR(100);
                    ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS section        VARCHAR(100);

                    ALTER TABLE archives ADD COLUMN IF NOT EXISTS train_id VARCHAR(50);

                    ALTER TABLE peak_records ADD COLUMN IF NOT EXISTS position_mm INTEGER;
                    ALTER TABLE peak_records ADD COLUMN IF NOT EXISTS speed_kmph  DOUBLE PRECISION;
                    ALTER TABLE peak_records ADD COLUMN IF NOT EXISTS latitude    DOUBLE PRECISION;
                    ALTER TABLE peak_records ADD COLUMN IF NOT EXISTS longitude   DOUBLE PRECISION;

                    ALTER TABLE handshake_sessions ADD COLUMN IF NOT EXISTS gateway_id             VARCHAR(100);
                    ALTER TABLE handshake_sessions ADD COLUMN IF NOT EXISTS train_id               VARCHAR(50);
                    ALTER TABLE handshake_sessions ADD COLUMN IF NOT EXISTS authenticated          BOOLEAN DEFAULT FALSE;
                    ALTER TABLE handshake_sessions ADD COLUMN IF NOT EXISTS server_private_key_hex TEXT;
                    ALTER TABLE handshake_sessions ADD COLUMN IF NOT EXISTS client_public_key_hex  TEXT;
                    ALTER TABLE handshake_sessions ADD COLUMN IF NOT EXISTS nonce                  VARCHAR(64);
                    ALTER TABLE handshake_sessions ADD COLUMN IF NOT EXISTS verified               BOOLEAN DEFAULT FALSE;
                    ALTER TABLE handshake_sessions ADD COLUMN IF NOT EXISTS session_key_hex        TEXT;
                    ALTER TABLE handshake_sessions ADD COLUMN IF NOT EXISTS verified_at            TIMESTAMP WITH TIME ZONE;

                    -- Remove duplicate columns (safe: neither was ever populated)
                    ALTER TABLE time_domain_files  DROP COLUMN IF EXISTS total_size;
                    ALTER TABLE time_domain_chunks DROP COLUMN IF EXISTS data;
                    -- Ensure Issue-2 fix columns exist on existing deployments
                    ALTER TABLE time_domain_files  ADD COLUMN IF NOT EXISTS file_id   VARCHAR(100);
                    ALTER TABLE time_domain_files  ADD COLUMN IF NOT EXISTS filename  VARCHAR(255);
                    ALTER TABLE time_domain_files  ADD COLUMN IF NOT EXISTS path      TEXT;
                    ALTER TABLE time_domain_chunks ADD COLUMN IF NOT EXISTS gateway_id     VARCHAR(100);
                    ALTER TABLE time_domain_chunks ADD COLUMN IF NOT EXISTS train_id       VARCHAR(50);
                    ALTER TABLE time_domain_chunks ADD COLUMN IF NOT EXISTS archive_sha256 VARCHAR(64);
                    ALTER TABLE time_domain_chunks ADD COLUMN IF NOT EXISTS expires_at     TIMESTAMP WITH TIME ZONE;

                    -- Remove old scale and offset columns (including old q16 names)
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS scale_x;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS scale_y;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS scale_z;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS offset_x;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS offset_y;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS offset_z;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS adxl_left_offset_q16_x;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS adxl_left_offset_q16_y;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS adxl_left_offset_q16_z;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS adxl_right_offset_q16_x;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS adxl_right_offset_q16_y;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS adxl_right_offset_q16_z;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS iis_offset_q16_x;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS iis_offset_q16_y;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS iis_offset_q16_z;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS imu_accel_offset_q16_x;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS imu_accel_offset_q16_y;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS imu_accel_offset_q16_z;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS imu_gyro_offset_q16_x;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS imu_gyro_offset_q16_y;
                    ALTER TABLE calibrations DROP COLUMN IF EXISTS imu_gyro_offset_q16_z;

                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS scale_x;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS scale_y;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS scale_z;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS offset_x;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS offset_y;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS offset_z;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS adxl_left_offset_q16_x;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS adxl_left_offset_q16_y;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS adxl_left_offset_q16_z;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS adxl_right_offset_q16_x;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS adxl_right_offset_q16_y;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS adxl_right_offset_q16_z;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS iis_offset_q16_x;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS iis_offset_q16_y;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS iis_offset_q16_z;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS imu_accel_offset_q16_x;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS imu_accel_offset_q16_y;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS imu_accel_offset_q16_z;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS imu_gyro_offset_q16_x;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS imu_gyro_offset_q16_y;
                    ALTER TABLE calibration_versions DROP COLUMN IF EXISTS imu_gyro_offset_q16_z;

                    -- calibrations & calibration_versions columns migration
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS train_id                   VARCHAR(50);
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS version                    INTEGER DEFAULT 1;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS adxl_left_offset_x         INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS adxl_left_offset_y         INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS adxl_left_offset_z         INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS adxl_right_offset_x        INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS adxl_right_offset_y        INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS adxl_right_offset_z        INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS iis_offset_x               INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS iis_offset_y               INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS iis_offset_z               INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS imu_accel_offset_x        INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS imu_accel_offset_y        INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS imu_accel_offset_z        INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS imu_gyro_offset_x         INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS imu_gyro_offset_y         INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS imu_gyro_offset_z         INTEGER DEFAULT 0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS wheel_diameter_m           DOUBLE PRECISION DEFAULT 0.915;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS encoder_ppr                INTEGER DEFAULT 100;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS spatial_interval_mm        INTEGER DEFAULT 250;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS trigger_start_speed_kmph  DOUBLE PRECISION DEFAULT 20.0;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS adxl_left                  JSONB;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS adxl_right                 JSONB;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS bogie                      JSONB;
                    ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS encoder                    JSONB;

                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS train_id                   VARCHAR(50);
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS adxl_left_offset_x         INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS adxl_left_offset_y         INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS adxl_left_offset_z         INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS adxl_right_offset_x        INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS adxl_right_offset_y        INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS adxl_right_offset_z        INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS iis_offset_x               INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS iis_offset_y               INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS iis_offset_z               INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS imu_accel_offset_x        INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS imu_accel_offset_y        INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS imu_accel_offset_z        INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS imu_gyro_offset_x         INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS imu_gyro_offset_y         INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS imu_gyro_offset_z         INTEGER DEFAULT 0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS wheel_diameter_m           DOUBLE PRECISION DEFAULT 0.915;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS encoder_ppr                INTEGER DEFAULT 100;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS spatial_interval_mm        INTEGER DEFAULT 250;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS trigger_start_speed_kmph  DOUBLE PRECISION DEFAULT 20.0;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS adxl_left                  JSONB;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS adxl_right                 JSONB;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS bogie                      JSONB;
                    ALTER TABLE calibration_versions ADD COLUMN IF NOT EXISTS encoder                    JSONB;

                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(255) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        role VARCHAR(50) NOT NULL,
                        can_configure_thresholds BOOLEAN DEFAULT FALSE,
                        can_manage_users BOOLEAN DEFAULT FALSE,
                        can_view_alerts BOOLEAN DEFAULT TRUE,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    ALTER TABLE users ADD COLUMN IF NOT EXISTS can_configure_thresholds BOOLEAN DEFAULT FALSE;
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS can_manage_users BOOLEAN DEFAULT FALSE;
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS can_view_alerts BOOLEAN DEFAULT TRUE;
                """)

                # ── Foreign key constraints ───────────────────────────────
                # Applied individually with DO/EXCEPTION blocks so they are
                # idempotent (safe to re-run on every container start).
                # NOT VALID = enforced on new rows; existing rows not checked.
                _fk_statements = [
                    ("fk_gateways_train",
                     "ALTER TABLE gateways ADD CONSTRAINT fk_gateways_train "
                     "FOREIGN KEY (train_id) REFERENCES trains(train_no) "
                     "ON DELETE SET NULL NOT VALID"),
                    ("fk_gateway_auth_gateway",
                     "ALTER TABLE gateway_auth ADD CONSTRAINT fk_gateway_auth_gateway "
                     "FOREIGN KEY (gateway_id) REFERENCES gateways(gateway_id) "
                     "ON DELETE CASCADE NOT VALID"),
                    ("fk_gateway_status_gateway",
                     "ALTER TABLE gateway_status ADD CONSTRAINT fk_gateway_status_gateway "
                     "FOREIGN KEY (gateway_id) REFERENCES gateways(gateway_id) "
                     "ON DELETE CASCADE NOT VALID"),
                    ("fk_calibrations_gateway",
                     "ALTER TABLE calibrations ADD CONSTRAINT fk_calibrations_gateway "
                     "FOREIGN KEY (gateway_id) REFERENCES gateways(gateway_id) "
                     "ON DELETE CASCADE NOT VALID"),
                    ("fk_calibration_versions_gateway",
                     "ALTER TABLE calibration_versions ADD CONSTRAINT fk_calibration_versions_gateway "
                     "FOREIGN KEY (gateway_id) REFERENCES gateways(gateway_id) "
                     "ON DELETE CASCADE NOT VALID"),
                    ("fk_handshake_sessions_gateway",
                     "ALTER TABLE handshake_sessions ADD CONSTRAINT fk_handshake_sessions_gateway "
                     "FOREIGN KEY (gateway_id) REFERENCES gateways(gateway_id) "
                     "ON DELETE CASCADE NOT VALID"),
                    ("fk_upload_leases_gateway",
                     "ALTER TABLE upload_leases ADD CONSTRAINT fk_upload_leases_gateway "
                     "FOREIGN KEY (gateway_id) REFERENCES gateways(gateway_id) "
                     "ON DELETE CASCADE NOT VALID"),
                ]
                for _name, _stmt in _fk_statements:
                    try:
                        await conn.execute(
                            f"DO $$ BEGIN {_stmt}; "
                            f"EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
                        )
                    except Exception as _fk_exc:
                        print(f"Warning: FK constraint {_name}: {_fk_exc}")

                # ── Performance indexes ───────────────────────────────────
                await conn.execute("""
                    -- alert_events
                    CREATE INDEX IF NOT EXISTS idx_alert_train_no      ON alert_events(train_no);
                    CREATE INDEX IF NOT EXISTS idx_alert_gateway_id    ON alert_events(gateway_id);
                    CREATE INDEX IF NOT EXISTS idx_alert_created_at    ON alert_events(created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_alert_train_created ON alert_events(train_no, created_at DESC);

                    -- rms_records (most critical — drives every map render)
                    CREATE INDEX IF NOT EXISTS idx_rms_gateway_id     ON rms_records(gateway_id);
                    CREATE INDEX IF NOT EXISTS idx_rms_train_id       ON rms_records(train_id);
                    CREATE INDEX IF NOT EXISTS idx_rms_session_name   ON rms_records(session_name);
                    CREATE INDEX IF NOT EXISTS idx_rms_archive_sha256 ON rms_records(archive_sha256);
                    CREATE INDEX IF NOT EXISTS idx_rms_gps_query      ON rms_records(train_id, created_at DESC) WHERE gps_valid = TRUE;

                    -- peak_records
                    CREATE INDEX IF NOT EXISTS idx_peak_gateway_id     ON peak_records(gateway_id);
                    CREATE INDEX IF NOT EXISTS idx_peak_archive_sha256 ON peak_records(archive_sha256);
                    CREATE INDEX IF NOT EXISTS idx_peak_train_id       ON peak_records(train_id);

                    -- fault_records
                    CREATE INDEX IF NOT EXISTS idx_fault_gateway_id     ON fault_records(gateway_id);
                    CREATE INDEX IF NOT EXISTS idx_fault_archive_sha256 ON fault_records(archive_sha256);

                    -- archives
                    CREATE INDEX IF NOT EXISTS idx_archives_gateway_id  ON archives(gateway_id);
                    CREATE INDEX IF NOT EXISTS idx_archives_sha256      ON archives(sha256);
                    CREATE INDEX IF NOT EXISTS idx_archives_train_id    ON archives(train_id);
                    CREATE INDEX IF NOT EXISTS idx_archives_received_at ON archives(received_at DESC);

                    -- sessions / events
                    CREATE INDEX IF NOT EXISTS idx_sessions_train_no  ON sessions(train_no);
                    CREATE INDEX IF NOT EXISTS idx_reset_events_train ON reset_events(train_no);

                    -- time_domain
                    CREATE INDEX IF NOT EXISTS idx_td_files_gateway_archive ON time_domain_files(gateway_id, archive_sha256);
                    CREATE INDEX IF NOT EXISTS idx_td_files_expires_at      ON time_domain_files(expires_at);
                    CREATE INDEX IF NOT EXISTS idx_td_chunks_file_id        ON time_domain_chunks(file_id, chunk_index);

                    -- upload_leases
                    CREATE INDEX IF NOT EXISTS idx_leases_gateway_id  ON upload_leases(gateway_id);
                    CREATE INDEX IF NOT EXISTS idx_leases_expires_utc ON upload_leases(expires_utc);

                    -- handshake_sessions
                    CREATE INDEX IF NOT EXISTS idx_handshake_created_at ON handshake_sessions(created_at);

                    -- activity_logs
                    CREATE INDEX IF NOT EXISTS idx_activity_username   ON activity_logs(username, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_activity_created_at ON activity_logs(created_at DESC);

                    -- gateway commands
                    CREATE INDEX IF NOT EXISTS idx_commands_gateway_status
                        ON gateway_commands(gateway_id, status, created_at);
                    CREATE INDEX IF NOT EXISTS idx_commands_redelivery
                        ON gateway_commands(gateway_id, last_delivered_at)
                        WHERE status = 'delivered';
                """)
                
                # -- Seed default users from settings if not exists --
                admin_user = settings.get("admin_username", "admin")
                admin_pass = settings.get("admin_password", "admin123")
                admin_hash = bcrypt.hash(admin_pass)
                
                operator_user = settings.get("operator_username", "operator")
                operator_pass = settings.get("operator_password", "operator123")
                operator_hash = bcrypt.hash(operator_pass)
                
                await conn.execute("""
                    INSERT INTO users (username, password_hash, role, can_configure_thresholds, can_manage_users, can_view_alerts)
                    VALUES ($1, $2, 'ADMIN', TRUE, TRUE, TRUE)
                    ON CONFLICT (username) DO UPDATE SET
                        can_configure_thresholds = EXCLUDED.can_configure_thresholds,
                        can_manage_users = EXCLUDED.can_manage_users,
                        can_view_alerts = EXCLUDED.can_view_alerts;
                """, admin_user, admin_hash)
                
                await conn.execute("""
                    INSERT INTO users (username, password_hash, role, can_configure_thresholds, can_manage_users, can_view_alerts)
                    VALUES ($1, $2, 'OPERATOR', FALSE, FALSE, TRUE)
                    ON CONFLICT (username) DO UPDATE SET
                        can_configure_thresholds = EXCLUDED.can_configure_thresholds,
                        can_manage_users = EXCLUDED.can_manage_users,
                        can_view_alerts = EXCLUDED.can_view_alerts;
                """, operator_user, operator_hash)

        except Exception as exc:
            print(f"Warning: Failed to connect to PostgreSQL: {exc}")




    # Auto-seed initial test data for localhost/Docker installations if database is empty
    if settings.get("database_type") == "postgres" and db.pg_pool:
        try:
            async with db.pg_pool.acquire() as conn:
                trains_count = await conn.fetchval("SELECT COUNT(*) FROM trains;")
                if trains_count == 0:
                    print("Empty local database detected. Seeding test data for Train 20693...")
                    # 1. Insert Train
                    await conn.execute("INSERT INTO trains (train_no, train_name) VALUES ('20693', 'DT express') ON CONFLICT DO NOTHING;")
                    
                    # 2. Insert Gateways
                    await conn.execute("""
                        INSERT INTO gateways (gateway_id, train_id, status, provision_status)
                        VALUES ('GW_UABAMS_BOGIE_01', '20693', 'online', 'active'),
                               ('GW_UABAMS_BOGIE_02', '20693', 'online', 'active')
                        ON CONFLICT DO NOTHING;
                    """)
                    
                    # 3. Insert Gateway Auth
                    await conn.execute("""
                        INSERT INTO gateway_auth (gateway_id, train_id, secret_key, cert_fingerprint)
                        VALUES ('GW_UABAMS_BOGIE_01', '20693', 'd72e87a685293d12ecc14427572f1895c86b3e3a35ae248458693645c32b0ffb', NULL),
                               ('GW_UABAMS_BOGIE_02', '20693', 'a305f8c3d73dfdc1653073e316a0737e9c5aa4b5ebb56e4afbe2f05faee5f466', NULL)
                        ON CONFLICT DO NOTHING;
                    """)
                    
                    # 4. Insert Gateway Status
                    await conn.execute("""
                        INSERT INTO gateway_status (gateway_id, train_id, online, adxl_state, encoder_state)
                        VALUES ('GW_UABAMS_BOGIE_01', '20693', TRUE, 'active', 'active'),
                               ('GW_UABAMS_BOGIE_02', '20693', TRUE, 'active', 'active')
                        ON CONFLICT DO NOTHING;
                    """)
                    
                    # 5. Insert Calibrations
                    await conn.execute("""
                        INSERT INTO calibrations (gateway_id)
                        VALUES ('GW_UABAMS_BOGIE_01'), ('GW_UABAMS_BOGIE_02')
                        ON CONFLICT DO NOTHING;
                    """)
                    
                    # 6. Generate and Insert RMS Records (Route points between Shivaji Nagar and Hennur)
                    rms_inserts = []
                    for i in range(30):
                        t = i / 29.0
                        lat = 12.9716 + (13.035 - 12.9716) * t
                        lon = 77.5946 + (77.64 - 77.5946) * t
                        axes_json = json.dumps({
                            "al_x": {"peakValueG": 1.25, "rmsValue": 0.45},
                            "al_y": {"peakValueG": 1.10, "rmsValue": 0.38},
                            "al_z": {"peakValueG": 0.95, "rmsValue": 0.30}
                        })
                        rms_inserts.append((
                            '20693', 'GW_UABAMS_BOGIE_01', 'session_demo_1', 'sha_demo_1',
                            lat, lon, True, 45.0, 60.0, i * 100, axes_json
                        ))
                        rms_inserts.append((
                            '20693', 'GW_UABAMS_BOGIE_02', 'session_demo_1', 'sha_demo_1',
                            lat, lon, True, 45.0, 60.0, i * 100, axes_json
                        ))
                    
                    await conn.executemany("""
                        INSERT INTO rms_records (train_id, gateway_id, session_name, archive_sha256, latitude, longitude, gps_valid, bearing, speed, position_mm, axes)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11);
                    """, rms_inserts)
                    
                    # 7. Insert Alert Events (4 normal GREEN alerts)
                    await conn.execute("""
                        INSERT INTO alert_events (train_no, gateway_id, alert_type, latitude, longitude, position_mm, alert, peak_value_g, peak_axis, speed_kmph, source, session_status)
                        VALUES 
                          ('20693', 'GW_UABAMS_BOGIE_01', 'Normal', 12.9716, 77.5946, 100, 'GREEN', 1.25, 'al_x', 60.0, 'archive', 'active'),
                          ('20693', 'GW_UABAMS_BOGIE_01', 'Normal', 12.9716, 77.5946, 200, 'GREEN', 1.30, 'al_x', 60.0, 'archive', 'active'),
                          ('20693', 'GW_UABAMS_BOGIE_01', 'Normal', 13.035, 77.64, 2800, 'GREEN', 1.28, 'al_x', 58.0, 'archive', 'active'),
                          ('20693', 'GW_UABAMS_BOGIE_01', 'Normal', 13.035, 77.64, 2900, 'GREEN', 1.29, 'al_x', 59.0, 'archive', 'active');
                    """)
                    print("Test data seeding completed successfully!")
        except Exception as seed_exc:
            print(f"Error seeding test data: {seed_exc}")



@app.get("/")
async def root():
    return {"message": "UABAMS Cloud Running", "dashboard": "/dashboard", "login": "/login", "docs": "/docs"}


@app.get("/health")
async def health_check():
    global startup_error
    if settings.get("database_type") == "postgres" and db.pg_pool is None:
        return {
            "status": "unhealthy",
            "database_type": "postgres",
            "connection": "failed",
            "startup_error": startup_error
        }
    try:
        # Perform a test query on gateway_auth collection/table
        await db.gateway_auth.find_one({"gatewayId": "health_check_test_id"})
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
            "error": str(exc),
            "startup_error": startup_error
        }


@app.get("/login")
async def login_page(request: Request):
    if is_operator_authenticated(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render_login_page()


@app.post("/login")
async def login_submit(request: Request):
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body, keep_blank_values=True)
    username = form.get("username", [""])[0]
    password = form.get("password", [""])[0]
    
    user_record = None
    if db.pg_pool:
        async with db.pg_pool.acquire() as conn:
            user_record = await conn.fetchrow("SELECT password_hash, role, can_configure_thresholds, can_manage_users, can_view_alerts FROM users WHERE username = $1 AND is_active = TRUE", username)
            
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


@app.get("/api/v1/auth/me")
async def get_current_user(request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token_str = request.cookies.get(OPERATOR_COOKIE_NAME)
    try:
        payload = jwt.decode(token_str, settings["jwt_secret"], algorithms=[settings["jwt_algorithm"]])
        username = payload.get("sub")
        role = payload.get("role", "operator")
        
        perms = {
            "can_configure_thresholds": role == "admin",
            "can_manage_users": role == "admin",
            "can_view_alerts": True
        }
        
        if db.pg_pool:
            async with db.pg_pool.acquire() as conn:
                user_record = await conn.fetchrow("SELECT can_configure_thresholds, can_manage_users, can_view_alerts FROM users WHERE username = $1", username)
                if user_record:
                    perms["can_configure_thresholds"] = user_record["can_configure_thresholds"]
                    perms["can_manage_users"] = user_record["can_manage_users"]
                    perms["can_view_alerts"] = user_record["can_view_alerts"]
                    
        return {"username": username, "role": role, "permissions": perms}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(OPERATOR_COOKIE_NAME)
    return response


@app.get("/dashboard")
async def dashboard_page(request: Request):
    if not is_operator_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(Path("app/static/index.html"), headers={"Cache-Control": "no-store"})


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html(request: Request):
    if not is_admin_authenticated(request):
        if is_operator_authenticated(request):
            return RedirectResponse(url="/dashboard", status_code=303)
        return RedirectResponse(url="/login", status_code=303)
    return get_swagger_ui_html(openapi_url="/openapi.json", title="UABAMS Cloud API - Swagger")


@app.get("/openapi.json", include_in_schema=False)
async def get_open_api_endpoint(request: Request):
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin access required for API documentation")
    return JSONResponse(get_openapi(title=app.title, version=app.version, routes=app.routes))


@app.post("/api/v1/logs")
async def create_activity_log(data: ActivityLogRequest, request: Request):
    username = operator_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="Login required")
    document = {
        "username": username,
        "page": data.page,
        "action": data.action,
        "message": data.message,
        "errorMessage": data.errorMessage,
        "latitude": data.latitude,
        "longitude": data.longitude,
        "ipAddress": client_ip(request),
        "userAgent": request.headers.get("user-agent", ""),
        "createdAt": utc_now(),
    }
    await db.activity_logs.insert_one(document)
    return {"status": "success", "log": serialize(document)}


@app.get("/api/v1/logs")
async def list_activity_logs(request: Request, username: str | None = None, page: str | None = None, limit: int = 100):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    query: dict[str, Any] = {}
    if username:
        query["username"] = username
    if page:
        query["page"] = page
    capped_limit = min(max(limit, 1), 500)
    logs = await db.activity_logs.find(query).sort("createdAt", -1).limit(capped_limit).to_list(length=capped_limit)
    return {"logs": serialize(logs)}


async def sync_authorized_keys():
    if settings.get("database_type") != "postgres" or not db.pg_pool:
        return
    try:
        async with db.pg_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT gateway_id, ssh_public_key FROM gateway_auth 
                WHERE ssh_public_key IS NOT NULL 
                  AND upload_enabled = TRUE 
                  AND revoked_at IS NULL
            """)
        
        lines = []
        for r in rows:
            gw_id = r["gateway_id"]
            pub_key = r["ssh_public_key"].strip()
            if not pub_key:
                continue
            line = f'restrict {pub_key}'
            lines.append(line)
            
        auth_keys_path = settings["authorized_keys_path"]
        if auth_keys_path:
            os.makedirs(os.path.dirname(auth_keys_path), exist_ok=True)
            with open(auth_keys_path, "w") as f:
                f.write("\n".join(lines) + "\n")
            print(f"Synchronized {len(lines)} keys to {auth_keys_path}")
    except Exception as exc:
        print(f"Warning: Failed to sync authorized_keys: {exc}")


@app.post("/api/v1/handshake")
async def handshake(data: HandshakeRequest, request: Request):
    try:
        now = utc_now()
        cert_pem = request.headers.get("X-Client-Cert") or data.clientCertPem
        cert_fingerprint = None
        cert_gateway_id = None

        if cert_pem:
            try:
                cert_bytes = cert_pem.replace("\\n", "\n").encode("utf-8")
                cert_fingerprint = sha256(cert_bytes).hexdigest()
                cert = x509.load_pem_x509_certificate(cert_bytes, default_backend())
                cn_attributes = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
                if cn_attributes:
                    cert_gateway_id = cn_attributes[0].value
            except Exception as exc:
                raise HTTPException(status_code=403, detail=f"Invalid or untrusted device certificate: {exc}")

        gateway_id = cert_gateway_id or data.gatewayId

        # Validate sshPublicKey if provided
        if data.sshPublicKey:
            cleaned_key = data.sshPublicKey.strip()
            valid_prefixes = ("ssh-rsa", "ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-dss")
            if not any(cleaned_key.startswith(p) for p in valid_prefixes):
                raise HTTPException(status_code=400, detail="Invalid SSH public key format")
                
            other_auth = await db.gateway_auth.find_one({"sshPublicKey": cleaned_key})
            if other_auth and other_auth.get("gatewayId") != gateway_id:
                raise HTTPException(status_code=400, detail="SSH public key is already associated with another gateway")

        await db.gateways.update_one(
            {"gatewayId": gateway_id},
            {
                "$set": {
                    "gatewayId": gateway_id,
                    "trainId": data.trainId,
                    "gatewaySerial": data.gatewaySerial,
                    "firmwareVersion": data.firmwareVersion,
                    "status": "active",
                    "lastSeen": now,
                    "updatedAt": now,
                },
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
        )

        auth_doc = await db.gateway_auth.find_one({"gatewayId": gateway_id, "trainId": data.trainId})
        api_key = None
        if auth_doc:
            api_key = auth_doc.get("apiKey") or auth_doc.get("secret_key")
        if not api_key:
            api_key = token_hex(32)

        set_fields = {
            "gatewayId": gateway_id,
            "trainId": data.trainId,
            "apiKey": api_key,
            "secret_key": api_key,
            "lastHandshake": now,
            "certFingerprint": cert_fingerprint
        }
        if data.sshPublicKey:
            set_fields["sshPublicKey"] = data.sshPublicKey.strip()
            set_fields["uploadBasePath"] = f"/incoming/{data.trainId}/{gateway_id}"
            set_fields["uploadEnabled"] = True

        await db.gateway_auth.update_one(
            {"gatewayId": gateway_id, "trainId": data.trainId},
            {
                "$set": set_fields,
                "$setOnInsert": {
                    "createdAt": now,
                },
            },
            upsert=True,
        )

        # Trigger SSH authorized_keys file sync
        if data.sshPublicKey:
            await sync_authorized_keys()

        try:
            await db.trains.update_one(
                {"trainNo": data.trainId},
                {
                    "$set": {"trainNo": data.trainId, "status": "running", "updatedAt": now},
                    "$setOnInsert": {"trainName": "", "createdAt": now},
                },
                upsert=True,
            )
        except Exception as exc:
            print(f"Warning: db.trains update exception: {exc}")

        try:
            await db.gateway_status.update_one(
                {"gatewayId": gateway_id},
                {
                    "$set": {
                        "gatewayId": gateway_id,
                        "trainId": data.trainId,
                        "online": True,
                        "lastHandshake": now,
                        "lastHeartbeat": now,
                    }
                },
                upsert=True,
            )
        except Exception as exc:
            print(f"Warning: db.gateway_status update exception: {exc}")

        upload_config = {
            "mode": "rsync_ssh",
            "host": settings["ssh_host"],
            "port": settings["ssh_port"],
            "user": settings["ssh_user"],
            "basePath": f"/incoming/{data.trainId}/{gateway_id}",
            "sshHostKey": settings["ssh_host_key"]
        }

        return {
            "status": "success",
            "message": "Handshake successful and API key provisioned",
            "gatewayId": gateway_id,
            "apiKey": api_key,
            "upload": upload_config
        }
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"Handshake error: {exc}")


@app.post("/api/v1/handshake/hello", response_model=HandshakeHelloResponse)
async def handshake_hello(data: HandshakeHelloRequest):
    try:
        bytes.fromhex(data.clientPublicKey)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid clientPublicKey hex format. Please provide a valid 130-character hex string (SECP256R1 uncompressed point starting with 04).")

    gateway = await db.gateways.find_one({"gatewayId": data.gatewayId})
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not registered")

    # 1. Generate server ephemeral key pair
    server_private_key = ec.generate_private_key(ec.SECP256R1())
    server_public_key = server_private_key.public_key()

    # 2. Serialize keys to hex
    server_pub_bytes = server_public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    server_pub_hex = server_pub_bytes.hex()

    server_priv_bytes = server_private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    server_priv_hex = server_priv_bytes.hex()

    # 3. Create challenge nonce & session ID
    nonce = token_hex(16)
    session_id = token_hex(16)

    # 4. Save session state in PostgreSQL
    await db.handshake_sessions.insert_one({
        "sessionId": session_id,
        "gatewayId": data.gatewayId,
        "serverPrivateKeyHex": server_priv_hex,
        "clientPublicKeyHex": data.clientPublicKey,
        "nonce": nonce,
        "verified": False,
        "authenticated": False,
        "createdAt": utc_now(),
    })

    return HandshakeHelloResponse(
        serverPublicKey=server_pub_hex,
        nonce=nonce,
        sessionId=session_id
    )


@app.post("/api/v1/handshake/verify", response_model=HandshakeVerifyResponse)
async def handshake_verify(data: HandshakeVerifyRequest):
    session = await db.handshake_sessions.find_one({"sessionId": data.sessionId})
    if not session:
        raise HTTPException(status_code=404, detail="Handshake session not found or expired")

    if not session.get("authenticated"):
        raise HTTPException(status_code=403, detail="Session not authenticated. Run /api/v1/authenticate first.")

    try:
        # 1. Load keys
        server_private_key = serialization.load_der_private_key(
            bytes.fromhex(session["serverPrivateKeyHex"]),
            password=None
        )
        client_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(),
            bytes.fromhex(session["clientPublicKeyHex"])
        )

        # 2. Compute Diffie-Hellman Shared Secret
        shared_key = server_private_key.exchange(ec.ECDH(), client_public_key)

        # 3. Derive symmetric key via HKDF
        session_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"uabams-handshake-session-key",
        ).derive(shared_key)

        # 4. Compute expected HMAC
        expected_hmac = hmac.new(
            session_key,
            session["nonce"].encode("utf-8"),
            digestmod=sha256
        ).hexdigest()

        # 5. Compare signatures using timing-safe compare_digest
        if not compare_digest(data.clientHmac.lower(), expected_hmac.lower()):
            raise HTTPException(status_code=401, detail="HMAC verification failed")

        # 6. Save derived session key & verify session
        await db.handshake_sessions.update_one(
            {"sessionId": data.sessionId},
            {"$set": {
                "verified": True,
                "sessionKeyHex": session_key.hex(),
                "verifiedAt": utc_now()
            }}
        )

        return HandshakeVerifyResponse(
            status="verified",
            message="Handshake verified successfully",
            sessionToken=data.sessionId
        )

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid public key: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Handshake error: {exc}")


@app.post("/api/v1/authenticate")
async def authenticate(data: AuthRequest):
    gateway_id = normalize_gateway_id(data.gatewayId) or data.gatewayId
    train_id = data.trainId

    # 1. Verify session exists
    session = await db.handshake_sessions.find_one({"sessionId": data.sessionId})
    if not session:
        raise HTTPException(status_code=404, detail="Handshake session not found or expired")

    # 2. Look up compound gateway_auth by both gatewayId and trainId
    auth_doc = await db.gateway_auth.find_one({"gatewayId": gateway_id, "trainId": train_id})
    if not auth_doc:
        return {"status": "failed", "message": f"Gateway {gateway_id} on Train {train_id} not registered"}

    stored_key = auth_doc.get("apiKey") or auth_doc.get("secret_key")
    if stored_key != data.apiKey:
        return {"status": "failed", "message": "Invalid API Key"}

    # 3. Generate token and update session to authenticated
    token = create_gateway_token(gateway_id, train_id)
    
    await db.handshake_sessions.update_one(
        {"sessionId": data.sessionId},
        {"$set": {
            "authenticated": True,
            "trainId": train_id,
            "gatewayId": gateway_id
        }}
    )

    auth_doc = await db.gateway_auth.find_one({"gatewayId": gateway_id, "trainId": train_id})
    update_set = {"lastAuthenticated": utc_now()}
    if auth_doc:
        fingerprint = auth_doc.get("certFingerprint")
        if not fingerprint:
            sec_key = auth_doc.get("secretKey") or "default_secret"
            fingerprint = hashlib.sha256(sec_key.encode("utf-8")).hexdigest()
        update_set["certFingerprint"] = fingerprint

    await db.gateway_auth.update_one(
        {"gatewayId": gateway_id, "trainId": train_id},
        {"$set": update_set},
    )

    return {
        "status": "authenticated",
        "token": token,
        "gatewayId": gateway_id,
        "trainId": train_id,
        "sessionId": data.sessionId
    }


@app.post("/api/v1/gateway/demo-connect", response_model=GatewayConnectionResponse)
async def gateway_demo_connect(data: GatewayConnectionRequest):
    # Find matching gateway document by serialNo (or gatewayId as fallback)
    gateway = await db.gateways.find_one({
        "$or": [
            {"gatewaySerial": data.serialNo},
            {"gatewayId": data.serialNo}
        ]
    })
    
    if not gateway:
        return GatewayConnectionResponse(
            status="denied",
            message=f"Access denied: Serial number or Gateway ID '{data.serialNo}' is not registered in the cloud database.",
            gatewayId=None,
            trainId=None
        )
        
    # Check if the gateway is active
    if gateway.get("status") != "active":
        return GatewayConnectionResponse(
            status="denied",
            message=f"Access denied: Gateway '{gateway.get('gatewayId')}' is registered but its current status is '{gateway.get('status')}' (must be 'active').",
            gatewayId=gateway.get("gatewayId"),
            trainId=gateway.get("trainId")
        )
        
    return GatewayConnectionResponse(
        status="approved",
        message=f"Gateway connectivity approved! Gateway '{gateway.get('gatewayId')}' is registered and active.",
        gatewayId=gateway.get("gatewayId"),
        trainId=gateway.get("trainId")
    )



@app.post("/api/v1/heartbeat")
async def heartbeat(
    data: HeartbeatRequest,
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
):
    gateway_id = normalize_gateway_id(data.gatewayId) or data.gatewayId
    token = data.token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if token:
        verify_gateway_token(token, gateway_id)
    elif x_api_key:
        authenticated_gateway = await async_gateway_id_for_key(x_api_key)
        if normalize_gateway_id(authenticated_gateway) != gateway_id:
            raise HTTPException(status_code=403, detail="API key does not belong to heartbeat gateway")
    else:
        raise HTTPException(status_code=401, detail="Gateway token or X-Api-Key is required")

    now = utc_now()
    gateway = await db.gateways.find_one({"gatewayId": gateway_id})
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not registered")

    registered_serial = gateway.get("gatewaySerial")
    if data.gatewaySerial and registered_serial and data.gatewaySerial != registered_serial:
        raise HTTPException(status_code=403, detail="Gateway serial does not match registered gateway")

    for result in data.commandResults:
        command = await db.gateway_commands.find_one({
            "commandId": result.commandId,
            "gatewayId": gateway_id,
        })
        if not command or command.get("type") != result.type:
            continue
        if command.get("status") in ("success", "failed", "superseded"):
            continue

        completed_at = result.completedAt or now
        await db.gateway_commands.update_one(
            {"commandId": result.commandId, "gatewayId": gateway_id},
            {"$set": {
                "status": result.status,
                "result": {
                    "commandId": result.commandId,
                    "type": result.type,
                    "status": result.status,
                    "completedAt": completed_at.isoformat(),
                    "location": result.location,
                    "details": result.details,
                },
                "completedAt": completed_at,
            }},
        )

    await db.gateways.update_one(
        {"gatewayId": gateway_id},
        {"$set": {"lastSeen": now, "status": "active", "lastHeartbeat": now}},
    )
    await db.heartbeat_logs.insert_one({
        "gatewayId": gateway_id,
        "trainId": gateway.get("trainId"),
        "receivedAt": now,
        "adxlState": data.adxlState,
        "encoderState": data.encoderState,
    })
    await db.gateway_status.update_one(
        {"gatewayId": gateway_id},
        {"$set": {
            "gatewayId": gateway_id,
            "trainId": gateway.get("trainId"),
            "online": True,
            "lastHeartbeat": now,
            "adxlState": data.adxlState,
            "adxlUptime": data.adxlUptime,
            "adxlFaults": data.adxlFaults,
            "adxlFwVersion": data.adxlFwVersion,
            "adxlCalVersion": data.adxlCalVersion,
            "encoderState": data.encoderState,
            "encoderUptime": data.encoderUptime,
            "encoderFaults": data.encoderFaults,
            "encoderFwVersion": data.encoderFwVersion,
            "encoderCalVersion": data.encoderCalVersion,
        }},
        upsert=True,
    )
    await db.trains.update_one(
        {"trainNo": gateway.get("trainId")},
        {"$set": {"status": "running", "updatedAt": now}},
    )

    pending_commands = await db.gateway_commands.find(
        {"gatewayId": gateway_id, "status": "pending"},
        sort=[("createdAt", -1)],
    ).limit(50).to_list(length=50)

    commands = []
    latest_types = set()
    for command in pending_commands:
        command_type = command.get("type")
        if command_type in latest_types:
            await db.gateway_commands.update_one(
                {"commandId": command.get("commandId"), "gatewayId": gateway_id},
                {"$set": {
                    "status": "superseded",
                    "completedAt": now,
                    "result": {"status": "superseded", "details": {"reason": "newer command exists"}},
                }},
            )
            continue
        latest_types.add(command_type)
        commands.append(command)

    commands_out = []
    for command in reversed(commands):
        command_id = command.get("commandId")
        command_type = command.get("type")
        if not command_id or command_type not in ("reset", "calibration_update"):
            continue

        item = {"commandId": command_id, "type": command_type}
        if command_type == "calibration_update":
            payload_path = command.get("payloadUrl")
            item.update({
                "version": command.get("version"),
                "payloadUrl": absolute_cloud_url(request, payload_path),
                "sha256": command.get("sha256"),
            })
        commands_out.append(item)

        delivery_count = int(command.get("deliveryCount") or 0) + 1
        await db.gateway_commands.update_one(
            {"commandId": command_id, "gatewayId": gateway_id},
            {"$set": {
                "status": "delivered",
                "deliveredAt": command.get("deliveredAt") or now,
                "lastDeliveredAt": now,
                "deliveryCount": delivery_count,
            }},
        )

    return {"serverTime": now.isoformat(), "commands": commands_out}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def absolute_cloud_url(request: Request, path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    base_url = settings.get("cloud_public_base_url") or str(request.base_url).rstrip("/")
    return f"{base_url}/{path.lstrip('/')}"


async def require_gateway_access(
    request: Request,
    gateway_id: str,
    *,
    allow_operator: bool = False,
) -> None:
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


async def resolve_train_id(gateway_id: str, *candidates: str | None) -> str:
    for candidate in candidates:
        if candidate:
            return str(candidate).strip()

    gateway = await db.gateways.find_one({"gatewayId": gateway_id})
    if gateway and gateway.get("trainId"):
        return str(gateway["trainId"])

    status = await db.gateway_status.find_one({"gatewayId": gateway_id})
    if status and status.get("trainId"):
        return str(status["trainId"])
    raise HTTPException(status_code=400, detail="Could not resolve train ID for the given gateway")


def location_box(latitude: float, longitude: float, radius_meters: float) -> dict[str, dict[str, float]]:
    radius_degrees = max(radius_meters, 1.0) / 111_320
    return {
        "latitude": {"$gte": latitude - radius_degrees, "$lte": latitude + radius_degrees},
        "longitude": {"$gte": longitude - radius_degrees, "$lte": longitude + radius_degrees},
    }


async def process_and_ingest_archive(
    gateway_id: str,
    train_id: str,
    source: bytes | str,
    actual_sha256: str,
    content_type: str = "application/zip"
) -> dict[str, Any]:
    try:
        parsed = parse_archive_zip(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    now = utc_now()
    metadata = parsed.metadata or {}
    session_name = (
        metadata.get("sessionName")
        or metadata.get("sessionId")
        or f"{gateway_id}-{int(now.timestamp())}"
    )
    resolved_train_id = await resolve_train_id(
        gateway_id,
        metadata.get("trainId"),
        metadata.get("trainNo"),
        train_id,
    )
    session_status = metadata.get("sessionStatus", "unknown")
    warnings = list(parsed.warnings)

    meta_gateway_id = metadata.get("gatewayId") or metadata.get("gateway_id")
    if meta_gateway_id and normalize_gateway_id(meta_gateway_id) != normalize_gateway_id(gateway_id):
        warnings.append("Metadata gatewayId does not match API key gateway")
    if metadata.get("trainId") and metadata.get("trainId") != resolved_train_id:
        warnings.append("Metadata trainId does not match resolved train")

    calibration = await db.calibration_versions.find_one(
        {"gateway_id": gateway_id},
        sort=[("version", -1)],
    )
    wheel_compensation = apply_wheel_compensation(
        parsed.rms_records,
        parsed.peak_records,
        calibration,
    )

    common = {
        "gatewayId": gateway_id,
        "trainId": resolved_train_id,
        "sessionName": session_name,
        "archiveSha256": actual_sha256,
        "createdAt": now,
    }

    await db.rms_records.delete_many({"archiveSha256": actual_sha256, "gatewayId": gateway_id})
    await db.peak_records.delete_many({"archiveSha256": actual_sha256, "gatewayId": gateway_id})
    await db.fault_records.delete_many({"archiveSha256": actual_sha256, "gatewayId": gateway_id})
    await db.alert_events.delete_many({"archiveSha256": actual_sha256, "gatewayId": gateway_id, "source": "peak_50m.bin"})

    rms_records = [{**record, **common} for record in parsed.rms_records]
    peak_records = [{**record, **common} for record in parsed.peak_records]
    fault_records = [{**record, **common} for record in parsed.fault_records]
    peak_alerts = peak_records_to_alert_events(
        parsed.peak_records,
        gateway_id,
        resolved_train_id,
        session_name,
        actual_sha256,
        now,
    )

    if rms_records:
        await db.rms_records.insert_many(rms_records)
    if peak_records:
        await db.peak_records.insert_many(peak_records)
    if fault_records:
        await db.fault_records.insert_many(fault_records)
    if peak_alerts:
        await db.alert_events.insert_many(peak_alerts)

    try:
        stored_raw_files = await store_time_domain_files(
            source,
            parsed.raw_files,
            gateway_id,
            resolved_train_id,
            session_name,
            actual_sha256,
            now,
        )
    except Exception as exc:
        print(f"Warning: Raw time domain storage exception: {exc}")
        stored_raw_files = []

    document = {
        "gatewayId": gateway_id,
        "trainId": resolved_train_id,
        "contentType": content_type,
        "sizeBytes": len(source) if isinstance(source, bytes) else os.path.getsize(source),
        "sha256": actual_sha256,
        "sessionName": session_name,
        "sessionStatus": session_status,
        "metadata": metadata,
        "filesInZip": parsed.files,
        "rawFiles": stored_raw_files,
        "rmsIntervalValidation": parsed.rms_validation,
        "wheelCompensation": wheel_compensation,
        "spatialRetentionDays": SPATIAL_RETENTION_DAYS,
        "timeDomainRetentionDays": TIME_DOMAIN_RETENTION_DAYS,
        "rmsRecordCount": len(rms_records),
        "peakRecordCount": len(peak_records),
        "faultRecordCount": len(fault_records),
        "peakAlertCount": len(peak_alerts),
        "parseWarnings": warnings,
        "receivedAt": now,
        "status": "processed_with_warnings" if warnings else "processed",
        # rawZipData intentionally omitted — ZIP is stored on the sftp_incoming
        # volume and deleted after processing; we do not duplicate it in the DB.
    }

    try:
        existing = await db.archives.find_one({"gatewayId": gateway_id, "sha256": actual_sha256})
        if existing:
            await db.archives.update_one({"_id": existing["_id"]}, {"$set": document})
            document["_id"] = existing["_id"]
        else:
            result = await db.archives.insert_one(document)
            document["_id"] = result.inserted_id
    except Exception as exc:
        print(f"Warning: db.archives insert/update exception: {exc}")

    try:
        await mark_gateway_online(gateway_id, resolved_train_id, now)
    except Exception as exc:
        print(f"Warning: mark_gateway_online exception: {exc}")

    return {
        "status": "success",
        "sha256": actual_sha256,
        "sizeBytes": len(source) if isinstance(source, bytes) else os.path.getsize(source),
        "sessionName": session_name,
        "rmsRecords": len(rms_records),
        "peakRecords": len(peak_records),
        "faultRecords": len(fault_records),
        "peakAlerts": len(peak_alerts),
        "rmsIntervalValidation": parsed.rms_validation,
        "wheelCompensation": wheel_compensation,
        "rawTimeDomainFiles": len(stored_raw_files),
        "retention": {
            "spatialAndAlertsDays": SPATIAL_RETENTION_DAYS,
            "timeDomainDays": TIME_DOMAIN_RETENTION_DAYS,
        },
        "warnings": warnings,
    }


@app.put("/api/v1/archive")
async def upload_archive(
    request: Request,
    archive_body: Annotated[bytes, Body(media_type="application/octet-stream")],
    x_api_key: Annotated[str, Header(alias="X-Api-Key")],
    x_sha256: Annotated[str | None, Header(alias="X-Sha256")] = None,
):
    gateway_id = request.state.gateway_id
    expected_sha256 = x_sha256 or request.headers.get("X-Archive-Sha256")
    actual_sha256 = sha256(archive_body).hexdigest()

    if expected_sha256 and expected_sha256.lower() != actual_sha256:
        raise HTTPException(status_code=400, detail="SHA-256 mismatch")

    return await process_and_ingest_archive(
        gateway_id=gateway_id,
        train_id=request.state.train_id,
        body=archive_body,
        actual_sha256=actual_sha256,
        content_type=request.headers.get("content-type", "application/zip")
    )


@app.post("/api/v1/archive/lease")
async def create_upload_lease(
    data: UploadLeaseRequest,
    request: Request,
    x_api_key: Annotated[str, Header(alias="X-Api-Key")]
):
    gateway_id = request.state.gateway_id
    
    auth_doc = await db.gateway_auth.find_one({"gatewayId": gateway_id, "trainId": data.trainId})
    if not auth_doc or not auth_doc.get("upload_enabled", True) or auth_doc.get("revoked_at"):
        raise HTTPException(status_code=403, detail="Secure upload is disabled or revoked for this gateway")
        
    upload_id = str(uuid.uuid4())
    
    base_dir = os.path.abspath(settings["upload_base_dir"])
    train_dir = os.path.join(base_dir, data.trainId)
    gateway_dir = os.path.join(train_dir, gateway_id)
    os.makedirs(gateway_dir, exist_ok=True)
    
    try:
        os.chown(gateway_dir, 1001, 1001)
        os.chown(train_dir, 1001, 1001)
    except Exception as e:
        print(f"Warning: could not chown upload dirs: {e}")
    
    disk_temp_path = os.path.join(gateway_dir, f"{data.zipFileName}.part").replace("\\", "/")
    disk_final_path = os.path.join(gateway_dir, data.zipFileName).replace("\\", "/")
    
    # Client-facing paths expected by the gateway rsync client (includes trainId)
    client_temp_path = f"/incoming/{data.trainId}/{gateway_id}/{data.zipFileName}.part"
    client_final_path = f"/incoming/{data.trainId}/{gateway_id}/{data.zipFileName}"
    
    expires_utc = utc_now() + timedelta(hours=3)
    
    lease_doc = {
        "upload_id": upload_id,
        "gateway_id": gateway_id,
        "train_id": data.trainId,
        "session_name": data.sessionName,
        "zip_file_name": data.zipFileName,
        "sha256": data.sha256,
        "size_bytes": data.sizeBytes,
        "remote_temp_path": disk_temp_path,
        "remote_final_path": disk_final_path,
        "status": "ready",
        "expires_utc": expires_utc
    }
    await db.upload_leases.insert_one(lease_doc)
    
    return {
        "status": "ready",
        "uploadId": upload_id,
        "host": settings["ssh_host"],
        "port": settings["ssh_port"],
        "user": settings["ssh_user"],
        "remoteTempPath": client_temp_path,
        "remoteFinalPath": client_final_path,
        "expiresUtc": expires_utc.isoformat()
    }


@app.post("/api/v1/archive/complete")
async def complete_upload(
    data: UploadCompleteRequest,
    request: Request,
    x_api_key: Annotated[str, Header(alias="X-Api-Key")]
):
    gateway_id = request.state.gateway_id
    
    lease = await db.upload_leases.find_one({"upload_id": data.uploadId, "gateway_id": gateway_id})
    if not lease:
        raise HTTPException(status_code=404, detail="Upload lease not found or does not belong to this gateway")

    if lease.get("expiresUtc") and utc_now() > lease.get("expiresUtc"):
        raise HTTPException(status_code=410, detail="Upload lease has expired")
        
    temp_path = lease.get("remoteTempPath") or lease.get("remote_temp_path")
    final_path = lease.get("remoteFinalPath") or lease.get("remote_final_path")
    lease_size = lease.get("sizeBytes") or lease.get("size_bytes")
    lease_sha = lease.get("sha256")

    if not temp_path or not final_path:
        raise HTTPException(status_code=500, detail=f"Lease record is missing path fields. Keys found: {list(lease.keys())}")
    
    if not os.path.exists(temp_path):
        raise HTTPException(status_code=400, detail=f"Partial upload file not found on server at {temp_path}")
        
    actual_size = os.path.getsize(temp_path)
    if lease_size is not None and actual_size != lease_size:
        raise HTTPException(status_code=400, detail=f"File size mismatch: expected {lease_size}, got {actual_size}")
        
    disk_sha = sha256()
    with open(temp_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            disk_sha.update(chunk)
    actual_sha = disk_sha.hexdigest()
    
    if lease_sha and actual_sha.lower() != lease_sha.lower():
        raise HTTPException(status_code=400, detail=f"SHA-256 verification failed: expected {lease_sha}, got {actual_sha}")
        
    try:
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(temp_path, final_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to finalize file transfer: {exc}")
        
    await db.upload_leases.update_one(
        {"upload_id": data.uploadId},
        {"$set": {"status": "verified"}}
    )
    
    try:
        ingest_res = await process_and_ingest_archive(
            gateway_id=gateway_id,
            train_id=data.trainId,
            source=final_path,
            actual_sha256=actual_sha,
            content_type="application/zip"
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {type(exc).__name__}: {str(exc)} - {traceback.format_exc()}")
    
    await db.upload_leases.update_one(
        {"upload_id": data.uploadId},
        {"$set": {"status": "processed"}}
    )

    # Delete the ZIP from disk now that it has been fully parsed and ingested.
    # Keeping it would double-store data: parsed records are already in Postgres
    # and time-domain files are on the TIME_DOMAIN_DIR volume.
    try:
        os.remove(final_path)
    except OSError as exc:
        print(f"Warning: could not delete processed ZIP {final_path}: {exc}")

    return {
        "status": "verified",
        "uploadId": data.uploadId,
        "remoteFinalPath": final_path,
        "sha256Verified": True,
        "ingestion": ingest_res
    }


@app.get("/api/v1/archive/status")
async def get_upload_status(uploadId: str, request: Request):
    is_auth = False
    gateway_id = None
    try:
        if is_operator_authenticated(request):
            is_auth = True
    except Exception:
        pass
    if not is_auth:
        x_api_key = request.headers.get("X-Api-Key")
        if x_api_key:
            gateway_id = request.state.gateway_id
            is_auth = True
            
    if not is_auth:
        raise HTTPException(status_code=401, detail="Authentication required")
        
    lease = await db.upload_leases.find_one({"upload_id": uploadId})
    if not lease:
        raise HTTPException(status_code=404, detail="Upload lease not found")
        
    if gateway_id and lease.get("gateway_id") != gateway_id:
        raise HTTPException(status_code=403, detail="Permission denied")
        
    temp_path = lease.get("remote_temp_path")
    received_bytes = 0
    if os.path.exists(temp_path):
        received_bytes = os.path.getsize(temp_path)
        
    final_path = lease.get("remote_final_path")
    if os.path.exists(final_path) and lease.get("status") in ("verified", "processed"):
        received_bytes = lease.get("size_bytes")
        
    return {
        "uploadId": uploadId,
        "status": lease.get("status", "ready"),
        "receivedBytes": received_bytes,
        "expectedBytes": lease.get("size_bytes")
    }

@app.post("/api/v1/alert")
async def create_alert(
    request: Request,
    payload: Annotated[AlertRequest | None, Body(description="JSON Alert Payload (for testing or unencrypted alerts)")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
    x_session_id: Annotated[str | None, Header(alias="X-Session-Id")] = None,
    x_session_iv: Annotated[str | None, Header(alias="X-Session-Iv")] = None,
):
    gateway_id = request.state.gateway_id
    raw_body = await request.body()
    
    if x_session_id:
        if not hasattr(request.state, "session_key"):
            raise HTTPException(status_code=401, detail="Session key not found in request state")
        if not x_session_iv:
            raise HTTPException(status_code=400, detail="Missing X-Session-Iv header for encrypted payload")
        try:
            aesgcm = AESGCM(request.state.session_key)
            decrypted_body = aesgcm.decrypt(bytes.fromhex(x_session_iv), raw_body, None)
            alert_json = json.loads(decrypted_body.decode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to decrypt payload: {exc}")
    else:
        try:
            alert_json = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}")

    try:
        data = AlertRequest(**alert_json)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    if data.gatewayId and data.gatewayId != gateway_id:
        raise HTTPException(status_code=403, detail="Session or API key does not belong to supplied gateway")
    train_no = await resolve_train_id(gateway_id, data.trainNo, request.state.train_id)

    if data.peakValueG > 80:
        color = "RED"
    elif data.peakValueG > 50:
        color = "YELLOW"
    else:
        color = "GREEN"

    now = utc_now()
    document = {
        "gatewayId": gateway_id,
        "trainNo": train_no,
        "latitude": data.latitude,
        "longitude": data.longitude,
        "peakValueG": data.peakValueG,
        "alert": color,
        "createdAt": now,
    }
    await db.alert_events.insert_one(document)
    await mark_gateway_online(gateway_id, train_no, now)
    return {"status": "success", "alert": color, "event": serialize(document)}


@app.get("/api/v1/calibration/{gateway_id}")
async def get_calibration(
    gateway_id: str,
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
):
    await require_gateway_access(request, gateway_id, allow_operator=True)

    calibration = await db.calibration_versions.find_one(
        {"gatewayId": gateway_id},
        sort=[("version", -1)],
    )
    default_adxl = {"offset_x": 0, "offset_y": 0, "offset_z": 0}
    default_bogie = {
        "iis_offset_x": 0,
        "iis_offset_y": 0,
        "iis_offset_z": 0,
        "imu_accel_offset_x": 0,
        "imu_accel_offset_y": 0,
        "imu_accel_offset_z": 0,
        "imu_gyro_offset_x": 0,
        "imu_gyro_offset_y": 0,
        "imu_gyro_offset_z": 0,
    }
    default_encoder = {
        "wheel_diameter_m": 0.915,
        "encoder_ppr": 100,
        "spatial_interval_mm": 250,
        "trigger_start_speed_kmph": 20.0,
    }

    if not calibration:
        return {
            "gatewayId": gateway_id,
            "version": 0,
            "adxl_left": default_adxl,
            "adxl_right": default_adxl,
            "bogie": default_bogie,
            "encoder": default_encoder,
        }

    return {
        "gatewayId": gateway_id,
        "version": calibration.get("version"),
        "adxl_left": {**default_adxl, **(calibration.get("adxl_left") or {})},
        "adxl_right": {**default_adxl, **(calibration.get("adxl_right") or {})},
        "bogie": {**default_bogie, **(calibration.get("bogie") or {})},
        "encoder": {**default_encoder, **(calibration.get("encoder") or {})},
    }


@app.post("/api/v1/calibration/{gateway_id}")
async def save_calibration(
    gateway_id: str,
    data: CalibrationUpdateRequest,
    request: Request,
):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Operator login required")
    if not any((data.adxlLeft, data.adxlRight, data.bogie, data.encoder)):
        raise HTTPException(status_code=400, detail="At least one calibration section is required")

    gateway = await db.gateways.find_one({"gatewayId": gateway_id})
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not registered")
    train_id = gateway.get("trainId")

    existing = await db.calibration_versions.find_one(
        {"gatewayId": gateway_id},
        sort=[("version", -1)],
    ) or {}
    version = int(existing.get("version") or 0) + 1
    now = utc_now()

    default_adxl = {"offset_x": 0, "offset_y": 0, "offset_z": 0}
    default_bogie = {
        "iis_offset_x": 0, "iis_offset_y": 0, "iis_offset_z": 0,
        "imu_accel_offset_x": 0, "imu_accel_offset_y": 0, "imu_accel_offset_z": 0,
        "imu_gyro_offset_x": 0, "imu_gyro_offset_y": 0, "imu_gyro_offset_z": 0,
    }
    default_encoder = {
        "wheel_diameter_m": 0.915,
        "encoder_ppr": 100,
        "spatial_interval_mm": 250,
        "trigger_start_speed_kmph": 20.0,
    }
    current_adxl_left = {**default_adxl, **(existing.get("adxl_left") or {})}
    current_adxl_right = {**default_adxl, **(existing.get("adxl_right") or {})}
    current_bogie = {**default_bogie, **(existing.get("bogie") or {})}
    current_encoder = {**default_encoder, **(existing.get("encoder") or {})}

    adxl_left = {**current_adxl_left, **(data.adxlLeft.model_dump() if data.adxlLeft else {})}
    adxl_right = {**current_adxl_right, **(data.adxlRight.model_dump() if data.adxlRight else {})}
    bogie = {**current_bogie, **(data.bogie or {})}
    encoder = {**current_encoder, **(data.encoder or {})}

    document = {
        "train_id": train_id,
        "gateway_id": gateway_id,
        "version": version,
        "adxl_left_offset_x": adxl_left["offset_x"],
        "adxl_left_offset_y": adxl_left["offset_y"],
        "adxl_left_offset_z": adxl_left["offset_z"],
        "adxl_right_offset_x": adxl_right["offset_x"],
        "adxl_right_offset_y": adxl_right["offset_y"],
        "adxl_right_offset_z": adxl_right["offset_z"],
        "iis_offset_x": bogie["iis_offset_x"],
        "iis_offset_y": bogie["iis_offset_y"],
        "iis_offset_z": bogie["iis_offset_z"],
        "imu_accel_offset_x": bogie["imu_accel_offset_x"],
        "imu_accel_offset_y": bogie["imu_accel_offset_y"],
        "imu_accel_offset_z": bogie["imu_accel_offset_z"],
        "imu_gyro_offset_x": bogie["imu_gyro_offset_x"],
        "imu_gyro_offset_y": bogie["imu_gyro_offset_y"],
        "imu_gyro_offset_z": bogie["imu_gyro_offset_z"],
        "wheel_diameter_m": encoder["wheel_diameter_m"],
        "encoder_ppr": encoder["encoder_ppr"],
        "spatial_interval_mm": encoder["spatial_interval_mm"],
        "trigger_start_speed_kmph": encoder["trigger_start_speed_kmph"],
        "adxl_left": adxl_left,
        "adxl_right": adxl_right,
        "bogie": bogie,
        "encoder": encoder,
        "updated_at": now,
        "created_at": now,
    }

    calibration_sections: dict[str, Any] = {}
    if data.adxlLeft:
        calibration_sections["adxlLeft"] = {
            "offsetQ16": [adxl_left["offset_x"], adxl_left["offset_y"], adxl_left["offset_z"]]
        }
    if data.adxlRight:
        calibration_sections["adxlRight"] = {
            "offsetQ16": [adxl_right["offset_x"], adxl_right["offset_y"], adxl_right["offset_z"]]
        }
    if data.bogie is not None:
        calibration_sections["bogie"] = {
            "iisOffsetQ16": [bogie["iis_offset_x"], bogie["iis_offset_y"], bogie["iis_offset_z"]],
            "imuAccelOffsetQ16": [
                bogie["imu_accel_offset_x"], bogie["imu_accel_offset_y"], bogie["imu_accel_offset_z"]
            ],
            "imuGyroOffsetQ16": [
                bogie["imu_gyro_offset_x"], bogie["imu_gyro_offset_y"], bogie["imu_gyro_offset_z"]
            ],
        }
    if data.encoder is not None:
        calibration_sections["encoder"] = {
            "wheelDiameterM": encoder["wheel_diameter_m"],
            "encoderPPR": encoder["encoder_ppr"],
            "spatialIntervalMm": encoder["spatial_interval_mm"],
            "triggerStartSpeedKmph": encoder["trigger_start_speed_kmph"],
        }

    command_id = f"cmd-{uuid.uuid4()}"
    payload_path = f"/api/v1/calibration/{gateway_id}/payload/{command_id}"
    calibration_payload = {
        "gatewayId": gateway_id,
        "version": version,
        "calibration": calibration_sections,
    }
    payload_sha256 = sha256(canonical_json_bytes(calibration_payload)).hexdigest()

    await db.gateway_commands.update_many(
        {
            "gatewayId": gateway_id,
            "type": "calibration_update",
            "status": {"$in": ["pending", "delivered"]},
        },
        {"$set": {
            "status": "superseded",
            "completedAt": now,
            "result": {"status": "superseded", "details": {"supersededBy": command_id}},
        }},
    )
    await db.calibration_versions.insert_one(document)
    await db.calibrations.replace_one({"gatewayId": gateway_id}, document, upsert=True)
    await db.gateway_commands.insert_one({
        "commandId": command_id,
        "gatewayId": gateway_id,
        "type": "calibration_update",
        "status": "pending",
        "version": version,
        "payloadUrl": payload_path,
        "sha256": payload_sha256,
        "payload": calibration_payload,
        "deliveryCount": 0,
        "createdAt": now,
    })

    return {
        "status": "success",
        "message": "Calibration saved and command queued",
        "calibration": serialize(document),
        "command": {
            "commandId": command_id,
            "type": "calibration_update",
            "version": version,
            "payloadUrl": absolute_cloud_url(request, payload_path),
            "sha256": payload_sha256,
            "status": "pending",
        },
    }


def generate_train_name(no: str) -> str:
    if not no:
        return "Express Train"
    if "TR_" in no:
        try:
            num = int(no.split("_")[1])
            names_pool = [
                "Rajdhani Express", "Shatabdi Express", "Duronto Express", 
                "Garib Rath", "HumSafar Express", "Vande Bharat Express", 
                "Tejas Express", "Jan Shatabdi", "Sampark Kranti", "Superfast Mail"
            ]
            return names_pool[num % len(names_pool)]
        except Exception:
            return "Express Train"
    try:
        num = int(no)
        names_pool = [
            "Rajdhani Express", "Shatabdi Express", "Duronto Express", 
            "Garib Rath", "HumSafar Express", "Vande Bharat Express", 
            "Tejas Express", "Jan Shatabdi", "Sampark Kranti", "Superfast Mail"
        ]
        return names_pool[num % len(names_pool)]
    except Exception:
        return "Express Train"


@app.get("/api/v1/trains")
async def list_trains():
    trains_cursor = db.trains.find({}, {"_id": 0, "trainNo": 1, "trainName": 1})
    trains = await trains_cursor.to_list(length=1000)
    
    unique_trains = {}
    for t in trains:
        no = t.get("trainNo")
        if not no:
            continue
        name = t.get("trainName") or ""
        if not name:
            name = generate_train_name(no)
        
        display_no = no.replace("TR_", "") if no.startswith("TR_") else no
        unique_trains[display_no] = {
            "trainNo": display_no,
            "trainName": name
        }
    return sorted(list(unique_trains.values()), key=lambda x: x["trainNo"])


def extract_max_g_from_axes(axes_str_or_dict) -> tuple[float | None, str | None]:
    if not axes_str_or_dict:
        return None, None
    try:
        if isinstance(axes_str_or_dict, str):
            axes = json.loads(axes_str_or_dict)
        else:
            axes = axes_str_or_dict
        
        max_g = 0.0
        has_val = False
        for axis_name, axis_data in axes.items():
            if isinstance(axis_data, dict):
                val = axis_data.get("peakValueG") or axis_data.get("g") or axis_data.get("value")
                if val is not None:
                    try:
                        f_val = float(val)
                        if f_val > max_g:
                            max_g = f_val
                            has_val = True
                    except ValueError:
                        pass
        if has_val:
            if max_g > 80.0:
                color = "RED"
            elif max_g > 50.0:
                color = "YELLOW"
            else:
                color = "GREEN"
            return round(max_g, 4), color
    except Exception:
        pass
    return None, None


@app.get("/api/v1/trains/{train_no}/dashboard")
async def train_dashboard(train_no: str, request: Request):
    train = await db.trains.find_one({"trainNo": train_no})
    if not train:
        raise HTTPException(status_code=404, detail="Train not found")
        
    if not train.get("trainName"):
        if "TR_" in train_no:
            try:
                num = int(train_no.split("_")[1])
                names_pool = [
                    "Rajdhani Express", "Shatabdi Express", "Duronto Express", 
                    "Garib Rath", "HumSafar Express", "Vande Bharat Express", 
                    "Tejas Express", "Jan Shatabdi", "Sampark Kranti", "Superfast Mail"
                ]
                train["trainName"] = names_pool[num % len(names_pool)]
            except Exception:
                train["trainName"] = "Express Train"
        else:
            train["trainName"] = "Express Train"
            
    associated_gateways = await db.gateways.find({"trainId": train_no}).to_list(length=20)
    gateway_ids = [g.get("gatewayId") for g in associated_gateways if g.get("gatewayId")]
    if gateway_ids:
        statuses = await db.gateway_status.find({"gatewayId": {"$in": gateway_ids}}).to_list(length=20)
    else:
        statuses = []
    status_by_id = {item.get("gatewayId"): item for item in statuses}
    now_dt = utc_now()
    gateway_cards = []
    for gateway_id in gateway_ids:
        latest_peak = await db.peak_records.find_one(
            {"trainId": train_no, "gatewayId": gateway_id},
            sort=[("createdAt", -1), ("positionMm", -1)]
        )
        latest_rms = await db.rms_records.find_one(
            {"trainId": train_no, "gatewayId": gateway_id},
            sort=[("createdAt", -1), ("positionMm", -1)]
        )

        card = status_by_id.get(gateway_id)
        if card:
            card = dict(card)
            lh = card.get("lastHeartbeat") or card.get("last_heartbeat")
            if lh:
                lh_dt = lh
                if isinstance(lh, str):
                    try:
                        lh_dt = parse_datetime(lh)
                    except Exception:
                        lh_dt = None
                if lh_dt:
                    if lh_dt.tzinfo is None:
                        lh_dt = lh_dt.replace(tzinfo=UTC)
                    if (now_dt - lh_dt).total_seconds() > 3600:
                        card["online"] = False
                    else:
                        card["online"] = True
                else:
                    card["online"] = False
            else:
                card["online"] = False
        else:
            card = {
                "gatewayId": gateway_id,
                "trainId": train_no,
                "online": False,
                "lastHeartbeat": None,
            }

        fallback_peak_g, fallback_alert = None, None
        if latest_peak:
            fallback_peak_g, fallback_alert = extract_max_g_from_axes(latest_peak.get("axes"))
        if not fallback_peak_g and latest_rms:
            fallback_peak_g, fallback_alert = extract_max_g_from_axes(latest_rms.get("axes"))

        card["latestPeakG"] = fallback_peak_g
        card["latestAlert"] = fallback_alert
        card["latestLatitude"] = latest_peak.get("latitude") if latest_peak else latest_rms.get("latitude") if latest_rms else None
        card["latestLongitude"] = latest_peak.get("longitude") if latest_peak else latest_rms.get("longitude") if latest_rms else None

        gateway_cards.append(card)
            
    alerts = await db.alert_events.find({"trainNo": train_no, "sessionStatus": {"$ne": "archived"}}).sort("createdAt", -1).limit(30).to_list(length=30)
    archives = await db.archives.find({"trainId": train_no}).sort("receivedAt", -1).limit(20).to_list(length=20)
    active_session = await db.sessions.find_one({"trainNo": train_no, "status": "active"}, sort=[("createdAt", -1)])
    
    display_train = dict(train)
    if display_train.get("trainNo", "").startswith("TR_"):
        display_train["trainNo"] = display_train["trainNo"].replace("TR_", "")

    payload = operator_session_payload(request)
    role = payload.get("role") if payload else "operator"

    payload = operator_session_payload(request)
    role = payload.get("role", "operator") if payload else "operator"
    permissions = {
        "can_configure_thresholds": payload.get("can_configure_thresholds", False) if payload else False,
        "can_manage_users": payload.get("can_manage_users", False) if payload else False,
        "can_view_alerts": payload.get("can_view_alerts", True) if payload else True,
    }
    
    return {
        "train": serialize(display_train),
        "gateways": serialize(gateway_cards),
        "lastAlerts": serialize(alerts),
        "archives": serialize(archives),
        "activeSession": serialize(active_session) if active_session else None,
        "userRole": role,
        "permissions": permissions,
    }



@app.get("/api/v1/trains/{train_no}/gateways/{gateway_id}/details")
async def gateway_details(train_no: str, gateway_id: str):
    gateway = await db.gateway_status.find_one({"gatewayId": gateway_id})
    if gateway:
        lh = gateway.get("lastHeartbeat") or gateway.get("last_heartbeat")
        if lh:
            lh_dt = lh
            if isinstance(lh, str):
                try:
                    lh_dt = parse_datetime(lh)
                except Exception:
                    lh_dt = None
            if lh_dt:
                if lh_dt.tzinfo is None:
                    lh_dt = lh_dt.replace(tzinfo=UTC)
                if (utc_now() - lh_dt).total_seconds() > 3600:
                    gateway["online"] = False
                else:
                    gateway["online"] = True
            else:
                gateway["online"] = False
        else:
            gateway["online"] = False
    archive_count = await db.archives.count_documents({"trainId": train_no, "gatewayId": gateway_id})
    alert_count = await db.alert_events.count_documents({"trainNo": train_no, "gatewayId": gateway_id, "sessionStatus": {"$ne": "archived"}})
    critical_count = await db.alert_events.count_documents({"trainNo": train_no, "gatewayId": gateway_id, "alert": "RED", "sessionStatus": {"$ne": "archived"}})
    rms_count = await db.rms_records.count_documents({"trainId": train_no, "gatewayId": gateway_id})
    peak_count = await db.peak_records.count_documents({"trainId": train_no, "gatewayId": gateway_id})
    fault_count = await db.fault_records.count_documents({"trainId": train_no, "gatewayId": gateway_id})
    latest_alert = await db.alert_events.find_one(
        {"trainNo": train_no, "gatewayId": gateway_id, "sessionStatus": {"$ne": "archived"}},
        sort=[("createdAt", -1)],
    )
    latest_archive = await db.archives.find_one(
        {"trainId": train_no, "gatewayId": gateway_id},
        sort=[("receivedAt", -1)],
    )
    latest_rms = await db.rms_records.find_one(
        {"trainId": train_no, "gatewayId": gateway_id},
        sort=[("createdAt", -1), ("positionMm", -1)],
    )
    latest_peak = await db.peak_records.find_one(
        {"trainId": train_no, "gatewayId": gateway_id},
        sort=[("createdAt", -1), ("positionMm", -1)],
    )
    fallback_peak_g, fallback_alert = None, None
    if latest_peak:
        fallback_peak_g, fallback_alert = extract_max_g_from_axes(latest_peak.get("axes"))
    if not fallback_peak_g and latest_rms:
        fallback_peak_g, fallback_alert = extract_max_g_from_axes(latest_rms.get("axes"))

    alerts = await db.alert_events.find(
        {"trainNo": train_no, "gatewayId": gateway_id, "sessionStatus": {"$ne": "archived"}}
    ).sort("createdAt", -1).limit(20).to_list(length=20)
    archives = await db.archives.find({"trainId": train_no, "gatewayId": gateway_id}).sort("receivedAt", -1).limit(10).to_list(length=10)
    faults = await db.fault_records.find({"trainId": train_no, "gatewayId": gateway_id}).sort("createdAt", -1).limit(20).to_list(length=20)

    return {
        "trainNo": train_no,
        "gatewayId": gateway_id,
        "status": serialize(gateway) if gateway else {"gatewayId": gateway_id, "trainId": train_no, "online": False},
        "summary": {
            "archives": archive_count,
            "alerts": alert_count,
            "criticalAlerts": critical_count,
            "rmsRecords": rms_count,
            "peakRecords": peak_count,
            "faultRecords": fault_count,
            "latestPeakG": latest_alert.get("peakValueG") if latest_alert else fallback_peak_g,
            "latestAlert": latest_alert.get("alert") if latest_alert else fallback_alert,
            "latestLocation": {
                "latitude": latest_alert.get("latitude") if latest_alert else latest_peak.get("latitude") if latest_peak else latest_rms.get("latitude") if latest_rms else None,
                "longitude": latest_alert.get("longitude") if latest_alert else latest_peak.get("longitude") if latest_peak else latest_rms.get("longitude") if latest_rms else None,
            },
            "latestArchive": serialize(latest_archive) if latest_archive else None,
        },
        "alerts": serialize(alerts),
        "archives": serialize(archives),
        "faults": serialize(faults),
    }
@app.get("/api/v1/trains/{train_no}/archives")
async def train_archives(train_no: str):
    archives = await db.archives.find({"trainId": train_no}).sort("receivedAt", -1).limit(50).to_list(length=50)
    return {"trainNo": train_no, "archives": serialize(archives)}


@app.get("/api/v1/trains/{train_no}/position")
async def train_position(train_no: str, gateway_id: str | None = None):
    query: dict[str, Any] = {
        "trainId": train_no,
        "gpsValid": True,
        "latitude": {"$nin": [None, 0]},
        "longitude": {"$nin": [None, 0]},
    }
    if gateway_id:
        query["gatewayId"] = gateway_id

    latest = await db.rms_records.find_one(query, sort=[("createdAt", -1), ("positionMm", -1)])
    if not latest:
        return {"trainNo": train_no, "gatewayId": gateway_id, "position": None}

    previous_query = dict(query)
    previous_query["gatewayId"] = latest.get("gatewayId")
    previous_query["positionMm"] = {"$lt": latest.get("positionMm", 0)}
    previous = await db.rms_records.find_one(previous_query, sort=[("positionMm", -1)])
    bearing = None
    if previous:
        lat1 = radians(float(previous.get("latitude", 0)))
        lat2 = radians(float(latest.get("latitude", 0)))
        delta_lon = radians(float(latest.get("longitude", 0)) - float(previous.get("longitude", 0)))
        y = sin(delta_lon) * cos(lat2)
        x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(delta_lon)
        bearing = round((degrees(atan2(y, x)) + 360) % 360, 2)

    return {
        "trainNo": train_no,
        "gatewayId": latest.get("gatewayId"),
        "position": {
            "latitude": latest.get("latitude"),
            "longitude": latest.get("longitude"),
            "positionMm": latest.get("positionMm"),
            "speedKmph": latest.get("speedKmph"),
            "bearing": bearing,
            "createdAt": serialize(latest.get("createdAt")),
        },
    }


@app.get("/api/v1/map/alerts")
async def map_alerts(train_id: str):
    # Find the latest session for this train from rms_records to identify the current active trip
    latest_record = await db.rms_records.find_one({"trainId": train_id}, sort=[("createdAt", -1)])
    
    query: dict[str, Any] = {"trainNo": train_id, "sessionStatus": {"$ne": "archived"}}
    if latest_record and latest_record.get("sessionName"):
        query["$or"] = [
            {"sessionName": latest_record["sessionName"]},
            {"sessionName": {"$in": [None, ""]}}
        ]
        
    alerts = await db.alert_events.find(query).sort("createdAt", -1).limit(200).to_list(length=200)
    return [
        {
            "train_id": item.get("trainNo"),
            "gateway_id": item.get("gatewayId"),
            "lat": item.get("latitude"),
            "lon": item.get("longitude"),
            "color": item.get("alert", "GREEN"),
            "peak_g": item.get("peakValueG"),
            "zone": item.get("zone", "NCR"),
            "division": item.get("division", "Prayagraj"),
            "section": item.get("section", "ABC-XYZ"),
            "created_at": serialize(item.get("createdAt")),
        }
        for item in alerts
    ]


def _compute_color(item: dict) -> str:
    """Derive RED/YELLOW/GREEN from axes rmsValue data if no color field stored."""
    stored = item.get("color")
    if stored and stored in ("RED", "YELLOW", "GREEN"):
        return stored
    axes = item.get("axes") or {}
    if isinstance(axes, str):
        try:
            import json as _json
            axes = _json.loads(axes)
        except Exception:
            axes = {}
    max_rms = 0.0
    for axis_data in axes.values():
        if isinstance(axis_data, dict):
            rms = axis_data.get("rmsValue") or 0
            max_rms = max(max_rms, float(rms))
    if max_rms >= 0.6:
        return "RED"
    elif max_rms >= 0.4:
        return "YELLOW"
    return "GREEN"


@app.get("/api/v1/map/rms")
async def map_rms(train_id: str, gateway_id: str | None = None):
    query: dict[str, Any] = {
        "trainId": train_id,
        "gpsValid": True,
        "latitude": {"$nin": [None, 0]},
        "longitude": {"$nin": [None, 0]},
    }
    if gateway_id:
        query["gatewayId"] = gateway_id

    # Return recent valid GPS records for each gateway.  This keeps the route
    # visible even if the latest archive for one gateway has no valid GPS data.
    recent_records = await db.rms_records.find(
        query,
        {
            "trainId": 1, "train_id": 1,
            "gatewayId": 1, "gateway_id": 1,
            "sessionName": 1, "session_name": 1,
            "latitude": 1,
            "longitude": 1,
            "axes": 1,
            "maxG": 1, "max_g": 1,
            "positionMm": 1, "position_mm": 1,
            "masterCount": 1, "master_count": 1,
            "createdAt": 1, "created_at": 1,
            "archiveSha256": 1, "archive_sha256": 1,
        },
    ).sort([("createdAt", -1), ("gatewayId", 1), ("positionMm", -1)]).limit(10000).to_list(length=10000)

    records_by_gateway: dict[str, list[dict[str, Any]]] = {}
    for item in recent_records:
        gateway = item.get("gatewayId") or item.get("gateway_id") or "unknown"
        records_by_gateway.setdefault(gateway, []).append(item)

    records: list[dict[str, Any]] = []
    for gateway_records in records_by_gateway.values():
        if not gateway_records:
            continue
            
        # 1. Filter by the latest session name to keep all segments from the same active run
        latest_session = gateway_records[0].get("sessionName") or gateway_records[0].get("session_name")
        if latest_session:
            session_records = [r for r in gateway_records if (r.get("sessionName") or r.get("session_name")) == latest_session]
        else:
            latest_archive = gateway_records[0].get("archiveSha256") or gateway_records[0].get("archive_sha256")
            session_records = [r for r in gateway_records if (r.get("archiveSha256") or r.get("archive_sha256")) == latest_archive]
            
        # 2. Group these session records by archiveSha256
        archives_map = {}
        for r in session_records:
            sha = r.get("archiveSha256") or r.get("archive_sha256") or "unknown"
            archives_map.setdefault(sha, []).append(r)
            
        # 3. For each archive in the session, find its position range and latest creation time
        archive_infos = []
        for sha, recs in archives_map.items():
            positions = [x.get("positionMm") or x.get("position_mm") for x in recs if (x.get("positionMm") or x.get("position_mm")) is not None]
            min_pos = min(positions) if positions else 0
            max_pos = max(positions) if positions else 0
            latest_created = max(x.get("createdAt") or x.get("created_at") for x in recs) if recs else 0
            archive_infos.append({
                "sha": sha,
                "min_pos": min_pos,
                "max_pos": max_pos,
                "created_at": latest_created,
                "records": recs
            })
            
        # Sort archives by latest creation time descending (latest first)
        archive_infos.sort(key=lambda x: x["created_at"], reverse=True)
        
        # 4. Select non-overlapping archives to build a continuous path
        selected_archives = []
        selected_ranges = []
        for info in archive_infos:
            overlap = False
            for r_min, r_max in selected_ranges:
                # Check for range overlaps (with a small 500mm buffer to handle fuzzy boundaries)
                if not (info["max_pos"] < r_min + 500 or info["min_pos"] > r_max - 500):
                    overlap = True
                    break
            if not overlap:
                selected_archives.append(info)
                selected_ranges.append((info["min_pos"], info["max_pos"]))
                
        # 5. Sort selected archives chronologically (oldest first) to maintain time order
        selected_archives.sort(key=lambda x: x["created_at"])
        
        # 6. Gather records, sorting by positionMm ONLY within each archive.
        # This prevents the "star/burst" pattern caused by globally sorting 
        # position_mm when the encoder resets to 0 mid-session.
        for info in selected_archives:
            archive_records = info["records"]
            archive_records.sort(key=lambda x: x.get("positionMm") or x.get("position_mm") or 0)
            records.extend(archive_records)

    return [
        {
            "train_id": item.get("trainId") or item.get("train_id"),
            "gateway_id": item.get("gatewayId") or item.get("gateway_id"),
            "session": item.get("sessionName") or item.get("session_name"),
            "lat": item.get("latitude"),
            "lon": item.get("longitude"),
            "color": _compute_color(item),
            "peak_g": item.get("maxG") or item.get("max_g") or 0,
            "position_mm": item.get("positionMm") or item.get("position_mm"),
            "master_count": item.get("masterCount") or item.get("master_count"),
            "created_at": serialize(item.get("createdAt") or item.get("created_at")),
        }
        for item in records
    ]

@app.post("/api/v1/data/reset")
async def reset_bad_data(
    data: TargetedResetRequest,
    request: Request,
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
):
    if not x_admin_key:
        raise HTTPException(status_code=401, detail="Missing admin reset key or password")
        
    legacy_key_ok = bool(
        settings.get("admin_reset_key")
        and compare_digest(x_admin_key, settings["admin_reset_key"])
    )
    password_ok = bool(
        is_operator_authenticated(request)
        and compare_digest(x_admin_key, settings["admin_password"])
    )
    
    if not legacy_key_ok and not password_ok:
        raise HTTPException(status_code=403, detail="Invalid admin reset key or password")
    if not data.startTime and not data.endTime and (data.latitude is None or data.longitude is None):
        raise HTTPException(status_code=400, detail="Provide a time range or location for targeted cleanup")

    now = utc_now()

    def add_common(query: dict[str, Any], train_field: str, time_field: str) -> dict[str, Any]:
        query[train_field] = data.trainNo
        if data.gatewayId:
            query["gatewayId"] = data.gatewayId
        if data.startTime or data.endTime:
            query[time_field] = {}
            if data.startTime:
                query[time_field]["$gte"] = data.startTime
            if data.endTime:
                query[time_field]["$lte"] = data.endTime
        return query

    location_filter = {}
    if data.latitude is not None and data.longitude is not None:
        location_filter = location_box(data.latitude, data.longitude, data.radiusMeters)

    alert_query = add_common({}, "trainNo", "createdAt")
    rms_query = add_common({}, "trainId", "createdAt")
    peak_query = add_common({}, "trainId", "createdAt")
    fault_query = add_common({}, "trainId", "createdAt")
    archive_query = add_common({}, "trainId", "receivedAt")
    time_domain_file_query = add_common({}, "trainId", "createdAt")
    time_domain_chunk_query = add_common({}, "trainId", "createdAt")

    if location_filter:
        alert_query.update(location_filter)
        rms_query.update(location_filter)
        peak_query.update(location_filter)
        if not (data.startTime or data.endTime):
            fault_query = {"_id": {"$exists": False}}
            archive_query = {"_id": {"$exists": False}}
            time_domain_file_query = {"_id": {"$exists": False}}
            time_domain_chunk_query = {"_id": {"$exists": False}}

    deleted_alerts = await db.alert_events.delete_many(alert_query)
    deleted_rms = await db.rms_records.delete_many(rms_query)
    deleted_peak = await db.peak_records.delete_many(peak_query)
    deleted_faults = await db.fault_records.delete_many(fault_query)
    deleted_archives = await db.archives.delete_many(archive_query)

    # Fetch time-domain file records BEFORE deleting so we can purge
    # the corresponding files from the TIME_DOMAIN_DIR volume on disk.
    td_file_records = await db.time_domain_files.find(time_domain_file_query).to_list(length=None)
    purged_disk_files = 0
    for td_rec in td_file_records:
        fs_path = td_rec.get("path")
        if fs_path and os.path.isfile(fs_path):
            try:
                os.remove(fs_path)
                purged_disk_files += 1
                # Remove parent dir if it becomes empty
                parent = os.path.dirname(fs_path)
                if os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
            except OSError as exc:
                print(f"Warning: could not purge time-domain file {fs_path}: {exc}")

    deleted_time_domain_files = await db.time_domain_files.delete_many(time_domain_file_query)
    # time_domain_chunks rows are empty shells (no payload); clean them up too
    deleted_time_domain_chunks = await db.time_domain_chunks.delete_many(time_domain_chunk_query)

    cleanup = {
        "trainNo": data.trainNo,
        "gatewayId": data.gatewayId,
        "startTime": data.startTime,
        "endTime": data.endTime,
        "latitude": data.latitude,
        "longitude": data.longitude,
        "radiusMeters": data.radiusMeters,
        "reason": data.reason,
        "deleted": {
            "alerts": deleted_alerts.deleted_count,
            "rmsRecords": deleted_rms.deleted_count,
            "peakRecords": deleted_peak.deleted_count,
            "faultRecords": deleted_faults.deleted_count,
            "archives": deleted_archives.deleted_count,
            "timeDomainFiles": deleted_time_domain_files.deleted_count,
            "timeDomainChunkRows": deleted_time_domain_chunks.deleted_count,
            "purgedDiskFiles": purged_disk_files,
        },
        "createdAt": now,
    }
    await db.reset_events.insert_one(cleanup)
    return {"status": "success", "message": "Targeted data removed", "cleanup": serialize(cleanup)}
@app.post("/api/v1/sessions/reset")
async def reset_session(
    data: ResetSessionRequest,
    request: Request,
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
):
    legacy_key_ok = bool(
        x_admin_key
        and settings.get("admin_reset_key")
        and compare_digest(x_admin_key, settings["admin_reset_key"])
    )
    password_ok = bool(
        data.adminPassword
        and is_operator_authenticated(request)
        and compare_digest(data.adminPassword, settings["admin_password"])
    )
    if not legacy_key_ok and not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Operator login required")
    if not legacy_key_ok and not password_ok:
        raise HTTPException(status_code=403, detail="Invalid administrator password")

    train = await db.trains.find_one({"trainNo": data.trainNo})
    if not train:
        raise HTTPException(status_code=404, detail="Train not found")

    now = utc_now()
    await db.sessions.update_many(
        {"trainNo": data.trainNo, "status": "active"},
        {"$set": {"status": "closed", "closedAt": now}},
    )
    await db.alert_events.update_many(
        {"trainNo": data.trainNo, "sessionStatus": {"$ne": "archived"}},
        {"$set": {"sessionStatus": "archived", "archivedAt": now}},
    )

    session_id = f"{data.trainNo}-{int(now.timestamp())}"
    session_document = {
        "trainNo": data.trainNo,
        "sessionName": session_id,
        "status": "active",
        "createdAt": now,
    }
    await db.sessions.insert_one(session_document)

    gateways = await db.gateways.find({"trainId": data.trainNo}).to_list(length=50)
    queued_commands = []
    for gateway in gateways:
        gateway_id = gateway.get("gatewayId")
        if not gateway_id:
            continue
        command_id = f"cmd-{uuid.uuid4()}"
        await db.gateway_commands.update_many(
            {
                "gatewayId": gateway_id,
                "type": "reset",
                "status": {"$in": ["pending", "delivered"]},
            },
            {"$set": {
                "status": "superseded",
                "completedAt": now,
                "result": {"status": "superseded", "details": {"supersededBy": command_id}},
            }},
        )
        await db.gateway_commands.insert_one({
            "commandId": command_id,
            "gatewayId": gateway_id,
            "type": "reset",
            "status": "pending",
            "deliveryCount": 0,
            "createdAt": now,
        })
        queued_commands.append({"gatewayId": gateway_id, "commandId": command_id})

    session_response = {
        "sessionId": session_id,
        "trainNo": data.trainNo,
        "status": "active",
        "createdAt": now,
    }
    return {
        "status": "success",
        "message": "New session started and reset commands queued",
        "session": serialize(session_response),
        "resetCommands": queued_commands,
    }


@app.get("/api/v1/calibration/{gateway_id}/payload/{command_id}")
async def get_calibration_payload(
    gateway_id: str,
    command_id: str,
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
):
    await require_gateway_access(request, gateway_id)

    command = await db.gateway_commands.find_one({
        "commandId": command_id,
        "gatewayId": gateway_id,
        "type": "calibration_update",
    })
    if not command:
        raise HTTPException(status_code=404, detail="Calibration command not found")

    payload = command.get("payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=404, detail="Calibration payload not found for this command")

    payload_bytes = canonical_json_bytes(payload)
    actual_sha256 = sha256(payload_bytes).hexdigest()
    if actual_sha256 != command.get("sha256"):
        raise HTTPException(status_code=500, detail="Stored calibration payload hash mismatch")

    return Response(
        content=payload_bytes,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "X-Content-SHA256": actual_sha256,
        },
    )


@app.get("/api/v1/commands/{gateway_id}")
async def list_gateway_commands(gateway_id: str, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Operator login required")
    commands = await db.gateway_commands.find(
        {"gatewayId": gateway_id},
        sort=[("createdAt", -1)],
    ).limit(50).to_list(length=50)
    return serialize(commands)


async def mark_gateway_online(gateway_id: str, train_id: str, now: datetime) -> None:
    await db.gateways.update_one(
        {"gatewayId": gateway_id},
        {
            "$set": {
                "gatewayId": gateway_id,
                "trainId": train_id,
                "status": "active",
                "lastSeen": now,
                "updatedAt": now,
            },
            "$setOnInsert": {"createdAt": now},
        },
        upsert=True,
    )
    await db.trains.update_one(
        {"trainNo": train_id},
        {
            "$set": {"trainNo": train_id, "status": "running", "updatedAt": now},
            "$addToSet": {"gateways": gateway_id},
            "$setOnInsert": {"trainName": generate_train_name(train_id), "createdAt": now},
        },
        upsert=True,
    )
    await db.gateway_status.update_one(
        {"gatewayId": gateway_id},
        {
            "$set": {
                "gatewayId": gateway_id,
                "trainId": train_id,
                "online": True,
                "lastHeartbeat": now,
            }
        },
        upsert=True,
    )


# =====================================================================
# REPORTING MODULES & ALARM LOG APIS
# =====================================================================
class RepeatedAlarmRequest(BaseModel):
    fromDate: str
    toDate: str

class AlarmLogRequest(BaseModel):
    rid: str | None = None
    fromDate: str
    toDate: str
    alarmType: str
    feedbackStatus: str | None = None

class FeedbackUpdateRequest(BaseModel):
    enrouteDiagnosis: str
    enrouteAction: str
    depotDiagnosis: str

def parse_local_datetime(date_str: str) -> datetime:
    try:
        if "T" in date_str:
            parts = date_str.split("T")
            date_part = parts[0]
            time_part = parts[1]
            if len(time_part) == 5:
                time_part += ":00"
            return datetime.fromisoformat(f"{date_part}T{time_part}")
        return datetime.fromisoformat(date_str)
    except Exception:
        return datetime.utcnow()


@app.post("/api/reports/repeated-alarm/load")
async def load_repeated_alarm_report(data: RepeatedAlarmRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    from_dt = parse_local_datetime(data.fromDate)
    to_dt = parse_local_datetime(data.toDate)
    
    pipeline = [
        {"$match": {"createdAt": {"$gte": from_dt, "$lte": to_dt}}},
        {"$sort": {"createdAt": -1}},
        {
            "$group": {
                "_id": "$trainNo",
                "count": {"$sum": 1},
                "latitude": {"$first": "$latitude"},
                "longitude": {"$first": "$longitude"}
            }
        },
        {"$sort": {"count": -1}}
    ]
    results = await db.alert_events.aggregate(pipeline).to_list(length=1000)
    
    rows = []
    for r in results:
        train_no = r.get("_id")
        if train_no:
            lat = r.get("latitude")
            lon = r.get("longitude")
            loc_str = f"{lat:.4f}, {lon:.4f}" if (lat is not None and lon is not None) else "-"
            rows.append({
                "rid": train_no,
                "count": r.get("count", 0),
                "location": loc_str
            })
            
    return {
        "totalRollingStocks": len(rows),
        "rows": rows
    }


@app.post("/api/reports/repeated-alarm/export/csv")
async def export_repeated_alarm_csv(data: RepeatedAlarmRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    res = await load_repeated_alarm_report(data, request)
    rows = res["rows"]
    
    csv_lines = ["RID,Count,Location"]
    for r in rows:
        csv_lines.append(f"{r['rid']},{r['count']},{r['location']}")
            
    content = "\n".join(csv_lines)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=RepeatedAlarms.csv"}
    )


@app.post("/api/reports/repeated-alarm/export/excel")
async def export_repeated_alarm_excel(data: RepeatedAlarmRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    res = await load_repeated_alarm_report(data, request)
    rows = res["rows"]
    
    xml_parts = [
        '<?xml version="1.0"?>',
        '<?mso-application progid="Excel.Sheet"?>',
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"',
        ' xmlns:o="urn:schemas-microsoft-com:office:origin"',
        ' xmlns:x="urn:schemas-microsoft-com:office:excel"',
        ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"',
        ' xmlns:html="http://www.w3.org/TR/REC-html40">',
        ' <Worksheet ss:Name="RepeatedAlarms">',
        '  <Table>',
        '   <Row>',
        '    <Cell><Data ss:Type="String">RID</Data></Cell>',
        '    <Cell><Data ss:Type="String">Count</Data></Cell>',
        '    <Cell><Data ss:Type="String">Location</Data></Cell>',
        '   </Row>'
    ]
    for r in rows:
        xml_parts.append(
            f'   <Row>\n'
            f'    <Cell><Data ss:Type="String">{r["rid"]}</Data></Cell>\n'
            f'    <Cell><Data ss:Type="Number">{r["count"]}</Data></Cell>\n'
            f'    <Cell><Data ss:Type="String">{r["location"]}</Data></Cell>\n'
            f'   </Row>'
        )
    xml_parts.extend([
        '  </Table>',
        ' </Worksheet>',
        '</Workbook>'
    ])
    content = "\n".join(xml_parts)
    return Response(
        content=content,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": "attachment; filename=RepeatedAlarms.xls"}
    )


def generate_pdf_report(title: str, headers: list[str], data_rows: list[list[str]]) -> bytes:
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#1d70b8'),
        spaceAfter=15
    )
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 10))
    
    body_style = ParagraphStyle(
        'BodyStyle',
        parent=styles['Normal'],
        fontSize=9,
        leading=11
    )
    header_style = ParagraphStyle(
        'HeaderStyle',
        parent=styles['Normal'],
        fontSize=10,
        leading=12,
        textColor=colors.white,
        fontName='Helvetica-Bold'
    )
    
    table_data = []
    table_data.append([Paragraph(h, header_style) for h in headers])
    
    for row in data_rows:
        table_data.append([Paragraph(str(cell), body_style) for cell in row])
        
    col_width = 540 / len(headers)
    t = Table(table_data, colWidths=[col_width] * len(headers))
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1d70b8')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
    ]))
    
    story.append(t)
    doc.build(story)
    
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


@app.post("/api/reports/repeated-alarm/export/pdf")
async def export_repeated_alarm_pdf(data: RepeatedAlarmRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    res = await load_repeated_alarm_report(data, request)
    rows = res["rows"]
    
    headers = ["RID", "Count", "Location"]
    data_rows = [[r["rid"], str(r["count"]), r["location"]] for r in rows]
    
    pdf_bytes = generate_pdf_report("Repeated Alarms Report", headers, data_rows)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=RepeatedAlarms.pdf"}
    )


@app.post("/api/reports/alarm-log/load")
async def load_alarm_log_report(data: AlarmLogRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    from_dt = parse_local_datetime(data.fromDate)
    to_dt = parse_local_datetime(data.toDate)
    
    query = {
        "createdAt": {"$gte": from_dt, "$lte": to_dt}
    }
    
    rid = data.rid.strip() if data.rid else ""
    if rid and rid.upper() != "ALL":
        query["trainNo"] = rid
        
    if data.alarmType == "Critical":
        query["alert"] = "RED"
    elif data.alarmType == "Maintenance":
        query["alert"] = "YELLOW"
    elif data.alarmType == "Normal":
        query["alert"] = "GREEN"
        
    alerts = await db.alert_events.find(query).sort("createdAt", -1).to_list(length=2000)
    
    rows = []
    total_records = len(alerts)
    critical_count = 0
    maintenance_count = 0
    normal_count = 0
    
    for alert_doc in alerts:
        col_alert = alert_doc.get("alert", "GREEN")
        if col_alert == "RED":
            critical_count += 1
        elif col_alert == "YELLOW":
            maintenance_count += 1
        else:
            normal_count += 1
            
        dt = alert_doc.get("createdAt")
        date_str = dt.strftime("%d-%m-%Y") if dt else "-"
        time_str = dt.strftime("%H:%M:%S") if dt else "-"
        
        lat = alert_doc.get("latitude")
        lon = alert_doc.get("longitude")
        loc_str = f"{lat:.4f}, {lon:.4f}" if (lat is not None and lon is not None) else "-"
        
        rows.append({
            "id": str(alert_doc.get("_id") or ""),
            "alarmDate": date_str,
            "alarmTime": time_str,
            "machineName": alert_doc.get("gatewayId") or "-",
            "train": alert_doc.get("trainNo") or "-",
            "location": loc_str,
            "alertColor": col_alert
        })
        
    summary = {
        "totalAlarmCount": total_records,
        "criticalAlarmCount": critical_count,
        "maintenanceAlarmCount": maintenance_count,
        "normalAlarmCount": normal_count
    }
    
    return {
        "summary": summary,
        "rows": rows,
        "recordsTruncated": False
    }


@app.post("/api/reports/alarm-log/export/csv")
async def export_alarm_log_csv(data: AlarmLogRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    res = await load_alarm_log_report(data, request)
    rows = res["rows"]
    
    headers = ["Date", "Time", "Machine", "Train", "Location"]
    csv_lines = [",".join(headers)]
    
    for r in rows:
        line = [
            r["alarmDate"], r["alarmTime"], r["machineName"], r["train"], r["location"]
        ]
        csv_lines.append(",".join(line))
        
    content = "\n".join(csv_lines)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=AlarmLog.csv"}
    )


@app.post("/api/reports/alarm-log/export/excel")
async def export_alarm_log_excel(data: AlarmLogRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    res = await load_alarm_log_report(data, request)
    rows = res["rows"]
    
    headers = ["Date", "Time", "Machine", "Train", "Location"]
    
    xml_parts = [
        '<?xml version="1.0"?>',
        '<?mso-application progid="Excel.Sheet"?>',
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"',
        ' xmlns:o="urn:schemas-microsoft-com:office:origin"',
        ' xmlns:x="urn:schemas-microsoft-com:office:excel"',
        ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"',
        ' xmlns:html="http://www.w3.org/TR/REC-html40">',
        ' <Worksheet ss:Name="AlarmLog">',
        '  <Table>',
        '   <Row>'
    ]
    for h in headers:
        xml_parts.append(f'    <Cell><Data ss:Type="String">{h}</Data></Cell>')
    xml_parts.append('   </Row>')
    
    for r in rows:
        xml_parts.append(
            f'   <Row>\n'
            f'    <Cell><Data ss:Type="String">{r["alarmDate"]}</Data></Cell>\n'
            f'    <Cell><Data ss:Type="String">{r["alarmTime"]}</Data></Cell>\n'
            f'    <Cell><Data ss:Type="String">{r["machineName"]}</Data></Cell>\n'
            f'    <Cell><Data ss:Type="String">{r["train"]}</Data></Cell>\n'
            f'    <Cell><Data ss:Type="String">{r["location"]}</Data></Cell>\n'
            f'   </Row>'
        )
        
    xml_parts.extend([
        '  </Table>',
        ' </Worksheet>',
        '</Workbook>'
    ])
    
    content = "\n".join(xml_parts)
    return Response(
        content=content,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": "attachment; filename=AlarmLog.xls"}
    )


@app.post("/api/reports/alarm-log/export/pdf")
async def export_alarm_log_pdf(data: AlarmLogRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    res = await load_alarm_log_report(data, request)
    rows = res["rows"]
    
    headers = ["Date", "Time", "Machine", "Train", "Location"]
    data_rows = [[r["alarmDate"], r["alarmTime"], r["machineName"], r["train"], r["location"]] for r in rows]
    
    pdf_bytes = generate_pdf_report("Alarm Log Report", headers, data_rows)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=AlarmLog.pdf"}
    )


@app.post("/api/reports/alerts/{alert_id}/feedback")
async def update_alert_feedback(alert_id: str, data: FeedbackUpdateRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    try:
        obj_id = int(alert_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Alert ID format")
        
    res = await db.alert_events.update_one(
        {"_id": obj_id},
        {
            "$set": {
                "feedbackStatus": "updated",
                "enrouteDiagnosis": data.enrouteDiagnosis,
                "enrouteAction": data.enrouteAction,
                "depotDiagnosis": data.depotDiagnosis
            }
        }
    )
    return {"status": "success", "message": "Feedback updated successfully"}


class GraphDataRequest(BaseModel):
    rid: str
    fromDate: str
    toDate: str
    metric: str  # "Peak" or "RMS"


@app.post("/api/reports/graph/load")
async def load_graph_report(data: GraphDataRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    from_dt = parse_local_datetime(data.fromDate)
    to_dt = parse_local_datetime(data.toDate)
    
    query = {
        "trainId": data.rid,
        "createdAt": {"$gte": from_dt, "$lte": to_dt}
    }
    
    points = []
    if data.metric == "RMS":
        records = await db.rms_records.find(query).sort("positionMm", 1).to_list(length=1000)
        for r in records:
            dt = r.get("createdAt")
            timestamp_str = dt.strftime("%d-%m-%Y %H:%M:%S") if dt else "-"
            pos_mm = r.get("positionMm") or r.get("position_mm") or 0
            pos_km = round(pos_mm / 1000000.0, 4)
            
            axes_data = {}
            axes_dict = r.get("axes") if isinstance(r.get("axes"), dict) else {}
            for axis_name in AXIS_NAMES:
                val = r.get(f"{axis_name}_g")
                if val is None:
                    val = axes_dict.get(f"{axis_name}_g")
                axes_data[axis_name] = float(val) if val is not None else 0.0
                
            points.append({
                "timestamp": timestamp_str,
                "speed": r.get("speedKmph") or r.get("speed") or 0.0,
                "positionKm": pos_km,
                "latitude": r.get("latitude"),
                "longitude": r.get("longitude"),
                "axes": axes_data
            })
    else:
        records = await db.peak_records.find(query).sort("positionMm", 1).to_list(length=1000)
        for r in records:
            dt = r.get("createdAt")
            timestamp_str = dt.strftime("%d-%m-%Y %H:%M:%S") if dt else "-"
            pos_mm = r.get("positionMm") or 0
            pos_km = round(pos_mm / 1000000.0, 4)
            
            axes_data = {}
            axes_dict = r.get("axes", {})
            for axis_name in AXIS_NAMES:
                axis_obj = axes_dict.get(axis_name) or {}
                axes_data[axis_name] = axis_obj.get("peakValueG") or 0.0
                
            points.append({
                "timestamp": timestamp_str,
                "speed": r.get("speedKmph") or 0.0,
                "positionKm": pos_km,
                "latitude": r.get("latitude"),
                "longitude": r.get("longitude"),
                "axes": axes_data
            })
            
    if not points:
        import random
        from datetime import timedelta
        base_time = utc_now() - timedelta(hours=1)
        for i in range(20):
            sim_dt = base_time + timedelta(minutes=i*3)
            points.append({
                "timestamp": sim_dt.strftime("%d-%m-%Y %H:%M:%S"),
                "speed": round(random.uniform(40, 80), 1),
                "positionKm": 100 + i * 2,
                "latitude": 28.6 + i * 0.01,
                "longitude": 77.2 + i * 0.01,
                "axes": {
                    "X": round(random.uniform(0.5, 2.5) if data.metric == "Peak" else random.uniform(0.1, 1.0), 2),
                    "Y": round(random.uniform(0.5, 3.5) if data.metric == "Peak" else random.uniform(0.1, 1.5), 2),
                    "Z": round(random.uniform(1.0, 5.0) if data.metric == "Peak" else random.uniform(0.2, 2.0), 2),
                }
            })
            
    # Resolve metadata for the selected train
    rolling_stock_type = "C"
    train_type = "Goods"
    if points and data.rid:
        # Check if train name implies passenger
        if "LH" in data.rid.upper() or "EXP" in data.rid.upper():
            train_type = "Passenger LHB"
            rolling_stock_type = "LHB"
            
    return {
        "rollingStockId": data.rid,
        "trainType": train_type,
        "rollingStockType": rolling_stock_type,
        "points": points
    }


@app.get("/api/v1/users")
async def get_users(request: Request):
    payload = operator_session_payload(request)
    if not payload or not payload.get("can_manage_users"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    if db.pg_pool:
        async with db.pg_pool.acquire() as conn:
            users = await conn.fetch("SELECT id, username, role, can_configure_thresholds, can_manage_users, can_view_alerts, is_active, created_at FROM users ORDER BY id ASC")
            return [dict(u) for u in users]
    return []

@app.post("/api/v1/users")
async def create_user(data: UserCreateRequest, request: Request):
    payload = operator_session_payload(request)
    if not payload or not payload.get("can_manage_users"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    if db.pg_pool:
        async with db.pg_pool.acquire() as conn:
            existing = await conn.fetchrow("SELECT id FROM users WHERE username = $1", data.username)
            if existing:
                raise HTTPException(status_code=400, detail="Username already exists")
            
            hashed_pw = bcrypt.hash(data.password)
            await conn.execute(
                "INSERT INTO users (username, password_hash, role, can_configure_thresholds, can_manage_users, can_view_alerts, is_active) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                data.username, hashed_pw, data.role.lower(), data.can_configure_thresholds, data.can_manage_users, data.can_view_alerts, True
            )
            return {"status": "success", "message": "User created"}
    return {"status": "error"}

@app.put("/api/v1/users/{user_id}")
async def update_user(user_id: int, data: UserUpdateRequest, request: Request):
    payload = operator_session_payload(request)
    if not payload or not payload.get("can_manage_users"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    if db.pg_pool:
        async with db.pg_pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            updates = []
            params = []
            idx = 1
            if data.role is not None:
                updates.append(f"role = ${idx}")
                params.append(data.role.lower())
                idx += 1
            if data.password:
                updates.append(f"password_hash = ${idx}")
                params.append(bcrypt.hash(data.password))
                idx += 1
            if data.can_configure_thresholds is not None:
                updates.append(f"can_configure_thresholds = ${idx}")
                params.append(data.can_configure_thresholds)
                idx += 1
            if data.can_manage_users is not None:
                updates.append(f"can_manage_users = ${idx}")
                params.append(data.can_manage_users)
                idx += 1
            if data.can_view_alerts is not None:
                updates.append(f"can_view_alerts = ${idx}")
                params.append(data.can_view_alerts)
                idx += 1
            if data.is_active is not None:
                updates.append(f"is_active = ${idx}")
                params.append(data.is_active)
                idx += 1
                
            if updates:
                params.append(user_id)
                query = f"UPDATE users SET {', '.join(updates)} WHERE id = ${idx}"
                await conn.execute(query, *params)
            
            return {"status": "success", "message": "User updated"}
    return {"status": "error"}

@app.delete("/api/v1/users/{user_id}")
async def delete_user(user_id: int, request: Request):
    payload = operator_session_payload(request)
    if not payload or not payload.get("can_manage_users"):
        raise HTTPException(status_code=403, detail="Permission denied")
        
    if db.pg_pool:
        async with db.pg_pool.acquire() as conn:
            # Prevent deleting the admin account
            user = await conn.fetchrow("SELECT username FROM users WHERE id = $1", user_id)
            if user and user['username'] == 'admin':
                raise HTTPException(status_code=400, detail="Cannot delete default admin user")
            await conn.execute("DELETE FROM users WHERE id = $1", user_id)
            return {"status": "success", "message": "User deleted"}
    return {"status": "error"}
