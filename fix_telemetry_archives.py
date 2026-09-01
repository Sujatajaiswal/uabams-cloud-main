import re

with open('app/routers/telemetry.py', 'r', encoding='utf-8') as f:
    py = f.read()

# For train_archives
old_archives = """@router.get("/api/v1/trains/{train_no}/archives")
async def train_archives(train_no: str):
    archives = await db.pg_pool.fetch(\"\"\""""

new_archives = """@router.get("/api/v1/trains/{train_no}/archives")
async def train_archives(train_no: str):
    real_rid = await db.pg_pool.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", train_no, f"TR_{train_no}")
    if real_rid:
        train_no = real_rid
    archives = await db.pg_pool.fetch(\"\"\""""

py = py.replace(old_archives, new_archives)

# For train_position
old_pos = """@router.get("/api/v1/trains/{train_no}/position")
async def train_position(train_no: str, gateway_id: str | None = None):
    query = \"SELECT gateway_id AS \\"gatewayId\\", latitude, longitude, position_mm AS \\"positionMm\\", speed AS \\"speedKmph\\", created_at AS \\"createdAt\\" FROM rms_records WHERE train_id = $1 AND gps_valid = TRUE AND latitude IS NOT NULL AND latitude != 0 AND longitude IS NOT NULL AND longitude != 0\""""

new_pos = """@router.get("/api/v1/trains/{train_no}/position")
async def train_position(train_no: str, gateway_id: str | None = None):
    real_rid = await db.pg_pool.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", train_no, f"TR_{train_no}")
    if real_rid:
        train_no = real_rid
    query = \"SELECT gateway_id AS \\"gatewayId\\", latitude, longitude, position_mm AS \\"positionMm\\", speed AS \\"speedKmph\\", created_at AS \\"createdAt\\" FROM rms_records WHERE train_id = $1 AND gps_valid = TRUE AND latitude IS NOT NULL AND latitude != 0 AND longitude IS NOT NULL AND longitude != 0\""""

py = py.replace(old_pos, new_pos)

with open('app/routers/telemetry.py', 'w', encoding='utf-8') as f:
    f.write(py)
