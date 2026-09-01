import re

with open('app/routers/telemetry.py', 'r', encoding='utf-8') as f:
    py = f.read()

old_query = """    await db.pg_pool.execute(\"\"\"
        INSERT INTO trains (train_no, train_name, status, updated_at, created_at)
        VALUES ($1, $2, 'running', $3, $3)
        ON CONFLICT (train_no) DO UPDATE SET
            status = 'running', updated_at = EXCLUDED.updated_at
    \"\"\", train_id, (train_id), now)"""

new_query = """    await db.pg_pool.execute(\"\"\"
        INSERT INTO trains (train_no, train_name, created_at)
        VALUES ($1, $2, $3)
        ON CONFLICT (train_no) DO NOTHING
    \"\"\", train_id, train_id, now)"""

py = py.replace(old_query, new_query)

with open('app/routers/telemetry.py', 'w', encoding='utf-8') as f:
    f.write(py)
