import re

with open('app/routers/telemetry.py', 'r', encoding='utf-8') as f:
    py = f.read()

# For map/alerts
old_map_alerts = """@router.get("/api/v1/map/alerts")
async def get_map_alerts(train_id: Optional[str] = None):
    async with db.pg_pool.acquire() as conn:
        query = \"\"\"
            SELECT 
                a.id,
                a.gateway_id AS "gatewayId",
                a.latitude,
                a.longitude,
                a.alert,
                a.alert_type AS "alertType",
                a.speed_kmph AS "speedKmph",
                a.peak_axis AS "peakAxis",
                a.peak_value_g AS "peakValueG",
                a.created_at AS "createdAt"
            FROM alert_events a
            WHERE a.latitude IS NOT NULL 
              AND a.longitude IS NOT NULL
        \"\"\"
        if train_id:
            query += " AND a.train_no = $1 ORDER BY a.created_at DESC"
            records = await conn.fetch(query, train_id)"""

new_map_alerts = """@router.get("/api/v1/map/alerts")
async def get_map_alerts(train_id: Optional[str] = None):
    async with db.pg_pool.acquire() as conn:
        if train_id:
            real_rid = await conn.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", train_id, f"TR_{train_id}")
            if real_rid:
                train_id = real_rid
        query = \"\"\"
            SELECT 
                a.id,
                a.gateway_id AS "gatewayId",
                a.latitude,
                a.longitude,
                a.alert,
                a.alert_type AS "alertType",
                a.speed_kmph AS "speedKmph",
                a.peak_axis AS "peakAxis",
                a.peak_value_g AS "peakValueG",
                a.created_at AS "createdAt"
            FROM alert_events a
            WHERE a.latitude IS NOT NULL 
              AND a.longitude IS NOT NULL
        \"\"\"
        if train_id:
            query += " AND a.train_no = $1 ORDER BY a.created_at DESC"
            records = await conn.fetch(query, train_id)"""

py = py.replace(old_map_alerts, new_map_alerts)

# For map/rms
old_map_rms = """@router.get("/api/v1/map/rms")
async def get_map_rms(train_id: Optional[str] = None):
    async with db.pg_pool.acquire() as conn:
        query = \"\"\"
            SELECT 
                gateway_id AS "gatewayId",
                latitude,
                longitude,
                speed_kmph AS "speedKmph",
                rms_x AS "rmsX",
                rms_y AS "rmsY",
                rms_z AS "rmsZ",
                created_at AS "createdAt"
            FROM rms_records 
            WHERE latitude IS NOT NULL 
              AND longitude IS NOT NULL
        \"\"\"
        if train_id:
            query += " AND train_id = $1 ORDER BY created_at DESC LIMIT 500"
            records = await conn.fetch(query, train_id)"""

new_map_rms = """@router.get("/api/v1/map/rms")
async def get_map_rms(train_id: Optional[str] = None):
    async with db.pg_pool.acquire() as conn:
        if train_id:
            real_rid = await conn.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", train_id, f"TR_{train_id}")
            if real_rid:
                train_id = real_rid
        query = \"\"\"
            SELECT 
                gateway_id AS "gatewayId",
                latitude,
                longitude,
                speed_kmph AS "speedKmph",
                rms_x AS "rmsX",
                rms_y AS "rmsY",
                rms_z AS "rmsZ",
                created_at AS "createdAt"
            FROM rms_records 
            WHERE latitude IS NOT NULL 
              AND longitude IS NOT NULL
        \"\"\"
        if train_id:
            query += " AND train_id = $1 ORDER BY created_at DESC LIMIT 500"
            records = await conn.fetch(query, train_id)"""

py = py.replace(old_map_rms, new_map_rms)

with open('app/routers/telemetry.py', 'w', encoding='utf-8') as f:
    f.write(py)
