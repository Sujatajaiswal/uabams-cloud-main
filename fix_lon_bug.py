import re

with open('app/routers/telemetry.py', 'r', encoding='utf-8') as f:
    py = f.read()

target = 'cond_lon = f"latitude BETWEEN ${len(args)-1} AND ${len(args)}"'
replacement = 'cond_lon = f"longitude BETWEEN ${len(args)-1} AND ${len(args)}"'

py = py.replace(target, replacement)

with open('app/routers/telemetry.py', 'w', encoding='utf-8') as f:
    f.write(py)
