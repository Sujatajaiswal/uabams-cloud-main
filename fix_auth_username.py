import re

with open('app/routers/auth.py', 'r', encoding='utf-8') as f:
    py = f.read()

target = """        if not user:
            raise HTTPException(status_code=404, detail="User not found")"""

insertion = """
        if data.username is not None and data.username != user['username']:
            validate_username(data.username)
            existing = await db.pg_pool.fetchrow("SELECT id FROM users WHERE username = $1", data.username)
            if existing:
                raise HTTPException(status_code=400, detail="Username already exists")
            
            # Prevent renaming the default admin
            if user['username'] == 'admin':
                raise HTTPException(status_code=400, detail="Cannot change the username of the default admin account")
"""

if "data.username is not None and data.username != user['username']" not in py:
    py = py.replace(target, target + insertion)

# Add it to the updates list
update_target = """        idx = 1
        if data.role is not None:"""

update_insertion = """        idx = 1
        if data.username is not None and data.username != user['username']:
            updates.append(f"username = ${idx}")
            params.append(data.username)
            idx += 1
        if data.role is not None:"""

if "updates.append(f\"username = ${idx}\")" not in py:
    py = py.replace(update_target, update_insertion)

with open('app/routers/auth.py', 'w', encoding='utf-8') as f:
    f.write(py)
