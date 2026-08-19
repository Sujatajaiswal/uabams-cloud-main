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

SPATIAL_RETENTION_DAYS = 90
TIME_DOMAIN_RETENTION_DAYS = 30
SPATIAL_RETENTION_SECONDS = SPATIAL_RETENTION_DAYS * 24 * 60 * 60
RAW_TIME_DOMAIN_CHUNK_BYTES = 8 * 1024 * 1024
TIME_DOMAIN_DIR = os.environ.get("TIME_DOMAIN_DIR", "/app/time_domain")
OPERATOR_COOKIE_NAME = "uabams_operator_session"
OPERATOR_SESSION_HOURS = 168


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
            await db.pg_pool.execute(
                """
                INSERT INTO activity_logs (username, page, action, ip_address, created_at)
                VALUES ($1, $2, $3, $4, $5)
                """,
                username,
                path,
                f"{request.method} {path}",
                client_ip(request),
                utc_now()
            )
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

                    CREATE TABLE IF NOT EXISTS zones (
                        id SERIAL PRIMARY KEY,
                        code VARCHAR(50) UNIQUE NOT NULL,
                        name VARCHAR(255) NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS divisions (
                        id SERIAL PRIMARY KEY,
                        zone_id INTEGER REFERENCES zones(id) ON DELETE CASCADE,
                        code VARCHAR(50) NOT NULL,
                        name VARCHAR(255) NOT NULL,
                        UNIQUE(zone_id, code)
                    );
                    CREATE TABLE IF NOT EXISTS sections (
                        id SERIAL PRIMARY KEY,
                        division_id INTEGER REFERENCES divisions(id) ON DELETE CASCADE,
                        code VARCHAR(50) NOT NULL,
                        name VARCHAR(255) NOT NULL,
                        start_km DOUBLE PRECISION,
                        end_km DOUBLE PRECISION,
                        UNIQUE(division_id, code)
                    );
                    
                    INSERT INTO zones (code, name) VALUES ('NCR', 'North Central Railway (NCR)') ON CONFLICT (code) DO NOTHING;
                    INSERT INTO zones (code, name) VALUES ('SR', 'Southern Railway (SR)') ON CONFLICT (code) DO NOTHING;
                    
                    INSERT INTO divisions (zone_id, code, name) 
                    SELECT id, 'Prayagraj', 'Prayagraj Division' FROM zones WHERE code = 'NCR'
                    ON CONFLICT (zone_id, code) DO NOTHING;
                    
                    INSERT INTO divisions (zone_id, code, name) 
                    SELECT id, 'Chennai', 'Chennai Division' FROM zones WHERE code = 'SR'
                    ON CONFLICT (zone_id, code) DO NOTHING;
                    
                    INSERT INTO sections (division_id, code, name, start_km, end_km) 
                    SELECT id, 'ABC-XYZ', 'ABC - XYZ', 0, 100 FROM divisions WHERE code = 'Prayagraj'
                    ON CONFLICT (division_id, code) DO NOTHING;
                    
                    INSERT INTO sections (division_id, code, name, start_km, end_km) 
                    SELECT id, 'Section-1', 'Section 1 (KM 0 - 127)', 0, 127 FROM divisions WHERE code = 'Chennai'
                    ON CONFLICT (division_id, code) DO NOTHING;

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

from app.routers import auth, telemetry, gateways, hierarchy, ui, logs

app.include_router(ui.router)
app.include_router(auth.router)
app.include_router(logs.router)
app.include_router(gateways.router)
app.include_router(telemetry.router)
app.include_router(hierarchy.router)
