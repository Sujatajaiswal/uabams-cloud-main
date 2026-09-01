import re

with open('app/routers/telemetry.py', 'r') as f:
    content = f.read()

# Add imports for operator_username, client_ip
if 'operator_username' not in content:
    content = content.replace('is_operator_authenticated,', 'is_operator_authenticated, operator_username, client_ip,')

new_reset_bad_data = '''@router.post("/api/v1/data/reset")
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
    if not data.startTime and not data.endTime and (data.latitude is None or data.longitude is None):
        raise HTTPException(status_code=400, detail="Provide a time range or location for targeted cleanup")

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
        cond_lon = f"latitude BETWEEN ${len(args)-1} AND ${len(args)}"
        
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

    return {"status": "success", "message": "Targeted data removed", "cleanup": cleanup_stats}'''

content = re.sub(r'@router\.post\("/api/v1/data/reset"\).*?return \{"status": "success", "message": "Targeted data removed", "cleanup": \{\}\}', new_reset_bad_data, content, flags=re.DOTALL)

with open('app/routers/telemetry.py', 'w') as f:
    f.write(content)
