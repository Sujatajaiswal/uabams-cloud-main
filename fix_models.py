import re

with open('app/models.py', 'r', encoding='utf-8') as f:
    py = f.read()

target = "class UserUpdateRequest(BaseModel):\n"
insertion = "    username: str | None = None\n"

if "username: str | None = None" not in py:
    py = py.replace(target, target + insertion)

with open('app/models.py', 'w', encoding='utf-8') as f:
    f.write(py)
