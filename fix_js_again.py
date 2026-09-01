import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# 1. Fix cleanupData validation
old_validation = r"if \(!payload\.startTime && !payload\.endTime && \(payload\.latitude === null \|\| payload\.longitude === null\)\) \{\s*setText\('resetOutput', 'Provide a time range or latitude/longitude before deleting data\.'\);\s*return;\s*\}"

new_validation = """if (!payload.gatewayId || payload.gatewayId === 'All Gateways') {
    setText('resetOutput', 'Please select a specific Gateway.');
    return;
  }
  if (!payload.startTime || !payload.endTime) {
    setText('resetOutput', 'Please provide both Start Time and End Time.');
    return;
  }
  if ((payload.latitude !== null && payload.longitude === null) || (payload.latitude === null && payload.longitude !== null)) {
    setText('resetOutput', 'Latitude and Longitude must be provided together as a pair.');
    return;
  }"""

js = re.sub(old_validation, new_validation, js)

# 2. Fix loadLogs catch block
old_load_logs = r"\}\)\.join\(''\) : '<tr><td colspan=\"8\">No logs found\.</td></tr>'\);\s*\}"
new_load_logs = """}).join('') : '<tr><td colspan="8">No logs found.</td></tr>');
  } catch (error) {
    console.error('Logs Error:', error);
    setHtml('logsTable', `<tr><td colspan="8" style="color:red;text-align:center;padding:20px;">Failed to refresh logs: ${error.message}</td></tr>`);
  }"""
js = re.sub(old_load_logs, new_load_logs, js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
