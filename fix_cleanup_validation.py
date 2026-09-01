import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

old_validation = """    if (!payload.startTime && !payload.endTime && (payload.latitude === null || payload.longitude === null)) {
      setText('resetOutput', 'Provide a time range or latitude/longitude before deleting data.');
      return;
    }"""

new_validation = """    if (!payload.gatewayId || !payload.startTime || !payload.endTime) {
      setText('resetOutput', 'Please select a specific Gateway and provide both Start Time and End Time.');
      return;
    }
    if ((payload.latitude !== null && payload.longitude === null) || (payload.latitude === null && payload.longitude !== null)) {
      setText('resetOutput', 'Latitude and Longitude must be provided together as a pair.');
      return;
    }"""

js = js.replace(old_validation, new_validation)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
