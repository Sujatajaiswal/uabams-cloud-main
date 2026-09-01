import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

old_validation = """if (!payload.gatewayId || payload.gatewayId === 'All Gateways') {
    setText('resetOutput', 'Please select a specific Gateway.');
    return;
  }
  if (!payload.startTime || !payload.endTime) {
    setText('resetOutput', 'Please provide both Start Time and End Time.');
    return;
  }"""

new_validation = """if (!payload.gatewayId || payload.gatewayId === 'All Gateways' || !payload.startTime || !payload.endTime) {
    setText('resetOutput', 'Please select a specific Gateway. or Please provide both Start Time and End Time.');
    return;
  }"""

js = js.replace(old_validation, new_validation)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
