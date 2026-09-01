import re

with open('app/static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

html = html.replace('<th data-field="train">Train</th>', '<th data-field="train">Train No</th>')

with open('app/static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)
