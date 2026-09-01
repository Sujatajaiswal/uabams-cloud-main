import re

with open('app/routers/hierarchy.py', 'r', encoding='utf-8') as f:
    py = f.read()

old_train_dash = """    async with db.pg_pool.acquire() as conn:
        train = await conn.fetchrow('SELECT train_no AS "trainNo", train_name AS "trainName", created_at AS "createdAt" FROM trains WHERE train_no = $1', train_no)"""

new_train_dash = """    async with db.pg_pool.acquire() as conn:
        # Check standard and TR_ prepended
        train = await conn.fetchrow('SELECT train_no AS "trainNo", train_name AS "trainName", created_at AS "createdAt" FROM trains WHERE train_no = $1 OR train_no = $2', train_no, f"TR_{train_no}")"""

py = py.replace(old_train_dash, new_train_dash)

with open('app/routers/hierarchy.py', 'w', encoding='utf-8') as f:
    f.write(py)
