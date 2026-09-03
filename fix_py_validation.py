import re

with open('app/routers/auth.py', 'r', encoding='utf-8') as f:
    py = f.read()

import textwrap

backend_validation = """
import re

def validate_username(username: str):
    if not re.match(r'^[A-Za-z0-9_]+$', username):
        raise HTTPException(status_code=400, detail="Invalid Username. Use only letters, numbers, and underscores (no spaces).")

def validate_password(password: str):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")
    if not re.search(r'[A-Z]', password):
        raise HTTPException(status_code=400, detail="Password must contain at least 1 uppercase letter.")
    if not re.search(r'[a-z]', password):
        raise HTTPException(status_code=400, detail="Password must contain at least 1 lowercase letter.")
    if not re.search(r'\d', password):
        raise HTTPException(status_code=400, detail="Password must contain at least 1 number.")
    if not re.search(r'[@#$%!]', password):
        raise HTTPException(status_code=400, detail="Password must contain at least 1 special character (@, #, $, %, !).")
"""

# Insert validators after imports
if "def validate_username" not in py:
    py = py.replace("import bcrypt", "import bcrypt\n" + backend_validation)

create_user_target = """        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")"""

create_user_validation = """        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")
        
        validate_username(data.username)
        validate_password(data.password)"""

if "validate_username(data.username)" not in py:
    py = py.replace(create_user_target, create_user_validation)

update_user_target = """        if data.password:
            hashed_pw = bcrypt.hash(data.password)"""

update_user_validation = """        if data.password:
            validate_password(data.password)
            hashed_pw = bcrypt.hash(data.password)"""

if "validate_password(data.password)" not in py:
    py = py.replace(update_user_target, update_user_validation)

with open('app/routers/auth.py', 'w', encoding='utf-8') as f:
    f.write(py)
