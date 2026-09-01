import re

with open('app/static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

html = html.replace('  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css">\n', '')
html = html.replace('  <script src="https://cdn.jsdelivr.net/npm/flatpickr"></script>\n', '')
html = html.replace('app-main.js?v=43', 'app-main.js?v=44')

with open('app/static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)
