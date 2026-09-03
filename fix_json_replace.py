with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

target = "setCalibrationValues(gatewayId, data);\n      if (output) output.textContent = JSON.stringify(data, null, 2);"
replacement = """setCalibrationValues(gatewayId, data);
      let outStr = JSON.stringify(data, null, 2);
      outStr = outStr.replace(/"trigger_start_speed_kmph":\\s*(\\d+)(?![\\.\\d])/g, '"trigger_start_speed_kmph": $1.0');
      outStr = outStr.replace(/"wheel_diameter_m":\\s*(\\d+)(?![\\.\\d])/g, '"wheel_diameter_m": $1.0');
      if (output) output.textContent = outStr;"""

js = js.replace(target, replacement)

# Windows line endings might be CRLF
target2 = "setCalibrationValues(gatewayId, data);\r\n      if (output) output.textContent = JSON.stringify(data, null, 2);"
js = js.replace(target2, replacement)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
