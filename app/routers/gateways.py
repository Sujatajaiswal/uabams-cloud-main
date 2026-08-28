from typing import Annotated
from fastapi import APIRouter, Request, HTTPException, Header, Response
from datetime import UTC
import json
import uuid
from hashlib import sha256

from app.database import db
from app.models import GatewayConnectionRequest, GatewayConnectionResponse, CalibrationUpdateRequest
from app.utils import (
    utc_now, serialize, canonical_json_bytes, absolute_cloud_url,
    require_gateway_access, is_operator_authenticated, 
)
from dateutil.parser import parse as parse_datetime

router = APIRouter(tags=["gateways"])

@router.post("/api/v1/gateway/demo-connect", response_model=GatewayConnectionResponse)
async def gateway_demo_connect(data: GatewayConnectionRequest):
    query = """
        SELECT gateway_id AS "gatewayId", train_id AS "trainId", status
        FROM gateways
        WHERE gateway_serial = $1 OR gateway_id = $1
    """
    gateway = await db.pg_pool.fetchrow(query, data.serialNo)
    
    if not gateway:
        return GatewayConnectionResponse(
            status="denied",
            message=f"Access denied: Serial number or Gateway ID '{data.serialNo}' is not registered in the cloud database.",
            gatewayId=None,
            trainId=None
        )
        
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

@router.get("/api/v1/calibration/{gateway_id}")
async def get_calibration(
    gateway_id: str,
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
):
    await require_gateway_access(request, gateway_id, allow_operator=True)

    query = """
        SELECT version,
               adxl_left, adxl_right, bogie, encoder
        FROM calibration_versions
        WHERE gateway_id = $1
        ORDER BY version DESC LIMIT 1
    """
    calibration = await db.pg_pool.fetchrow(query, gateway_id)
    
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

    if not calibration:
        return {
            "gatewayId": gateway_id,
            "version": 0,
            "adxl_left": default_adxl,
            "adxl_right": default_adxl,
            "bogie": default_bogie,
            "encoder": default_encoder,
        }

    def parse_jsonb(val):
        if not val:
            return {}
        if isinstance(val, str):
            return json.loads(val)
        return dict(val)

    adxl_left_data = parse_jsonb(calibration.get("adxl_left"))
    adxl_right_data = parse_jsonb(calibration.get("adxl_right"))
    bogie_data = parse_jsonb(calibration.get("bogie"))
    encoder_data = parse_jsonb(calibration.get("encoder"))

    return {
        "gatewayId": gateway_id,
        "version": calibration.get("version"),
        "adxl_left": {**default_adxl, **adxl_left_data},
        "adxl_right": {**default_adxl, **adxl_right_data},
        "bogie": {**default_bogie, **bogie_data},
        "encoder": {**default_encoder, **encoder_data},
    }

@router.post("/api/v1/calibration/{gateway_id}")
async def save_calibration(
    gateway_id: str,
    data: CalibrationUpdateRequest,
    request: Request,
):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Operator login required")
    if not any((data.adxlLeft, data.adxlRight, data.bogie, data.encoder)):
        raise HTTPException(status_code=400, detail="At least one calibration section is required")

    gateway = await db.pg_pool.fetchrow("SELECT train_id FROM gateways WHERE gateway_id = $1", gateway_id)
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not registered")
    train_id = gateway.get("train_id")

    existing_query = """
        SELECT version, adxl_left, adxl_right, bogie, encoder
        FROM calibration_versions
        WHERE gateway_id = $1
        ORDER BY version DESC LIMIT 1
    """
    existing = await db.pg_pool.fetchrow(existing_query, gateway_id) or {}

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

    def parse_jsonb(val):
        if not val:
            return {}
        if isinstance(val, str):
            return json.loads(val)
        return dict(val)

    current_adxl_left = {**default_adxl, **parse_jsonb(existing.get("adxl_left"))}
    current_adxl_right = {**default_adxl, **parse_jsonb(existing.get("adxl_right"))}
    current_bogie = {**default_bogie, **parse_jsonb(existing.get("bogie"))}
    current_encoder = {**default_encoder, **parse_jsonb(existing.get("encoder"))}

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

    calibration_sections = {}
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

    async with db.pg_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                UPDATE gateway_commands
                SET status = 'superseded',
                    completed_at = $1,
                    result = $2::jsonb
                WHERE gateway_id = $3
                  AND type = 'calibration_update'
                  AND status IN ('pending', 'delivered')
            """, now, json.dumps({"status": "superseded", "details": {"supersededBy": command_id}}), gateway_id)

            await conn.execute("""
                INSERT INTO calibration_versions (
                    train_id, gateway_id, version,
                    adxl_left_offset_x, adxl_left_offset_y, adxl_left_offset_z,
                    adxl_right_offset_x, adxl_right_offset_y, adxl_right_offset_z,
                    iis_offset_x, iis_offset_y, iis_offset_z,
                    imu_accel_offset_x, imu_accel_offset_y, imu_accel_offset_z,
                    imu_gyro_offset_x, imu_gyro_offset_y, imu_gyro_offset_z,
                    wheel_diameter_m, encoder_ppr, spatial_interval_mm, trigger_start_speed_kmph,
                    adxl_left, adxl_right, bogie, encoder, created_at
                ) VALUES (
                    $1, $2, $3,
                    $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22,
                    $23::jsonb, $24::jsonb, $25::jsonb, $26::jsonb, $27
                )
            """,
                document["train_id"], document["gateway_id"], document["version"],
                document["adxl_left_offset_x"], document["adxl_left_offset_y"], document["adxl_left_offset_z"],
                document["adxl_right_offset_x"], document["adxl_right_offset_y"], document["adxl_right_offset_z"],
                document["iis_offset_x"], document["iis_offset_y"], document["iis_offset_z"],
                document["imu_accel_offset_x"], document["imu_accel_offset_y"], document["imu_accel_offset_z"],
                document["imu_gyro_offset_x"], document["imu_gyro_offset_y"], document["imu_gyro_offset_z"],
                document["wheel_diameter_m"], document["encoder_ppr"], document["spatial_interval_mm"], document["trigger_start_speed_kmph"],
                json.dumps(document["adxl_left"]), json.dumps(document["adxl_right"]), json.dumps(document["bogie"]), json.dumps(document["encoder"]),
                now
            )

            await conn.execute("""
                INSERT INTO calibrations (
                    train_id, gateway_id, version,
                    adxl_left_offset_x, adxl_left_offset_y, adxl_left_offset_z,
                    adxl_right_offset_x, adxl_right_offset_y, adxl_right_offset_z,
                    iis_offset_x, iis_offset_y, iis_offset_z,
                    imu_accel_offset_x, imu_accel_offset_y, imu_accel_offset_z,
                    imu_gyro_offset_x, imu_gyro_offset_y, imu_gyro_offset_z,
                    wheel_diameter_m, encoder_ppr, spatial_interval_mm, trigger_start_speed_kmph,
                    adxl_left, adxl_right, bogie, encoder, updated_at
                ) VALUES (
                    $1, $2, $3,
                    $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22,
                    $23::jsonb, $24::jsonb, $25::jsonb, $26::jsonb, $27
                ) ON CONFLICT (gateway_id) DO UPDATE SET
                    train_id = EXCLUDED.train_id,
                    version = EXCLUDED.version,
                    adxl_left_offset_x = EXCLUDED.adxl_left_offset_x, adxl_left_offset_y = EXCLUDED.adxl_left_offset_y, adxl_left_offset_z = EXCLUDED.adxl_left_offset_z,
                    adxl_right_offset_x = EXCLUDED.adxl_right_offset_x, adxl_right_offset_y = EXCLUDED.adxl_right_offset_y, adxl_right_offset_z = EXCLUDED.adxl_right_offset_z,
                    iis_offset_x = EXCLUDED.iis_offset_x, iis_offset_y = EXCLUDED.iis_offset_y, iis_offset_z = EXCLUDED.iis_offset_z,
                    imu_accel_offset_x = EXCLUDED.imu_accel_offset_x, imu_accel_offset_y = EXCLUDED.imu_accel_offset_y, imu_accel_offset_z = EXCLUDED.imu_accel_offset_z,
                    imu_gyro_offset_x = EXCLUDED.imu_gyro_offset_x, imu_gyro_offset_y = EXCLUDED.imu_gyro_offset_y, imu_gyro_offset_z = EXCLUDED.imu_gyro_offset_z,
                    wheel_diameter_m = EXCLUDED.wheel_diameter_m, encoder_ppr = EXCLUDED.encoder_ppr, spatial_interval_mm = EXCLUDED.spatial_interval_mm, trigger_start_speed_kmph = EXCLUDED.trigger_start_speed_kmph,
                    adxl_left = EXCLUDED.adxl_left, adxl_right = EXCLUDED.adxl_right, bogie = EXCLUDED.bogie, encoder = EXCLUDED.encoder, updated_at = EXCLUDED.updated_at
            """,
                document["train_id"], document["gateway_id"], document["version"],
                document["adxl_left_offset_x"], document["adxl_left_offset_y"], document["adxl_left_offset_z"],
                document["adxl_right_offset_x"], document["adxl_right_offset_y"], document["adxl_right_offset_z"],
                document["iis_offset_x"], document["iis_offset_y"], document["iis_offset_z"],
                document["imu_accel_offset_x"], document["imu_accel_offset_y"], document["imu_accel_offset_z"],
                document["imu_gyro_offset_x"], document["imu_gyro_offset_y"], document["imu_gyro_offset_z"],
                document["wheel_diameter_m"], document["encoder_ppr"], document["spatial_interval_mm"], document["trigger_start_speed_kmph"],
                json.dumps(document["adxl_left"]), json.dumps(document["adxl_right"]), json.dumps(document["bogie"]), json.dumps(document["encoder"]),
                now
            )

            await conn.execute("""
                INSERT INTO gateway_commands (
                    command_id, gateway_id, type, status, version,
                    payload_url, sha256, payload, delivery_count, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
            """,
                command_id, gateway_id, "calibration_update", "pending", version,
                payload_path, payload_sha256, json.dumps(calibration_payload), 0, now
            )

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

@router.get("/api/v1/calibration/{gateway_id}/payload/{command_id}")
async def get_calibration_payload(
    gateway_id: str,
    command_id: str,
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
):
    await require_gateway_access(request, gateway_id)

    query = """
        SELECT payload, sha256
        FROM gateway_commands
        WHERE command_id = $1 AND gateway_id = $2 AND type = 'calibration_update'
    """
    command = await db.pg_pool.fetchrow(query, command_id, gateway_id)
    if not command:
        raise HTTPException(status_code=404, detail="Calibration command not found")

    payload_str = command.get("payload")
    if not payload_str:
        raise HTTPException(status_code=404, detail="Calibration payload not found for this command")

    payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str

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

@router.get("/api/v1/commands/{gateway_id}")
async def list_gateway_commands(gateway_id: str, request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Operator login required")

    query = """
        SELECT command_id AS "commandId",
               gateway_id AS "gatewayId",
               type, status, version,
               payload_url AS "payloadUrl",
               sha256, payload, result,
               created_at AS "createdAt",
               delivered_at AS "deliveredAt",
               last_delivered_at AS "lastDeliveredAt",
               delivery_count AS "deliveryCount",
               completed_at AS "completedAt"
        FROM gateway_commands
        WHERE gateway_id = $1
        ORDER BY created_at DESC
        LIMIT 50
    """
    commands_rows = await db.pg_pool.fetch(query, gateway_id)
    
    commands = []
    for r in commands_rows:
        d = dict(r)
        if isinstance(d.get("payload"), str):
            d["payload"] = json.loads(d["payload"])
        if isinstance(d.get("result"), str):
            d["result"] = json.loads(d["result"])
        commands.append(d)

    return serialize(commands)

@router.get("/api/v1/trains/{train_no}/gateways/{gateway_id}/details")
async def gateway_details(train_no: str, gateway_id: str):
    gateway_query = """
        SELECT gateway_id AS "gatewayId", train_id AS "trainId", online, last_heartbeat AS "lastHeartbeat",
               adxl_state AS "adxlState", adxl_uptime AS "adxlUptime", adxl_faults AS "adxlFaults",
               adxl_fw_version AS "adxlFwVersion", adxl_cal_version AS "adxlCalVersion",
               encoder_state AS "encoderState", encoder_uptime AS "encoderUptime",
               encoder_faults AS "encoderFaults", encoder_fw_version AS "encoderFwVersion",
               encoder_cal_version AS "encoderCalVersion", updated_at AS "updatedAt"
        FROM gateway_status WHERE gateway_id = $1
    """
    gateway_row = await db.pg_pool.fetchrow(gateway_query, gateway_id)
    gateway = dict(gateway_row) if gateway_row else None

    if gateway:
        lh = gateway.get("lastHeartbeat")
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

    async with db.pg_pool.acquire() as conn:
        archive_count = await conn.fetchval("SELECT COUNT(*) FROM archives WHERE train_id = $1 AND gateway_id = $2", train_no, gateway_id)
        alert_count = await conn.fetchval("SELECT COUNT(*) FROM alert_events WHERE train_no = $1 AND gateway_id = $2 AND session_status != 'archived'", train_no, gateway_id)
        critical_count = await conn.fetchval("SELECT COUNT(*) FROM alert_events WHERE train_no = $1 AND gateway_id = $2 AND alert = 'RED' AND session_status != 'archived'", train_no, gateway_id)
        rms_count = await conn.fetchval("SELECT COUNT(*) FROM rms_records WHERE train_id = $1 AND gateway_id = $2", train_no, gateway_id)
        peak_count = await conn.fetchval("SELECT COUNT(*) FROM peak_records WHERE train_id = $1 AND gateway_id = $2", train_no, gateway_id)
        fault_count = await conn.fetchval("SELECT COUNT(*) FROM fault_records WHERE train_id = $1 AND gateway_id = $2", train_no, gateway_id)
        
        latest_alert_row = await conn.fetchrow("""
            SELECT id, train_no AS "trainNo", gateway_id AS "gatewayId", alert_type AS "alertType",
                   latitude, longitude, position_mm AS "positionMm", session_name AS "sessionName",
                   archive_sha256 AS "archiveSha256", source, peak_axis AS "peakAxis", peak_value_g AS "peakValueG",
                   speed_kmph AS "speedKmph", alert, session_status AS "sessionStatus", zone, division, section,
                   archived_at AS "archivedAt", created_at AS "createdAt"
            FROM alert_events WHERE train_no = $1 AND gateway_id = $2 AND session_status != 'archived' 
            ORDER BY CASE alert WHEN 'RED' THEN 1 WHEN 'YELLOW' THEN 2 WHEN 'GREEN' THEN 3 ELSE 4 END ASC, created_at DESC LIMIT 1
        """, train_no, gateway_id)
        latest_alert = dict(latest_alert_row) if latest_alert_row else None

        latest_archive_row = await conn.fetchrow("""
            SELECT id, gateway_id AS "gatewayId", sha256, received_at AS "receivedAt", train_id AS "trainId",
                   session_name AS "sessionName", session_status AS "sessionStatus", size_bytes AS "sizeBytes",
                   status, parse_warnings AS "parseWarnings"
            FROM archives WHERE train_id = $1 AND gateway_id = $2 ORDER BY received_at DESC LIMIT 1
        """, train_no, gateway_id)
        latest_archive = dict(latest_archive_row) if latest_archive_row else None

        latest_rms_row = await conn.fetchrow("""
            SELECT id, train_id AS "trainId", gateway_id AS "gatewayId", session_name AS "sessionName",
                   archive_sha256 AS "archiveSha256", latitude, longitude, gps_valid AS "gpsValid", bearing,
                   speed, position_mm AS "positionMm", axes, al_x_g, al_y_g, al_z_g, ar_x_g, ar_y_g, ar_z_g,
                   bg_x_g, bg_y_g, bg_z_g, created_at AS "createdAt"
            FROM rms_records WHERE train_id = $1 AND gateway_id = $2 ORDER BY created_at DESC, position_mm DESC LIMIT 1
        """, train_no, gateway_id)
        latest_rms = dict(latest_rms_row) if latest_rms_row else None

        latest_peak_row = await conn.fetchrow("""
            SELECT id, train_id AS "trainId", gateway_id AS "gatewayId", archive_sha256 AS "archiveSha256",
                   window_start_mm AS "windowStartMm", position_mm AS "positionMm", speed_kmph AS "speedKmph",
                   latitude, longitude, axes, created_at AS "createdAt"
            FROM peak_records WHERE train_id = $1 AND gateway_id = $2 ORDER BY created_at DESC, position_mm DESC LIMIT 1
        """, train_no, gateway_id)
        latest_peak = dict(latest_peak_row) if latest_peak_row else None
        
        alerts_rows = await conn.fetch("""
            SELECT id, train_no AS "trainNo", gateway_id AS "gatewayId", alert_type AS "alertType",
                   latitude, longitude, position_mm AS "positionMm", session_name AS "sessionName",
                   archive_sha256 AS "archiveSha256", source, peak_axis AS "peakAxis", peak_value_g AS "peakValueG",
                   speed_kmph AS "speedKmph", alert, session_status AS "sessionStatus", zone, division, section,
                   archived_at AS "archivedAt", created_at AS "createdAt"
            FROM alert_events WHERE train_no = $1 AND gateway_id = $2 AND session_status != 'archived'
            ORDER BY created_at DESC LIMIT 20
        """, train_no, gateway_id)
        alerts = [dict(r) for r in alerts_rows]

        archives_rows = await conn.fetch("""
            SELECT id, gateway_id AS "gatewayId", sha256, received_at AS "receivedAt", train_id AS "trainId",
                   session_name AS "sessionName", session_status AS "sessionStatus", size_bytes AS "sizeBytes",
                   status, parse_warnings AS "parseWarnings"
            FROM archives WHERE train_id = $1 AND gateway_id = $2 ORDER BY received_at DESC LIMIT 10
        """, train_no, gateway_id)
        archives = [dict(r) for r in archives_rows]

        faults_rows = await conn.fetch("""
            SELECT id, train_id AS "trainId", gateway_id AS "gatewayId", archive_sha256 AS "archiveSha256",
                   timestamp_ms AS "timestampMs", fault_code AS "faultCode", description, created_at AS "createdAt"
            FROM fault_records WHERE train_id = $1 AND gateway_id = $2 ORDER BY created_at DESC LIMIT 20
        """, train_no, gateway_id)
        faults = [dict(r) for r in faults_rows]

    fallback_peak_g, fallback_alert = None, None
    if latest_peak:
        fallback_peak_g, fallback_alert = (latest_peak.get("axes"))
    if not fallback_peak_g and latest_rms:
        fallback_peak_g, fallback_alert = (latest_rms.get("axes"))

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
