import re

with open('app/static/app-main.js', 'r', encoding='utf-8', errors='replace') as f:
    js = f.read()

# Fix corrupted command history title
js = re.sub(r'<div class="cal-section-title" style="margin-top:16px">[^<]+Command History</div>', '<div class="cal-section-title" style="margin-top:16px">Command History</div>', js)

# Fix table overflow and ID length
js = js.replace('<table style="width:100%;font-size:0.82em;border-collapse:collapse">', '<div style="overflow-x:auto; width:100%;"><table style="width:100%;font-size:0.82em;border-collapse:collapse">')
js = js.replace('</table>`;', '</table></div>`;')
js = js.replace('.slice(-16)}</td>', '.slice(-8)}</td>')

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
