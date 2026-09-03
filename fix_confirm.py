import re

with open('app/static/app-main.js', 'r', encoding='utf-8', errors='replace') as f:
    js = f.read()

js = re.sub(r'if \(!confirm\(`[^`]+This will reset the session', 'if (!confirm(`Are you sure? This will reset the session', js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
