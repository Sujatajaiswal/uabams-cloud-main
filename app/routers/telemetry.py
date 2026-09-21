from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

def generate_pdf_report(title: str, headers: list, rows: list) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph(title, styles['Title']))
    
    data = [headers] + rows
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 8)
    ]))
    elements.append(table)
    doc.build(elements)
    return buffer.getvalue()

import json
import os
import uuid
import traceback
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from math import isfinite, atan2, cos, degrees, radians, sin
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.database import db, settings
from app.middleware.auth import async_gateway_id_for_key, normalize_gateway_id
from app.models import (
    AlertRequest,
    HeartbeatRequest,
    ResetSessionRequest,
    TargetedResetRequest,
    UploadLeaseRequest,
    UploadCompleteRequest,
)
from app.parsers.archive import parse_archive_zip, peak_records_to_alert_events, AXIS_NAMES
from app.utils import (
    utc_now, serialize, verify_gateway_token, absolute_cloud_url,
    resolve_train_id, location_box,
    SPATIAL_RETENTION_DAYS, TIME_DOMAIN_RETENTION_DAYS,
    apply_wheel_compensation, is_operator_authenticated, operator_username, client_ip, 
    operator_session_payload, send_sms
)

router = APIRouter()

TIME_DOMAIN_DIR = os.environ.get("TIME_DOMAIN_DIR", "/app/time_domain")

async def mark_gateway_online(gateway_id: str, now: datetime) -> None:
    await db.pg_pool.execute("""
        INSERT INTO gateways (gateway_id, status, last_seen, updated_at, created_at)
        VALUES ($1, 'active', $2, $2, $2)
        ON CONFLICT (gateway_id) DO UPDATE SET
            status = 'active',
            last_seen = EXCLUDED.last_seen, updated_at = EXCLUDED.updated_at
    """, gateway_id, now)

    await db.pg_pool.execute("""
        INSERT INTO gateway_status (gateway_id, online, last_heartbeat)
        VALUES ($1, TRUE, $2)
        ON CONFLICT (gateway_id) DO UPDATE SET
            online = TRUE, last_heartbeat = EXCLUDED.last_heartbeat
    """, gateway_id, now)

async def store_time_domain_files(
    archive_source: bytes | str,
    raw_files: list[dict[str, Any]],
    gateway_id: str,
    train_id: str,
    session_name: str,
    archive_sha256: str,
    created_at: datetime,
    logical_gateway_id: str | None = None,
) -> list[dict[str, Any]]:
    import shutil
    import zipfile
    from io import BytesIO

    await db.pg_pool.execute("DELETE FROM time_domain_chunks WHERE archive_sha256 = $1 AND gateway_id = $2", archive_sha256, gateway_id)
    await db.pg_pool.execute("DELETE FROM time_domain_files WHERE archive_sha256 = $1 AND gateway_id = $2", archive_sha256, gateway_id)

    expires_at = created_at + timedelta(days=TIME_DOMAIN_RETENTION_DAYS)
    stored_files: list[dict[str, Any]] = []

    archive_dir = os.path.join(TIME_DOMAIN_DIR, gateway_id, archive_sha256)
    os.makedirs(archive_dir, exist_ok=True)

    archive_file = archive_source if isinstance(archive_source, str) else BytesIO(archive_source)
    with zipfile.ZipFile(archive_file) as archive:
        for raw_file in raw_files:
            zip_member = raw_file.get("zip_member")
            if not zip_member:
                continue

            original_path = raw_file.get("path") or "unknown"
            safe_filename = os.path.basename(original_path) or "data.bin"
            fs_path = os.path.join(archive_dir, safe_filename)

            file_sha256_hash = sha256()
            file_size = 0
            with archive.open(zip_member) as src, open(fs_path, "wb") as dst:
                while True:
                    chunk = src.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
                    file_sha256_hash.update(chunk)
                    file_size += len(chunk)
            file_sha256 = file_sha256_hash.hexdigest()
            
            await db.pg_pool.execute(
                """
                INSERT INTO time_domain_files 
                (gateway_id, train_id, logical_gateway_id, session_name, archive_sha256, filename, path, size_bytes, sha256, chunk_count, created_at, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 0, $10, $11)
                """,
                gateway_id, train_id, logical_gateway_id, session_name, archive_sha256, safe_filename, fs_path, file_size, file_sha256, created_at, expires_at
            )

            stored_files.append({
                "filename": safe_filename,
                "path": fs_path,
                "sizeBytes": file_size,
                "sha256": file_sha256,
                "expiresAt": expires_at,
            })

    return stored_files

async def process_and_ingest_archive(
    gateway_id: str,
    train_id: str,
    source: bytes | str,
    actual_sha256: str,
    logical_gateway_id: str | None = None,
    content_type: str = "application/zip"
) -> dict[str, Any]:
    try:
        parsed = parse_archive_zip(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    now = utc_now()
    metadata = parsed.metadata or {}
    session_name = metadata.get("sessionName") or metadata.get("sessionId") or f"{gateway_id}-{int(now.timestamp())}"
    resolved_train_id = await resolve_train_id(gateway_id, metadata.get("trainId"), metadata.get("trainNo"), train_id)
    session_status = metadata.get("sessionStatus", "unknown")
    warnings = list(parsed.warnings)

    calibration = await db.pg_pool.fetchrow("SELECT * FROM calibration_versions WHERE gateway_id = $1 ORDER BY version DESC LIMIT 1", gateway_id)
    wheel_compensation = apply_wheel_compensation(parsed.rms_records, parsed.peak_records, dict(calibration) if calibration else None)

    await db.pg_pool.execute("DELETE FROM rms_records WHERE archive_sha256 = $1 AND gateway_id = $2", actual_sha256, gateway_id)
    await db.pg_pool.execute("DELETE FROM peak_records WHERE archive_sha256 = $1 AND gateway_id = $2", actual_sha256, gateway_id)
    await db.pg_pool.execute("DELETE FROM fault_records WHERE archive_sha256 = $1 AND gateway_id = $2", actual_sha256, gateway_id)
    await db.pg_pool.execute("DELETE FROM alert_events WHERE archive_sha256 = $1 AND gateway_id = $2 AND source = 'peak_50m.bin'", actual_sha256, gateway_id)

    if parsed.rms_records:
        rms_data = []
        for r in parsed.rms_records:
            rms_data.append((
                resolved_train_id, gateway_id, logical_gateway_id, session_name, actual_sha256,
                r.get('latitude'), r.get('longitude'), r.get('gpsValid'),
                r.get('bearing'), r.get('speedKmph'), r.get('positionMm'),
                json.dumps(r.get('axes', {})),
                r.get('al_x_g'), r.get('al_y_g'), r.get('al_z_g'),
                r.get('ar_x_g'), r.get('ar_y_g'), r.get('ar_z_g'),
                r.get('bg_x_g'), r.get('bg_y_g'), r.get('bg_z_g'),
                now
            ))
        rms_cols = [
            'train_id', 'gateway_id', 'logical_gateway_id', 'session_name', 'archive_sha256',
            'latitude', 'longitude', 'gps_valid', 'bearing', 'speed', 'position_mm',
            'axes', 'al_x_g', 'al_y_g', 'al_z_g', 'ar_x_g', 'ar_y_g', 'ar_z_g',
            'bg_x_g', 'bg_y_g', 'bg_z_g', 'created_at'
        ]
        await db.pg_pool.copy_records_to_table('rms_records', records=rms_data, columns=rms_cols)

    if parsed.peak_records:
        peak_data = []
        for r in parsed.peak_records:
            peak_data.append((
                resolved_train_id, gateway_id, logical_gateway_id, actual_sha256,
                r.get('windowStartMm'), r.get('windowEndMm'), r.get('positionMm'),
                r.get('startKm'), r.get('endKm'),
                r.get('speedKmph'), r.get('avgSpeedKmph'), r.get('minSpeedKmph'), r.get('maxSpeedKmph'),
                r.get('validMask'), r.get('alertGenerated'), r.get('alertsCount'),
                r.get('latitude'), r.get('longitude'), json.dumps(r.get('axes', {})), now
            ))
        peak_cols = [
            'train_id', 'gateway_id', 'logical_gateway_id', 'archive_sha256',
            'window_start_mm', 'window_end_mm', 'position_mm',
            'start_km', 'end_km',
            'speed_kmph', 'avg_speed_kmph', 'min_speed_kmph', 'max_speed_kmph',
            'valid_mask', 'alert_generated', 'alerts_count',
            'latitude', 'longitude', 'axes', 'created_at'
        ]
        await db.pg_pool.copy_records_to_table('peak_records', records=peak_data, columns=peak_cols)

    if parsed.fault_records:
        fault_data = []
        for r in parsed.fault_records:
            fault_data.append((
                resolved_train_id, gateway_id, logical_gateway_id, actual_sha256,
                r.get('timestampMs'), r.get('faultCode'), r.get('description'), now
            ))
        await db.pg_pool.executemany("""
            INSERT INTO fault_records
            (train_id, gateway_id, logical_gateway_id, archive_sha256, timestamp_ms, fault_code, description, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """, fault_data)

    peak_alerts = peak_records_to_alert_events(
        parsed.peak_records, gateway_id, resolved_train_id, session_name, actual_sha256, now
    )
    if peak_alerts:
        alert_data = []
        for r in peak_alerts:
            alert_data.append((
                r.get('trainNo'), r.get('gatewayId'), logical_gateway_id, r.get('alert_type', 'peak'),
                r.get('latitude'), r.get('longitude'), r.get('positionMm'),
                r.get('sessionName'), r.get('archiveSha256'),
                r.get('source'), r.get('peakAxis'), r.get('peakValueG'), r.get('speedKmph'),
                r.get('alert'), r.get('sessionStatus', 'active'), r.get('zone'),
                r.get('division'), r.get('section'), r.get('archivedAt'), now,
                r.get('sensor'), r.get('axis'), r.get('channel'), r.get('thresholdG'),
                r.get('locationKm'), r.get('startKm'), r.get('endKm'),
                r.get('windowAvgSpeedKmph'), r.get('minSpeedKmph'), r.get('maxSpeedKmph'),
                r.get('alertsCount')
            ))
        await db.pg_pool.executemany("""
            INSERT INTO alert_events
            (train_no, gateway_id, logical_gateway_id, alert_type, latitude, longitude, position_mm, session_name, archive_sha256, source, peak_axis, peak_value_g, speed_kmph, alert, session_status, zone, division, section, archived_at, created_at, sensor, axis, channel, threshold_g, location_km, start_km, end_km, window_speed_kmph, min_speed_kmph, max_speed_kmph, alerts_count)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31)
        """, alert_data)

    try:
        stored_raw_files = await store_time_domain_files(source, parsed.raw_files, gateway_id, resolved_train_id, session_name, actual_sha256, now, logical_gateway_id)
    except Exception as exc:
        print(f"Warning: Raw time domain storage exception: {exc}")
        stored_raw_files = []

    size_bytes = len(source) if isinstance(source, bytes) else os.path.getsize(source)
    status_str = "processed_with_warnings" if warnings else "processed"
    
    existing = await db.pg_pool.fetchrow("SELECT id FROM archives WHERE gateway_id = $1 AND sha256 = $2", gateway_id, actual_sha256)
    if existing:
        await db.pg_pool.execute("""
            UPDATE archives SET
            train_id = $1, logical_gateway_id = $2, session_name = $3, session_status = $4, size_bytes = $5, status = $6, parse_warnings = $7
            WHERE id = $8
        """, resolved_train_id, logical_gateway_id, session_name, session_status, size_bytes, status_str, json.dumps(warnings), existing["id"])
    else:
        await db.pg_pool.execute("""
            INSERT INTO archives (gateway_id, sha256, received_at, train_id, logical_gateway_id, session_name, session_status, size_bytes, status, parse_warnings)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """, gateway_id, actual_sha256, now, resolved_train_id, logical_gateway_id, session_name, session_status, size_bytes, status_str, json.dumps(warnings))

    try:
        await mark_gateway_online(gateway_id, now)
    except Exception as exc:
        pass

    return {
        "status": "success",
        "sha256": actual_sha256,
        "sizeBytes": size_bytes,
        "sessionName": session_name,
        "rmsRecords": len(parsed.rms_records),
        "peakRecords": len(parsed.peak_records),
        "faultRecords": len(parsed.fault_records),
        "peakAlerts": len(peak_alerts),
        "rmsIntervalValidation": parsed.rms_validation,
        "wheelCompensation": wheel_compensation,
        "rawTimeDomainFiles": len(stored_raw_files),
        "retention": {"spatialAndAlertsDays": SPATIAL_RETENTION_DAYS, "timeDomainDays": TIME_DOMAIN_RETENTION_DAYS},
        "warnings": warnings,
    }


@router.post("/api/v1/heartbeat")
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
    gateway = await db.pg_pool.fetchrow("SELECT gateway_serial AS \"gatewaySerial\", train_id AS \"trainId\" FROM gateways WHERE gateway_id = $1", gateway_id)
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not registered")

    registered_serial = gateway.get("gatewaySerial")
    if data.gatewaySerial and registered_serial and data.gatewaySerial != registered_serial:
        raise HTTPException(status_code=403, detail="Gateway serial does not match registered gateway")

    for result in data.commandResults:
        command = await db.pg_pool.fetchrow("SELECT type, status FROM gateway_commands WHERE command_id = $1 AND gateway_id = $2", result.commandId, gateway_id)
        if not command or command.get("type") != result.type:
            continue
        if command.get("status") in ("success", "failed", "superseded", "ignored"):
            continue

        completed_at = result.completedAt or now
        res_dict = {
            "commandId": result.commandId,
            "type": result.type,
            "status": result.status,
            "completedAt": completed_at.isoformat(),
        }
        if result.location is not None:
            res_dict["location"] = result.location
        if result.details is not None:
            res_dict["details"] = result.details
        if hasattr(result, "reason") and result.reason is not None:
            res_dict["reason"] = result.reason
        if hasattr(result, "nodes") and result.nodes is not None:
            nodes_dict = result.nodes.model_dump(exclude_unset=True) if hasattr(result.nodes, "model_dump") else result.nodes.dict(exclude_unset=True)
            res_dict["nodes"] = nodes_dict
        
        res_json = json.dumps(res_dict)
        await db.pg_pool.execute("UPDATE gateway_commands SET status = $1, result = $2::jsonb, completed_at = $3 WHERE command_id = $4 AND gateway_id = $5", result.status, res_json, completed_at, result.commandId, gateway_id)

    await db.pg_pool.execute("UPDATE gateways SET last_seen = $1, status = 'active', last_heartbeat = $1 WHERE gateway_id = $2", now, gateway_id)
    await db.pg_pool.execute("INSERT INTO heartbeat_logs (gateway_id, train_id, logical_gateway_id, received_at, adxl_state, encoder_state) VALUES ($1, $2, $3, $4, $5, $6)", gateway_id, data.trainId, data.logicalGatewayId, now, data.adxlState, data.encoderState)
    await db.pg_pool.execute("""
        INSERT INTO gateway_status (gateway_id, train_id, online, last_heartbeat, adxl_state, adxl_uptime, adxl_faults, adxl_fw_version, adxl_cal_version, encoder_state, encoder_uptime, encoder_faults, encoder_fw_version, encoder_cal_version)
        VALUES ($1, $2, TRUE, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        ON CONFLICT (gateway_id) DO UPDATE SET
            train_id = COALESCE(EXCLUDED.train_id, gateway_status.train_id), online = TRUE, last_heartbeat = EXCLUDED.last_heartbeat,
            adxl_state = EXCLUDED.adxl_state, adxl_uptime = EXCLUDED.adxl_uptime, adxl_faults = EXCLUDED.adxl_faults, adxl_fw_version = EXCLUDED.adxl_fw_version, adxl_cal_version = EXCLUDED.adxl_cal_version,
            encoder_state = EXCLUDED.encoder_state, encoder_uptime = EXCLUDED.encoder_uptime, encoder_faults = EXCLUDED.encoder_faults, encoder_fw_version = EXCLUDED.encoder_fw_version, encoder_cal_version = EXCLUDED.encoder_cal_version
    """, gateway_id, data.trainId, now, data.adxlState, data.adxlUptime, data.adxlFaults, data.adxlFwVersion, data.adxlCalVersion, data.encoderState, data.encoderUptime, data.encoderFaults, data.encoderFwVersion, data.encoderCalVersion)



    pending_commands = await db.pg_pool.fetch("SELECT command_id AS \"commandId\", type, status, version, payload_url AS \"payloadUrl\", sha256, delivered_at AS \"deliveredAt\", delivery_count AS \"deliveryCount\" FROM gateway_commands WHERE gateway_id = $1 AND status = 'pending' ORDER BY created_at DESC LIMIT 50", gateway_id)

    commands = []
    latest_types = set()
    for command in pending_commands:
        command_type = command.get("type")
        if command_type in latest_types:
            await db.pg_pool.execute("UPDATE gateway_commands SET status = 'superseded', completed_at = $1, result = $2::jsonb WHERE command_id = $3 AND gateway_id = $4", now, json.dumps({"status": "superseded", "details": {"reason": "newer command exists"}}), command.get("commandId"), gateway_id)
            continue
        latest_types.add(command_type)
        commands.append(dict(command))

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
        await db.pg_pool.execute("UPDATE gateway_commands SET status = 'delivered', delivered_at = COALESCE(delivered_at, $1), last_delivered_at = $1, delivery_count = $2 WHERE command_id = $3 AND gateway_id = $4", now, delivery_count, command_id, gateway_id)

    return {"serverTime": now.isoformat(), "commands": commands_out}


@router.put("/api/v1/archive")
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
        source=archive_body,
        actual_sha256=actual_sha256,
        logical_gateway_id=request.state.logical_gateway_id,
        content_type=request.headers.get("content-type", "application/zip")
    )


@router.post("/api/v1/archive/lease")
async def create_upload_lease(
    data: UploadLeaseRequest,
    request: Request,
    x_api_key: Annotated[str, Header(alias="X-Api-Key")]
):
    gateway_id = request.state.gateway_id
    
    auth_doc = await db.pg_pool.fetchrow("SELECT upload_enabled, revoked_at FROM gateway_auth WHERE gateway_id = $1", gateway_id)
    if not auth_doc or not auth_doc.get("upload_enabled", True) or auth_doc.get("revoked_at"):
        raise HTTPException(status_code=403, detail="Secure upload is disabled or revoked for this gateway")
        
    upload_id = str(uuid.uuid4())
    
    base_dir = os.path.abspath(settings["upload_base_dir"])
    train_dir = os.path.join(base_dir, gateway_id)
    gateway_dir = os.path.join(train_dir, data.trainId)
    os.makedirs(gateway_dir, exist_ok=True)
    
    disk_temp_path = os.path.join(gateway_dir, f"{data.zipFileName}.part").replace("\\", "/")
    disk_final_path = os.path.join(gateway_dir, data.zipFileName).replace("\\", "/")
    
    client_temp_path = f"/incoming/{gateway_id}/{data.trainId}/{data.zipFileName}.part"
    client_final_path = f"/incoming/{gateway_id}/{data.trainId}/{data.zipFileName}"
    
    expires_utc = utc_now() + timedelta(hours=3)
    
    await db.pg_pool.execute("""
        INSERT INTO upload_leases (upload_id, gateway_id, train_id, logical_gateway_id, session_name, zip_file_name, sha256, size_bytes, remote_temp_path, remote_final_path, status, expires_utc)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'ready', $11)
    """, upload_id, gateway_id, data.trainId, data.logicalGatewayId, data.sessionName, data.zipFileName, data.sha256, data.sizeBytes, disk_temp_path, disk_final_path, expires_utc)
    
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


@router.post("/api/v1/archive/complete")
async def complete_upload(
    data: UploadCompleteRequest,
    request: Request,
    x_api_key: Annotated[str, Header(alias="X-Api-Key")]
):
    gateway_id = request.state.gateway_id
    
    lease = await db.pg_pool.fetchrow("SELECT remote_temp_path AS \"remoteTempPath\", remote_final_path AS \"remoteFinalPath\", size_bytes AS \"sizeBytes\", sha256, expires_utc AS \"expiresUtc\", logical_gateway_id AS \"logicalGatewayId\", status FROM upload_leases WHERE upload_id = $1 AND gateway_id = $2", data.uploadId, gateway_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Upload lease not found or does not belong to this gateway")

    if lease.get("expiresUtc") and utc_now() > lease.get("expiresUtc"):
        raise HTTPException(status_code=410, detail="Upload lease has expired")
        
    temp_path = lease.get("remoteTempPath")
    final_path = lease.get("remoteFinalPath")
    lease_size = lease.get("sizeBytes")
    lease_sha = lease.get("sha256")
    lease_status = lease.get("status")

    if not temp_path or not final_path:
        raise HTTPException(status_code=500, detail="Lease record is missing path fields.")

    # 1. If this lease was already processed, return immediate success
    if lease_status == "processed":
        return {
            "status": "verified",
            "uploadId": data.uploadId,
            "remoteFinalPath": final_path,
            "sha256Verified": True,
            "message": "Upload already processed"
        }

    # 2. Check if archive was already ingested under this SHA-256 (handles duplicate retries)
    expected_sha = lease_sha or data.sha256
    if expected_sha:
        existing_archive = await db.pg_pool.fetchrow(
            "SELECT id, session_name, status FROM archives WHERE gateway_id = $1 AND sha256 = $2 AND status IN ('processed', 'processed_with_warnings')",
            gateway_id, expected_sha.lower()
        )
        if existing_archive:
            await db.pg_pool.execute("UPDATE upload_leases SET status = 'processed' WHERE upload_id = $1", data.uploadId)
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return {
                "status": "verified",
                "uploadId": data.uploadId,
                "remoteFinalPath": final_path,
                "sha256Verified": True,
                "ingestion": {
                    "status": "success",
                    "sha256": expected_sha.lower(),
                    "sessionName": existing_archive.get("session_name"),
                    "message": "Archive already ingested"
                }
            }
    
    if not os.path.exists(temp_path):
        raise HTTPException(status_code=400, detail=f"Partial upload file not found on server at {temp_path}")
        
    actual_size = os.path.getsize(temp_path)
    if lease_size is not None and actual_size != lease_size:
        raise HTTPException(status_code=400, detail=f"File size mismatch: expected {lease_size}, got {actual_size}")
        
    disk_sha = sha256()
    with open(temp_path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            disk_sha.update(chunk)
    actual_sha = disk_sha.hexdigest()
    
    if lease_sha and actual_sha.lower() != lease_sha.lower():
        raise HTTPException(status_code=400, detail=f"SHA-256 verification failed")
        
    try:
        os.replace(temp_path, final_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to finalize file transfer: {exc}")
        
    await db.pg_pool.execute("UPDATE upload_leases SET status = 'verified' WHERE upload_id = $1", data.uploadId)

    # 3. Check if actual computed sha was already processed
    existing_archive = await db.pg_pool.fetchrow(
        "SELECT id, session_name, status FROM archives WHERE gateway_id = $1 AND sha256 = $2 AND status IN ('processed', 'processed_with_warnings')",
        gateway_id, actual_sha.lower()
    )
    if existing_archive:
        await db.pg_pool.execute("UPDATE upload_leases SET status = 'processed' WHERE upload_id = $1", data.uploadId)
        return {
            "status": "verified",
            "uploadId": data.uploadId,
            "remoteFinalPath": final_path,
            "sha256Verified": True,
            "ingestion": {
                "status": "success",
                "sha256": actual_sha.lower(),
                "sessionName": existing_archive.get("session_name"),
                "message": "Archive already ingested"
            }
        }
    
    try:
        effective_logical_id = data.logicalGatewayId or lease.get("logicalGatewayId")
        ingest_res = await process_and_ingest_archive(
            gateway_id=gateway_id,
            train_id=data.trainId,
            source=final_path,
            actual_sha256=actual_sha,
            logical_gateway_id=effective_logical_id,
            content_type="application/zip"
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")
    
    await db.pg_pool.execute("UPDATE upload_leases SET status = 'processed' WHERE upload_id = $1", data.uploadId)

    return {
        "status": "verified",
        "uploadId": data.uploadId,
        "remoteFinalPath": final_path,
        "sha256Verified": True,
        "ingestion": ingest_res
    }


@router.get("/api/v1/archive/status")
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
        
    lease = await db.pg_pool.fetchrow("SELECT gateway_id AS \"gatewayId\", remote_temp_path AS \"remoteTempPath\", remote_final_path AS \"remoteFinalPath\", status, size_bytes AS \"sizeBytes\" FROM upload_leases WHERE upload_id = $1", uploadId)
    if not lease:
        raise HTTPException(status_code=404, detail="Upload lease not found")
        
    if gateway_id and lease.get("gatewayId") != gateway_id:
        raise HTTPException(status_code=403, detail="Permission denied")
        
    temp_path = lease.get("remoteTempPath")
    received_bytes = 0
    if temp_path and os.path.exists(temp_path):
        received_bytes = os.path.getsize(temp_path)
        
    final_path = lease.get("remoteFinalPath")
    if final_path and os.path.exists(final_path) and lease.get("status") in ("verified", "processed"):
        received_bytes = lease.get("sizeBytes")
        
    return {
        "uploadId": uploadId,
        "status": lease.get("status", "ready"),
        "receivedBytes": received_bytes,
        "expectedBytes": lease.get("sizeBytes")
    }


@router.post("/api/v1/alert")
async def create_alert(
    request: Request,
    payload: Annotated[AlertRequest | None, Body()] = None,
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

    if data.gatewayId and normalize_gateway_id(data.gatewayId) != normalize_gateway_id(gateway_id) and data.gatewayId != gateway_id:
        raise HTTPException(status_code=403, detail="Session or API key does not belong to supplied gateway")
    train_no = await resolve_train_id(gateway_id, data.trainNo, request.state.train_id)

    now = utc_now()
    created_at = datetime.fromtimestamp(data.timestampUtcMs / 1000.0, tz=UTC) if data.timestampUtcMs else now

    # Store window-level alert data
    window_row = await db.pg_pool.fetchrow("""
        INSERT INTO window_alerts (
            gateway_id, logical_gateway_id, train_no, session_name,
            timestamp_utc_ms, start_km, end_km, speed_kmph,
            min_speed_kmph, max_speed_kmph, alerts_count, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        RETURNING id
    """, gateway_id, data.logicalGatewayId, train_no, data.sessionName,
       data.timestampUtcMs, data.startKm, data.endKm, data.speedKmph,
       data.minSpeedKmph, data.maxSpeedKmph, data.alertsCount, created_at)
    window_alert_id = window_row["id"] if window_row else None

    # Determine axis colors and index each axis violation into alert_events
    overall_color = "GREEN"
    axis_documents = []

    for item in data.alerts:
        if item.peakValueG > 80:
            axis_color = "RED"
        elif item.peakValueG > 50:
            axis_color = "YELLOW"
        elif item.thresholdG and item.peakValueG >= item.thresholdG:
            axis_color = "RED" if (item.peakValueG >= item.thresholdG * 1.5 or item.peakValueG >= 8.0) else "YELLOW"
        else:
            axis_color = "GREEN"

        if axis_color == "RED":
            overall_color = "RED"
        elif axis_color == "YELLOW" and overall_color != "RED":
            overall_color = "YELLOW"

        pos_mm = int(round(item.locationKm * 1_000_000)) if item.locationKm is not None else None

        await db.pg_pool.execute("""
            INSERT INTO alert_events (
                gateway_id, train_no, logical_gateway_id, alert_type,
                sensor, axis, channel, peak_axis,
                latitude, longitude, position_mm, location_km,
                start_km, end_km, session_name, source,
                peak_value_g, threshold_g, speed_kmph,
                window_speed_kmph, min_speed_kmph, max_speed_kmph,
                alerts_count, window_alert_id, alert, session_status, created_at
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7, $8,
                $9, $10, $11, $12,
                $13, $14, $15, $16,
                $17, $18, $19,
                $20, $21, $22,
                $23, $24, $25, $26, $27
            )
        """,
            gateway_id, train_no, data.logicalGatewayId, "realtime",
            item.sensor, item.axis, item.channel, item.channel,
            item.latitude, item.longitude, pos_mm, item.locationKm,
            data.startKm, data.endKm, data.sessionName, "api_v1_alert",
            item.peakValueG, item.thresholdG, item.speedKmph,
            data.speedKmph, data.minSpeedKmph, data.maxSpeedKmph,
            data.alertsCount, window_alert_id, axis_color, "active", created_at
        )
        axis_documents.append({
            "sensor": item.sensor,
            "axis": item.axis,
            "channel": item.channel,
            "peakValueG": item.peakValueG,
            "thresholdG": item.thresholdG,
            "speedKmph": item.speedKmph,
            "locationKm": item.locationKm,
            "latitude": item.latitude,
            "longitude": item.longitude,
            "alert": axis_color,
        })

        # Evaluate route-wise limits and send SMS
        if item.speedKmph > 80:
            route_info = await db.pg_pool.fetchrow(
                "SELECT r.id, r.name, r.vertical_limit, r.lateral_limit FROM trains t JOIN routes r ON t.route_id = r.id WHERE t.train_no = $1 LIMIT 1",
                train_no
            )
            if route_info:
                # Determine limit based on axis
                is_vertical = item.axis.upper() == "Z"
                is_lateral = item.axis.upper() == "Y"
                
                route_threshold = None
                if is_vertical:
                    route_threshold = route_info.get("vertical_limit")
                elif is_lateral:
                    route_threshold = route_info.get("lateral_limit")
                
                if route_threshold and item.peakValueG >= route_threshold:
                    # Check for 50m window filtering
                    # Look for higher peaks in the same axis within +/- 0.050 km (50m) in the last hour
                    recent_higher_peak = await db.pg_pool.fetchval(
                        """
                        SELECT 1 FROM alert_events 
                        WHERE train_no = $1 
                          AND axis = $2 
                          AND location_km BETWEEN $3 AND $4
                          AND peak_value_g >= $5
                          AND created_at >= NOW() - INTERVAL '1 hour'
                        LIMIT 1
                        """,
                        train_no, item.axis, item.locationKm - 0.050, item.locationKm + 0.050, item.peakValueG
                    )
                    
                    if not recent_higher_peak:
                        # Fetch relevant contacts
                        contacts = await db.pg_pool.fetch(
                            "SELECT id, mobile_number FROM contacts WHERE route_id = $1 AND sms_enabled = TRUE AND active = TRUE",
                            route_info.get("id")
                        )
                        # Assume track feature can be looked up or passed, for now use a placeholder
                        nearest_feature = "Unknown"
                        
                        msg = (f"apnaUABAMS Alert Train: {train_no} "
                               f"Route: {route_info.get('name')} "
                               f"Type: {'Vertical' if is_vertical else 'Lateral'} "
                               f"Acc. Value: {item.peakValueG:.2f}g "
                               f"Limit: {route_threshold}g "
                               f"Speed: {item.speedKmph:.2f}kmph "
                               f"GPS: {item.latitude},{item.longitude} "
                               f"Feature: {nearest_feature}")
                        
                        from app.utils import send_sms
                        for contact in contacts:
                            success, reason = send_sms(contact.get("mobile_number"), msg)
                            status = "SENT" if success else "FAILED"
                            
                            # Log the notification
                            await db.pg_pool.execute(
                                "INSERT INTO alert_notifications (alert_id, contact_id, notification_type, status, failure_reason) VALUES ($1, $2, $3, $4, $5)",
                                window_alert_id, contact.get("id"), "SMS", status, reason
                            )


    await mark_gateway_online(gateway_id, now)

    window_document = {
        "windowAlertId": window_alert_id,
        "gatewayId": gateway_id,
        "logicalGatewayId": data.logicalGatewayId,
        "trainNo": train_no,
        "sessionName": data.sessionName,
        "timestampUtcMs": data.timestampUtcMs,
        "startKm": data.startKm,
        "endKm": data.endKm,
        "speedKmph": data.speedKmph,
        "minSpeedKmph": data.minSpeedKmph,
        "maxSpeedKmph": data.maxSpeedKmph,
        "alertsCount": data.alertsCount,
        "alerts": axis_documents,
        "alert": overall_color,
        "createdAt": serialize(created_at),
    }
    return {
        "status": "success",
        "alert": overall_color,
        "windowAlertId": window_alert_id,
        "event": serialize(window_document),
    }


@router.get("/api/v1/trains/{train_no}/archives")
async def train_archives(train_no: str):
    real_rid = await db.pg_pool.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", train_no, f"TR_{train_no}")
    if real_rid:
        train_no = real_rid
    archives = await db.pg_pool.fetch("""
        SELECT gateway_id AS "gatewayId", sha256, received_at AS "receivedAt", train_id AS "trainId", session_name AS "sessionName", session_status AS "sessionStatus", size_bytes AS "sizeBytes", status, parse_warnings AS "parseWarnings"
        FROM archives WHERE train_id = $1 ORDER BY received_at DESC LIMIT 50
    """, train_no)
    return {"trainNo": train_no, "archives": serialize([dict(x) for x in archives])}


@router.get("/api/v1/trains/{train_no}/position")
async def train_position(train_no: str, gateway_id: str | None = None):
    real_rid = await db.pg_pool.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", train_no, f"TR_{train_no}")
    if real_rid:
        train_no = real_rid
    query = "SELECT gateway_id AS \"gatewayId\", latitude, longitude, position_mm AS \"positionMm\", speed AS \"speedKmph\", created_at AS \"createdAt\" FROM rms_records WHERE train_id = $1 AND gps_valid = TRUE AND latitude IS NOT NULL AND latitude != 0 AND longitude IS NOT NULL AND longitude != 0"
    args = [train_no]
    if gateway_id:
        query += " AND gateway_id = $2"
        args.append(gateway_id)
    query += " ORDER BY created_at DESC, position_mm DESC LIMIT 1"
    
    latest = await db.pg_pool.fetchrow(query, *args)
    if not latest:
        return {"trainNo": train_no, "gatewayId": gateway_id, "position": None}

    prev_query = "SELECT latitude, longitude FROM rms_records WHERE train_id = $1 AND gps_valid = TRUE AND latitude IS NOT NULL AND latitude != 0 AND longitude IS NOT NULL AND longitude != 0 AND gateway_id = $2 AND position_mm < $3 ORDER BY position_mm DESC LIMIT 1"
    previous = await db.pg_pool.fetchrow(prev_query, train_no, latest.get("gatewayId"), latest.get("positionMm", 0))
    
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

def _compute_color(item: dict) -> str:
    stored = item.get("color")
    if stored and stored in ("RED", "YELLOW", "GREEN"):
        return stored
    axes = item.get("axes") or {}
    if isinstance(axes, str):
        try:
            axes = json.loads(axes)
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


@router.get("/api/v1/map/alerts")
async def map_alerts(train_id: str, severity: str | None = None):
    where_clauses = [
        "train_no = $1",
        "session_status != 'archived'",
        "latitude IS NOT NULL",
        "latitude != 0",
        "longitude IS NOT NULL",
        "longitude != 0"
    ]
    args = [train_id]
    if severity and severity.upper() != "ALL":
        where_clauses.append(f"alert = ${len(args) + 1}")
        args.append(severity.upper())

    where_sql = " AND ".join(where_clauses)
    query = f"""
        SELECT train_no AS "trainNo", gateway_id AS "gatewayId", latitude, longitude,
               alert, peak_value_g AS "peakValueG", zone, division, section,
               created_at AS "createdAt"
        FROM alert_events
        WHERE {where_sql}
        ORDER BY created_at DESC
        LIMIT 500
    """
    alerts = await db.pg_pool.fetch(query, *args)
    
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


@router.get("/api/v1/map/rms")
async def map_rms(train_id: str, gateway_id: str | None = None):
    where_clauses = [
        "train_id = $1",
        "gps_valid = TRUE",
        "latitude IS NOT NULL",
        "latitude != 0",
        "longitude IS NOT NULL",
        "longitude != 0"
    ]
    args = [train_id]
    if gateway_id:
        where_clauses.append(f"gateway_id = ${len(args) + 1}")
        args.append(gateway_id)

    where_sql = " AND ".join(where_clauses)

    total_count = await db.pg_pool.fetchval(
        f"SELECT count(*) FROM rms_records WHERE {where_sql}", *args
    ) or 0

    if total_count == 0:
        return []

    target_points = 5000
    if total_count <= target_points:
        query = f"""
            SELECT train_id AS "trainId", gateway_id AS "gatewayId", session_name AS "sessionName",
                   latitude, longitude, axes, position_mm AS "positionMm", created_at AS "createdAt",
                   archive_sha256 AS "archiveSha256"
            FROM rms_records
            WHERE {where_sql}
            ORDER BY gateway_id ASC, position_mm ASC
        """
        rows = await db.pg_pool.fetch(query, *args)
    else:
        step = max(1, total_count // target_points)
        step_param_idx = len(args) + 1
        query_args = list(args) + [step]
        query = f"""
            WITH base AS (
                SELECT 
                    train_id AS "trainId", gateway_id AS "gatewayId", session_name AS "sessionName",
                    latitude, longitude, axes, position_mm AS "positionMm", created_at AS "createdAt",
                    archive_sha256 AS "archiveSha256",
                    ROW_NUMBER() OVER (PARTITION BY gateway_id ORDER BY position_mm ASC) as rn,
                    COUNT(*) OVER (PARTITION BY gateway_id) as total_gw
                FROM rms_records
                WHERE {where_sql}
            )
            SELECT "trainId", "gatewayId", "sessionName", latitude, longitude, axes, "positionMm", "createdAt", "archiveSha256"
            FROM base
            WHERE rn % ${step_param_idx} = 0 OR rn = 1 OR rn = total_gw
            ORDER BY "gatewayId" ASC, "positionMm" ASC
        """
        rows = await db.pg_pool.fetch(query, *query_args)

    records = []
    last_coords_by_gw = {}
    for item in rows:
        g_id = item.get("gatewayId") or "unknown"
        lat = round(float(item["latitude"]), 5)
        lon = round(float(item["longitude"]), 5)
        last_coord = last_coords_by_gw.get(g_id)
        color = _compute_color(item)

        if last_coord != (lat, lon):
            records.append({
                "train_id": item.get("trainId"),
                "gateway_id": item.get("gatewayId"),
                "session": item.get("sessionName"),
                "lat": item.get("latitude"),
                "lon": item.get("longitude"),
                "color": color,
                "peak_g": 0,
                "position_mm": item.get("positionMm"),
                "created_at": serialize(item.get("createdAt")),
            })
            last_coords_by_gw[g_id] = (lat, lon)
        else:
            if color in ("RED", "YELLOW") and records and records[-1]["gateway_id"] == g_id:
                records[-1]["color"] = color

    return records


@router.post("/api/v1/data/reset")
async def reset_bad_data(
    data: TargetedResetRequest,
    request: Request,
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
):
    if not x_admin_key:
        raise HTTPException(status_code=401, detail="Missing admin reset key or password")
        
    legacy_key_ok = bool(settings.get("admin_reset_key") and compare_digest(x_admin_key, settings["admin_reset_key"]))
    password_ok = bool(is_operator_authenticated(request) and compare_digest(x_admin_key, settings["admin_password"]))
    
    if not legacy_key_ok and not password_ok:
        raise HTTPException(status_code=403, detail="Invalid admin reset key or password")
    
    if not data.gatewayId or data.gatewayId == "All Gateways" or not data.startTime or not data.endTime:
        raise HTTPException(status_code=400, detail="Please select a specific Gateway. or Please provide both Start Time and End Time.")
    if (data.latitude is not None and data.longitude is None) or (data.latitude is None and data.longitude is not None):
        raise HTTPException(status_code=400, detail="Latitude and Longitude must be provided together as a pair")

    now = utc_now()
    
    args = []
    conds = []
    
    if data.trainNo and data.trainNo.upper() != "ALL":
        args.append(data.trainNo)
        conds.append(f"train_id = ${len(args)}")
        
    if data.gatewayId and data.gatewayId != "All Gateways":
        args.append(data.gatewayId)
        conds.append(f"gateway_id = ${len(args)}")
        
    if data.startTime:
        args.append(data.startTime)
        conds.append(f"created_at >= ${len(args)}")
        
    if data.endTime:
        args.append(data.endTime)
        conds.append(f"created_at <= ${len(args)}")
        
    where_peak = " AND ".join(conds) if conds else "1=1"
    where_alert = where_peak.replace("train_id", "train_no")
    where_fault = where_peak
    
    # If location is provided, add to peak and alert, but not fault
    if data.latitude is not None and data.longitude is not None:
        lat = data.latitude
        lon = data.longitude
        radius = data.radiusMeters
        lat_delta = radius / 111320.0
        lon_delta = radius / (111320.0 * cos(radians(lat)))
        
        args.append(lat - lat_delta)
        args.append(lat + lat_delta)
        cond_lat = f"latitude BETWEEN ${len(args)-1} AND ${len(args)}"
        
        args.append(lon - lon_delta)
        args.append(lon + lon_delta)
        cond_lon = f"longitude BETWEEN ${len(args)-1} AND ${len(args)}"
        
        where_peak += f" AND {cond_lat} AND {cond_lon}"
        where_alert += f" AND {cond_lat} AND {cond_lon}"

    res_peak = await db.pg_pool.execute(f"DELETE FROM peak_records WHERE {where_peak}", *args)
    res_rms = await db.pg_pool.execute(f"DELETE FROM rms_records WHERE {where_peak}", *args)
    res_alert = await db.pg_pool.execute(f"DELETE FROM alert_events WHERE {where_alert}", *args)
    
    # Faults don't have latitude/longitude, we use where_fault
    res_fault = await db.pg_pool.execute(f"DELETE FROM fault_records WHERE {where_fault}", *args[:len(conds)])
    
    # Log activity
    username = operator_username(request) or "admin"
    ip = client_ip(request) or "unknown"
    await db.pg_pool.execute(
        "INSERT INTO activity_logs (username, page, action, error_message, ip_address) VALUES ($1, $2, $3, $4, $5)",
        username, "/dashboard", f"Data Cleanup - {data.trainNo}", "", ip
    )
    
    cleanup_stats = {
        "peak_records": res_peak,
        "rms_records": res_rms,
        "alert_events": res_alert,
        "fault_records": res_fault
    }

    return {"status": "success", "message": "Targeted data removed", "cleanup": cleanup_stats}


@router.post("/api/v1/sessions/reset")
async def reset_session(
    data: ResetSessionRequest,
    request: Request,
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
):
    legacy_key_ok = bool(x_admin_key and settings.get("admin_reset_key") and compare_digest(x_admin_key, settings["admin_reset_key"]))
    password_ok = bool(data.adminPassword and is_operator_authenticated(request) and compare_digest(data.adminPassword, settings["admin_password"]))
    
    if not legacy_key_ok and not password_ok:
        raise HTTPException(status_code=403, detail="Invalid admin reset key or password")
        
    now = utc_now()
    session_id = f"{data.trainNo}-{int(now.timestamp())}"
    await db.pg_pool.execute("INSERT INTO sessions (train_no, session_name, status, created_at) VALUES ($1, $2, 'active', $3)", data.trainNo, session_id, now)

    # Delete everything for this train!
    if data.trainNo and data.trainNo.upper() != "ALL":
        await db.pg_pool.execute("DELETE FROM peak_records WHERE train_id = $1", data.trainNo)
        await db.pg_pool.execute("DELETE FROM rms_records WHERE train_id = $1", data.trainNo)
        await db.pg_pool.execute("DELETE FROM fault_records WHERE train_id = $1", data.trainNo)
        await db.pg_pool.execute("DELETE FROM alert_events WHERE train_no = $1", data.trainNo)
        await db.pg_pool.execute("DELETE FROM window_alerts WHERE train_no = $1", data.trainNo)
        await db.pg_pool.execute("DELETE FROM archives WHERE gateway_id IN (SELECT gateway_id FROM gateways WHERE train_id = $1)", data.trainNo)

    # Use gateway_train_assignments (new decoupled table) with gateway_status as fallback
    gta_gateways = await db.pg_pool.fetch(
        'SELECT DISTINCT gateway_id AS "gatewayId" FROM gateway_train_assignments WHERE train_id = $1 AND is_active = true',
        data.trainNo
    )
    gateways = gta_gateways
    if not gateways:
        gateways = await db.pg_pool.fetch(
            'SELECT DISTINCT gateway_id AS "gatewayId" FROM gateway_status WHERE train_id = $1',
            data.trainNo
        )
    queued_commands = []
    for gateway in gateways:
        gateway_id = gateway.get("gatewayId")
        if not gateway_id:
            continue
        command_id = f"cmd-{uuid.uuid4()}"
        await db.pg_pool.execute("UPDATE gateway_commands SET status = 'superseded', completed_at = $1, result = $2::jsonb WHERE gateway_id = $3 AND type = 'reset' AND status IN ('pending', 'delivered')", now, json.dumps({"status": "superseded", "details": {"supersededBy": command_id}}), gateway_id)
        await db.pg_pool.execute("INSERT INTO gateway_commands (command_id, gateway_id, type, status, delivery_count, created_at) VALUES ($1, $2, 'reset', 'pending', 0, $3)", command_id, gateway_id, now)
        queued_commands.append({"gatewayId": gateway_id, "commandId": command_id})

    # Log activity
    username = operator_username(request) or "admin"
    ip = client_ip(request) or "unknown"
    await db.pg_pool.execute(
        "INSERT INTO activity_logs (username, page, action, error_message, ip_address) VALUES ($1, $2, $3, $4, $5)",
        username, "/dashboard", f"Reset Session - {data.trainNo}", "", ip
    )

    session_response = {"sessionId": session_id, "trainNo": data.trainNo, "status": "active", "createdAt": now}
    return {"status": "success", "message": "New session started and reset commands queued", "session": serialize(session_response), "resetCommands": queued_commands}


class RepeatedAlarmRequest(BaseModel):
    rid: str | None = None
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

@router.post("/api/reports/repeated-alarm/load")
async def load_repeated_alarm_report(data: RepeatedAlarmRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    from_dt = parse_local_datetime(data.fromDate)
    to_dt = parse_local_datetime(data.toDate)
    
    query = """
        SELECT train_no AS rid, COUNT(*) as count, (array_agg(latitude))[1] as latitude, (array_agg(longitude))[1] as longitude
        FROM alert_events WHERE created_at >= $1 AND created_at <= $2
    """
    args = [from_dt, to_dt]
    rid = data.rid.strip() if data.rid else ""
    
    if rid and rid.upper() != "ALL":
        real_rid = await db.pg_pool.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", rid, f"TR_{rid}")
        if not real_rid:
            raise HTTPException(status_code=404, detail="Train not found")
        rid = real_rid
        data.rid = real_rid
    if rid and rid.upper() != "ALL":
        args.append(rid)
        query += f" AND train_no = ${len(args)}"
    query += " GROUP BY train_no ORDER BY count DESC LIMIT 1000"
    results = await db.pg_pool.fetch(query, *args)
    
    rows = []
    for r in results:
        train_no = r.get("rid")
        if train_no:
            lat = r.get("latitude")
            lon = r.get("longitude")
            loc_str = f"{lat:.4f}, {lon:.4f}" if (lat is not None and lon is not None) else "-"
            rows.append({"rid": train_no, "count": r.get("count", 0), "location": loc_str})
            
    return {"totalRollingStocks": len(rows), "rows": rows}

@router.post("/api/reports/repeated-alarm/export/csv")
async def export_repeated_alarm_csv(data: RepeatedAlarmRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    res = await load_repeated_alarm_report(data, request)
    rows = res["rows"]
    csv_lines = ["RID,Count,Location"]
    for r in rows:
        csv_lines.append(f"{r['rid']},{r['count']},{r['location']}")
    return Response(content="\n".join(csv_lines), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=RepeatedAlarms.csv"})

@router.post("/api/reports/repeated-alarm/export/excel")
async def export_repeated_alarm_excel(data: RepeatedAlarmRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    res = await load_repeated_alarm_report(data, request)
    rows = res["rows"]
    xml_parts = ['<?xml version="1.0"?>', '<?mso-application progid="Excel.Sheet"?>', '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"', ' xmlns:o="urn:schemas-microsoft-com:office:origin"', ' xmlns:x="urn:schemas-microsoft-com:office:excel"', ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"', ' xmlns:html="http://www.w3.org/TR/REC-html40">', ' <Worksheet ss:Name="RepeatedAlarms">', '  <Table>', '   <Row>', '    <Cell><Data ss:Type="String">RID</Data></Cell>', '    <Cell><Data ss:Type="String">Count</Data></Cell>', '    <Cell><Data ss:Type="String">Location</Data></Cell>', '   </Row>']
    for r in rows:
        xml_parts.append(f'   <Row>\n    <Cell><Data ss:Type="String">{r["rid"]}</Data></Cell>\n    <Cell><Data ss:Type="Number">{r["count"]}</Data></Cell>\n    <Cell><Data ss:Type="String">{r["location"]}</Data></Cell>\n   </Row>')
    xml_parts.extend(['  </Table>', ' </Worksheet>', '</Workbook>'])
    return Response(content="\n".join(xml_parts), media_type="application/vnd.ms-excel", headers={"Content-Disposition": "attachment; filename=RepeatedAlarms.xls"})

@router.post("/api/reports/repeated-alarm/export/pdf")
async def export_repeated_alarm_pdf(data: RepeatedAlarmRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    res = await load_repeated_alarm_report(data, request)
    rows = res["rows"]
    headers = ["RID", "Count", "Location"]
    data_rows = [[r["rid"], str(r["count"]), r["location"]] for r in rows]
    pdf_bytes = generate_pdf_report("Repeated Alarms Report", headers, data_rows)
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=RepeatedAlarms.pdf"})

@router.post("/api/reports/alarm-log/load")
async def load_alarm_log_report(data: AlarmLogRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    from_dt = parse_local_datetime(data.fromDate)
    to_dt = parse_local_datetime(data.toDate)
    
    query = "SELECT id, gateway_id, train_no, latitude, longitude, alert, created_at FROM alert_events WHERE created_at >= $1 AND created_at <= $2"
    args = [from_dt, to_dt]
    
    rid = data.rid.strip() if data.rid else ""
    
    if rid and rid.upper() != "ALL":
        real_rid = await db.pg_pool.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", rid, f"TR_{rid}")
        if not real_rid:
            raise HTTPException(status_code=404, detail="Train not found")
        rid = real_rid
        data.rid = real_rid
    if rid and rid.upper() != "ALL":
        args.append(rid)
        query += f" AND train_no = ${len(args)}"
        
    if data.alarmType == "Critical":
        query += " AND alert = 'RED'"
    elif data.alarmType == "Warning":
        query += " AND alert = 'YELLOW'"
    elif data.alarmType == "Normal":
        query += " AND alert = 'GREEN'"
        
    query += " ORDER BY created_at DESC LIMIT 2000"
    alerts = await db.pg_pool.fetch(query, *args)
    
    rows = []
    total_records = len(alerts)
    critical_count = 0
    Warning_count = 0
    normal_count = 0
    
    for alert_doc in alerts:
        col_alert = alert_doc.get("alert", "GREEN")
        if col_alert == "RED":
            critical_count += 1
        elif col_alert == "YELLOW":
            Warning_count += 1
        else:
            normal_count += 1
            
        dt = alert_doc.get("created_at")
        date_str = dt.strftime("%d-%m-%Y") if dt else "-"
        time_str = dt.strftime("%H:%M:%S") if dt else "-"
        
        lat = alert_doc.get("latitude")
        lon = alert_doc.get("longitude")
        loc_str = f"{lat:.4f}, {lon:.4f}" if (lat is not None and lon is not None) else "-"
        
        rows.append({
            "id": str(alert_doc.get("id") or ""),
            "alarmDate": date_str,
            "alarmTime": time_str,
            "machineName": alert_doc.get("gateway_id") or "-",
            "train": alert_doc.get("train_no") or "-",
            "location": loc_str,
            "alertColor": col_alert
        })
        
    return {
        "summary": {
            "totalAlarmCount": total_records,
            "criticalAlarmCount": critical_count,
            "WarningAlarmCount": Warning_count,
            "normalAlarmCount": normal_count
        },
        "rows": rows,
        "recordsTruncated": False
    }

@router.post("/api/reports/alarm-log/export/csv")
async def export_alarm_log_csv(data: AlarmLogRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    res = await load_alarm_log_report(data, request)
    rows = res["rows"]
    headers = ["Date", "Time", "Machine", "Train", "Location"]
    csv_lines = [",".join(headers)]
    for r in rows:
        csv_lines.append(",".join([r["alarmDate"], r["alarmTime"], r["machineName"], r["train"], r["location"]]))
    return Response(content="\n".join(csv_lines), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=AlarmLog.csv"})

@router.post("/api/reports/alarm-log/export/excel")
async def export_alarm_log_excel(data: AlarmLogRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    res = await load_alarm_log_report(data, request)
    rows = res["rows"]
    headers = ["Date", "Time", "Machine", "Train", "Location"]
    xml_parts = ['<?xml version="1.0"?>', '<?mso-application progid="Excel.Sheet"?>', '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"', ' xmlns:o="urn:schemas-microsoft-com:office:origin"', ' xmlns:x="urn:schemas-microsoft-com:office:excel"', ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"', ' xmlns:html="http://www.w3.org/TR/REC-html40">', ' <Worksheet ss:Name="AlarmLog">', '  <Table>', '   <Row>']
    for h in headers:
        xml_parts.append(f'    <Cell><Data ss:Type="String">{h}</Data></Cell>')
    xml_parts.append('   </Row>')
    for r in rows:
        xml_parts.append(f'   <Row>\n    <Cell><Data ss:Type="String">{r["alarmDate"]}</Data></Cell>\n    <Cell><Data ss:Type="String">{r["alarmTime"]}</Data></Cell>\n    <Cell><Data ss:Type="String">{r["machineName"]}</Data></Cell>\n    <Cell><Data ss:Type="String">{r["train"]}</Data></Cell>\n    <Cell><Data ss:Type="String">{r["location"]}</Data></Cell>\n   </Row>')
    xml_parts.extend(['  </Table>', ' </Worksheet>', '</Workbook>'])
    return Response(content="\n".join(xml_parts), media_type="application/vnd.ms-excel", headers={"Content-Disposition": "attachment; filename=AlarmLog.xls"})

@router.post("/api/reports/alarm-log/export/pdf")
async def export_alarm_log_pdf(data: AlarmLogRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    res = await load_alarm_log_report(data, request)
    rows = res["rows"]
    headers = ["Date", "Time", "Machine", "Train", "Location"]
    data_rows = [[r["alarmDate"], r["alarmTime"], r["machineName"], r["train"], r["location"]] for r in rows]
    pdf_bytes = generate_pdf_report("Alarm Log Report", headers, data_rows)
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=AlarmLog.pdf"})

@router.post("/api/reports/alerts/{alert_id}/feedback")
async def update_alert_feedback(alert_id: str, data: FeedbackUpdateRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    try:
        obj_id = int(alert_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Alert ID format")
    await db.pg_pool.execute("UPDATE alert_events SET enroute_diagnosis = $1, enroute_action = $2, depot_diagnosis = $3 WHERE id = $4", data.enrouteDiagnosis, data.enrouteAction, data.depotDiagnosis, obj_id)
    return {"status": "success", "message": "Feedback updated successfully"}

class GraphDataRequest(BaseModel):
    rid: str
    fromDate: str
    toDate: str
    metric: str

@router.post("/api/reports/graph/load")
async def load_graph_report(data: GraphDataRequest, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
        
    from_dt = parse_local_datetime(data.fromDate)
    to_dt = parse_local_datetime(data.toDate)
    
    points = []
    if data.metric == "RMS":
        records = await db.pg_pool.fetch("SELECT created_at, position_mm, speed, latitude, longitude, axes, al_x_g, al_y_g, al_z_g, ar_x_g, ar_y_g, ar_z_g, bg_x_g, bg_y_g, bg_z_g FROM rms_records WHERE train_id = $1 AND created_at >= $2 AND created_at <= $3 ORDER BY position_mm ASC LIMIT 1000", data.rid, from_dt, to_dt)
        for r in records:
            dt = r.get("created_at")
            timestamp_str = dt.strftime("%d-%m-%Y %H:%M:%S") if dt else "-"
            pos_mm = r.get("position_mm") or 0
            pos_km = round(pos_mm / 1000000.0, 4)
            
            axes_data = {}
            axes_dict = r.get("axes") if isinstance(r.get("axes"), dict) else {}
            if isinstance(axes_dict, str):
                try: axes_dict = json.loads(axes_dict)
                except: axes_dict = {}
            for axis_name in AXIS_NAMES:
                val = r.get(f"{axis_name}_g")
                if val is None:
                    val = axes_dict.get(f"{axis_name}_g")
                axes_data[axis_name] = float(val) if val is not None else 0.0
                
            points.append({
                "timestamp": timestamp_str,
                "speed": r.get("speed") or 0.0,
                "positionKm": pos_km,
                "latitude": r.get("latitude"),
                "longitude": r.get("longitude"),
                "axes": axes_data
            })
    else:
        records = await db.pg_pool.fetch("SELECT created_at, position_mm, speed_kmph, latitude, longitude, axes FROM peak_records WHERE train_id = $1 AND created_at >= $2 AND created_at <= $3 ORDER BY position_mm ASC LIMIT 1000", data.rid, from_dt, to_dt)
        for r in records:
            dt = r.get("created_at")
            timestamp_str = dt.strftime("%d-%m-%Y %H:%M:%S") if dt else "-"
            pos_mm = r.get("position_mm") or 0
            pos_km = round(pos_mm / 1000000.0, 4)
            
            axes_data = {}
            axes_dict = r.get("axes", {})
            if isinstance(axes_dict, str):
                try: axes_dict = json.loads(axes_dict)
                except: axes_dict = {}
            for axis_name in AXIS_NAMES:
                axis_obj = axes_dict.get(axis_name) or {}
                axes_data[axis_name] = axis_obj.get("peakValueG") or 0.0
                
            points.append({
                "timestamp": timestamp_str,
                "speed": r.get("speed_kmph") or 0.0,
                "positionKm": pos_km,
                "latitude": r.get("latitude"),
                "longitude": r.get("longitude"),
                "axes": axes_data
            })
            
    rolling_stock_type = "C"
    train_type = "Goods"
    if points and data.rid:
        if "LH" in data.rid.upper() or "EXP" in data.rid.upper():
            train_type = "Passenger LHB"
            rolling_stock_type = "LHB"
            
    return {
        "rollingStockId": data.rid,
        "trainType": train_type,
        "rollingStockType": rolling_stock_type,
        "points": points
    }



