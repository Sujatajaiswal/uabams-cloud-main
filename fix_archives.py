import re

with open('app/routers/telemetry.py', 'r', encoding='utf-8') as f:
    py = f.read()

target = 'await db.pg_pool.execute("DELETE FROM alert_events WHERE train_no = $1", data.trainNo)'
insertion = '        await db.pg_pool.execute("DELETE FROM uploaded_archives WHERE gateway_id IN (SELECT gateway_id FROM gateways WHERE train_id = $1)", data.trainNo)'

if "DELETE FROM uploaded_archives" not in py:
    py = py.replace(target, target + '\n' + insertion)

with open('app/routers/telemetry.py', 'w', encoding='utf-8') as f:
    f.write(py)
