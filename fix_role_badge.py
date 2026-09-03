import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# Replace invalid background:var(--border) with default badge or specific color
js = js.replace('style="background:var(--border)"', 'style="background:var(--blue); color:white;"')

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
