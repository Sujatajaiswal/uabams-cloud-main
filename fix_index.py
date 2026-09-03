import re

with open('app/static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

html = html.replace('<button id="loadLogsBtn" onclick="loadLogs()">Refresh Logs</button>', '<button id="loadLogsBtn">Refresh Logs</button>')

with open('app/static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)
