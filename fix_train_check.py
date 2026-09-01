import re

with open('app/routers/telemetry.py', 'r', encoding='utf-8') as f:
    py = f.read()

def inject_train_check(match):
    prefix = match.group(0)
    check = """
    
    if rid and rid.upper() != "ALL":
        train_exists = await db.pg_pool.fetchval("SELECT 1 FROM trains WHERE train_no = $1", rid)
        if not train_exists:
            raise HTTPException(status_code=404, detail="Train not found")"""
    return prefix + check

py = re.sub(r'rid = data\.rid\.strip\(\) if data\.rid else ""', inject_train_check, py)

with open('app/routers/telemetry.py', 'w', encoding='utf-8') as f:
    f.write(py)
