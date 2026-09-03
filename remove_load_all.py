import re

with open('app/static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

html = re.sub(r'\s*<button id="loadAllCalibrationBtn">Load All</button>', '', html)

with open('app/static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

js = re.sub(r"\s*setText\('loadAllCalibrationBtn', [^;]+;", "", js)
js = re.sub(r"\s*\$\('loadAllCalibrationBtn'\)\?\.addEventListener\('click', loadAllCalibration\);", "", js)

# Remove the function block
js = re.sub(r"async function loadAllCalibration\(\) \{\s*for \(const gatewayId of visibleGatewayIds\(\)\) \{\s*await loadCalibration\(gatewayId\);\s*\}\s*\}", "", js)


with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
