import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# Replace any placeholder="Enter offset of X-axis" with "Enter offset of X-axis (default 0)" if not already there
js = re.sub(r'placeholder="([^"]+?axis)(?!\s*\(default 0\))"', r'placeholder="\1 (default 0)"', js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
