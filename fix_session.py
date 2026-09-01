import re

with open('app/routers/telemetry.py', 'r') as f:
    content = f.read()

new_session_reset = '''@router.post("/api/v1/sessions/reset")
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

    gateways = await db.pg_pool.fetch("SELECT gateway_id AS \\\"gatewayId\\\" FROM gateways WHERE train_id = $1", data.trainNo)
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
    return {"status": "success", "message": "New session started and reset commands queued", "session": serialize(session_response), "resetCommands": queued_commands}'''

content = re.sub(r'@router\.post\("/api/v1/sessions/reset"\).*?return \{"status": "success", "message": "New session started and reset commands queued", "session": serialize\(session_response\), "resetCommands": queued_commands\}', new_session_reset, content, flags=re.DOTALL)

with open('app/routers/telemetry.py', 'w') as f:
    f.write(content)
