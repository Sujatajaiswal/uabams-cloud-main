import re

with open('app/static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

html = html.replace('<button id="loadLogsBtn">Refresh Logs</button>', '<button id="loadLogsBtn" onclick="loadLogs()">Refresh Logs</button>')
html = html.replace('app-main.js?v=46', 'app-main.js?v=47')

with open('app/static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)
