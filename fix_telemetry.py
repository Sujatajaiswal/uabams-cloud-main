import re

with open('app/routers/telemetry.py', 'r') as f:
    c = f.read()

target = '''    results = await db.pg_pool.fetch("\""
        SELECT train_no AS rid, COUNT(*) as count, (array_agg(latitude))[1] as latitude, (array_agg(longitude))[1] as longitude
        FROM alert_events WHERE created_at >=  AND created_at <= 
        GROUP BY train_no ORDER BY count DESC LIMIT 1000
    "\"", from_dt, to_dt)'''

replacement = '''    query = """
        SELECT train_no AS rid, COUNT(*) as count, (array_agg(latitude))[1] as latitude, (array_agg(longitude))[1] as longitude
        FROM alert_events WHERE created_at >=  AND created_at <= 
    """
    args = [from_dt, to_dt]
    rid = data.rid.strip() if data.rid else ""
    if rid and rid.upper() != "ALL":
        args.append(rid)
        query += f" AND train_no = $"
    query += " GROUP BY train_no ORDER BY count DESC LIMIT 1000"
    
    results = await db.pg_pool.fetch(query, *args)'''

c = c.replace(target, replacement)

with open('app/routers/telemetry.py', 'w') as f:
    f.write(c)
