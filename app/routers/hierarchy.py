import json
from datetime import UTC
from dateutil.parser import parse as parse_datetime
from fastapi import APIRouter, Request, HTTPException
from typing import Any

from app.database import db
from app.utils import  serialize, is_operator_authenticated, operator_session_payload,  utc_now

router = APIRouter()


def _axis_summary(axes: Any) -> tuple[float | None, str | None]:
    if isinstance(axes, str):
        try:
            axes = json.loads(axes)
        except (TypeError, json.JSONDecodeError):
            return None, None
    if not isinstance(axes, dict):
        return None, None

    values = []
    for axis in axes.values():
        if not isinstance(axis, dict):
            continue
        value = axis.get("peakValueG")
        if value is None:
            value = axis.get("rmsValue")
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not values:
        return None, None

    peak_g = max(values)
    alert = "RED" if peak_g > 80 else "YELLOW" if peak_g > 50 else "GREEN"
    return peak_g, alert

@router.get("/api/v1/trains")
async def list_trains():
    if not db.pg_pool:
        return []
    
    async with db.pg_pool.acquire() as conn:
        records = await conn.fetch('SELECT train_no AS "trainNo", train_name AS "trainName" FROM trains')
    
    unique_trains = {}
    for t in records:
        no = t.get("trainNo")
        if not no:
            continue
        name = t.get("trainName") or ""
        if not name:
            name = (no)
        
        display_no = no.replace("TR_", "") if no.startswith("TR_") else no
        unique_trains[display_no] = {
            "trainNo": display_no,
            "trainName": name
        }
    return sorted(list(unique_trains.values()), key=lambda x: x["trainNo"])

@router.get("/api/v1/trains/{train_no}/dashboard")
async def train_dashboard(train_no: str, request: Request):
    if not db.pg_pool:
        return {}
        
    async with db.pg_pool.acquire() as conn:
        # Check standard and TR_ prepended
        train = await conn.fetchrow('SELECT train_no AS "trainNo", train_name AS "trainName", created_at AS "createdAt" FROM trains WHERE train_no = $1 OR train_no = $2', train_no, f"TR_{train_no}")
        if not train:
            raise HTTPException(status_code=404, detail="Train not found")
            
        train = dict(train)
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

        associated_gateways = await conn.fetch('SELECT gateway_id AS "gatewayId" FROM gateways WHERE train_id = $1 LIMIT 20', train_no)
        gateway_ids = [g["gatewayId"] for g in associated_gateways if g.get("gatewayId")]

        statuses = []
        if gateway_ids:
            statuses = await conn.fetch('SELECT gateway_id AS "gatewayId", train_id AS "trainId", online, last_heartbeat AS "lastHeartbeat" FROM gateway_status WHERE gateway_id = ANY($1)', gateway_ids)

        status_by_id = {item["gatewayId"]: dict(item) for item in statuses}
        now_dt = utc_now()
        gateway_cards = []

        for gateway_id in gateway_ids:
            latest_peak = await conn.fetchrow('''
                SELECT axes, latitude, longitude
                FROM peak_records 
                WHERE train_id = $1 AND gateway_id = $2 
                ORDER BY created_at DESC, position_mm DESC LIMIT 1
            ''', train_no, gateway_id)

            latest_rms = await conn.fetchrow('''
                SELECT axes, latitude, longitude
                FROM rms_records 
                WHERE train_id = $1 AND gateway_id = $2 
                ORDER BY created_at DESC, position_mm DESC LIMIT 1
            ''', train_no, gateway_id)

            card = status_by_id.get(gateway_id)
            if card:
                lh = card.get("lastHeartbeat")
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
                fallback_peak_g, fallback_alert = _axis_summary(latest_peak.get("axes"))
            if not fallback_peak_g and latest_rms:
                fallback_peak_g, fallback_alert = _axis_summary(latest_rms.get("axes"))

            card["latestPeakG"] = fallback_peak_g
            card["latestAlert"] = fallback_alert
            card["latestLatitude"] = latest_peak.get("latitude") if latest_peak else latest_rms.get("latitude") if latest_rms else None
            card["latestLongitude"] = latest_peak.get("longitude") if latest_peak else latest_rms.get("longitude") if latest_rms else None

            gateway_cards.append(card)

        alerts = await conn.fetch('''
            SELECT 
                id AS "_id",
                train_no AS "trainNo",
                gateway_id AS "gatewayId",
                alert_type AS "alertType",
                latitude, longitude,
                position_mm AS "positionMm",
                session_name AS "sessionName",
                archive_sha256 AS "archiveSha256",
                source,
                peak_axis AS "peakAxis",
                peak_value_g AS "peakValueG",
                speed_kmph AS "speedKmph",
                alert,
                session_status AS "sessionStatus",
                zone, division, section,
                archived_at AS "archivedAt",
                created_at AS "createdAt"
            FROM alert_events 
            WHERE train_no = $1 AND session_status != 'archived' 
            ORDER BY created_at DESC LIMIT 30
        ''', train_no)

        archives = await conn.fetch('''
            SELECT 
                id AS "_id",
                gateway_id AS "gatewayId",
                sha256,
                received_at AS "receivedAt",
                train_id AS "trainId",
                session_name AS "sessionName",
                session_status AS "sessionStatus",
                size_bytes AS "sizeBytes",
                status,
                parse_warnings AS "parseWarnings"
            FROM archives 
            WHERE train_id = $1 
            ORDER BY received_at DESC LIMIT 20
        ''', train_no)
        
        formatted_archives = []
        for arc in archives:
            arc_dict = dict(arc)
            if arc_dict.get("parseWarnings") and isinstance(arc_dict["parseWarnings"], str):
                try:
                    arc_dict["parseWarnings"] = json.loads(arc_dict["parseWarnings"])
                except Exception:
                    pass
            formatted_archives.append(arc_dict)

        active_session = await conn.fetchrow('''
            SELECT 
                id AS "_id",
                train_no AS "trainNo",
                session_name AS "sessionName",
                status,
                created_at AS "createdAt",
                closed_at AS "closedAt"
            FROM sessions 
            WHERE train_no = $1 AND status = 'active' 
            ORDER BY created_at DESC LIMIT 1
        ''', train_no)

        display_train = dict(train)
        if display_train.get("trainNo", "").startswith("TR_"):
            display_train["trainNo"] = display_train["trainNo"].replace("TR_", "")

        payload = operator_session_payload(request)
        role = payload.get("role", "operator") if payload else "operator"
        permissions = {
            "can_configure_thresholds": payload.get("can_configure_thresholds", False) if payload else False,
            "can_manage_users": payload.get("can_manage_users", False) if payload else False,
            "can_view_alerts": payload.get("can_view_alerts", True) if payload else True,
        }
        
        return {
            "train": serialize(display_train),
            "gateways": serialize([dict(g) for g in gateway_cards]),
            "lastAlerts": serialize([dict(a) for a in alerts]),
            "archives": serialize(formatted_archives),
            "activeSession": serialize(dict(active_session)) if active_session else None,
            "userRole": role,
            "permissions": permissions,
        }

@router.get("/api/v1/hierarchy/zones")
async def get_zones(request: Request):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    if not db.pg_pool:
        return []
    async with db.pg_pool.acquire() as conn:
        records = await conn.fetch("SELECT id, code, name FROM zones ORDER BY name ASC")
    return [dict(r) for r in records]

@router.get("/api/v1/hierarchy/divisions")
async def get_divisions(request: Request, zone_code: str = None):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    if not db.pg_pool:
        return []
    async with db.pg_pool.acquire() as conn:
        if zone_code:
            records = await conn.fetch("""
                SELECT d.id, d.code, d.name, d.zone_id AS "zoneId"
                FROM divisions d JOIN zones z ON d.zone_id = z.id 
                WHERE z.code = $1 ORDER BY d.name ASC
            """, zone_code)
        else:
            records = await conn.fetch('SELECT id, code, name, zone_id AS "zoneId" FROM divisions ORDER BY name ASC')
    return [dict(r) for r in records]

@router.get("/api/v1/hierarchy/sections")
async def get_sections(request: Request, division_code: str = None):
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Login required")
    if not db.pg_pool:
        return []
    async with db.pg_pool.acquire() as conn:
        if division_code:
            records = await conn.fetch("""
                SELECT s.id, s.code, s.name, s.division_id AS "divisionId", s.start_km AS "startKm", s.end_km AS "endKm"
                FROM sections s JOIN divisions d ON s.division_id = d.id 
                WHERE d.code = $1 ORDER BY s.name ASC
            """, division_code)
        else:
            records = await conn.fetch('SELECT id, code, name, division_id AS "divisionId", start_km AS "startKm", end_km AS "endKm" FROM sections ORDER BY name ASC')
    return [dict(r) for r in records]
