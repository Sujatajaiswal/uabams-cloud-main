import re

with open('app/routers/telemetry.py', 'r', encoding='utf-8') as f:
    py = f.read()

# load_alarm_log_report
old_alarm = """    if rid and rid.upper() != "ALL":
        train_exists = await db.pg_pool.fetchval("SELECT 1 FROM trains WHERE train_no = $1", rid)
        if not train_exists:
            raise HTTPException(status_code=404, detail="Train not found")"""

new_alarm = """    if rid and rid.upper() != "ALL":
        real_rid = await db.pg_pool.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", rid, f"TR_{rid}")
        if not real_rid:
            raise HTTPException(status_code=404, detail="Train not found")
        rid = real_rid
        data.rid = real_rid"""

py = py.replace(old_alarm, new_alarm)

# load_repeated_alarm_report
old_rep = """    if rid and rid.upper() != "ALL":
        train_exists = await db.pg_pool.fetchval("SELECT 1 FROM trains WHERE train_no = $1", rid)
        if not train_exists:
            raise HTTPException(status_code=404, detail="Train not found")"""

new_rep = """    if rid and rid.upper() != "ALL":
        real_rid = await db.pg_pool.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", rid, f"TR_{rid}")
        if not real_rid:
            raise HTTPException(status_code=404, detail="Train not found")
        rid = real_rid
        data.rid = real_rid"""

py = py.replace(old_rep, new_rep)

# load_graph_report doesn't throw 404, but it queries by `data.rid`. Let's fix that.
old_graph = """    if data.rid and data.rid.upper() != "ALL":
        base_query += " AND r.train_id = $2"
        params.append(data.rid)
    else:"""

new_graph = """    if data.rid and data.rid.upper() != "ALL":
        real_rid = await db.pg_pool.fetchval("SELECT train_no FROM trains WHERE train_no = $1 OR train_no = $2 LIMIT 1", data.rid, f"TR_{data.rid}")
        if real_rid:
            data.rid = real_rid
            
        base_query += " AND r.train_id = $2"
        params.append(data.rid)
    else:"""

py = py.replace(old_graph, new_graph)

with open('app/routers/telemetry.py', 'w', encoding='utf-8') as f:
    f.write(py)
