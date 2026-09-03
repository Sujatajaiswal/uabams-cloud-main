import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

replacement = """setCalibrationValues(gatewayId, data);
      let outStr = JSON.stringify(data, null, 2);
      outStr = outStr.replace(/"trigger_start_speed_kmph":\\s*(\\d+)(?![\\.\\d])/g, '"trigger_start_speed_kmph": $1.0');
      outStr = outStr.replace(/"wheel_diameter_m":\\s*(\\d+)(?![\\.\\d])/g, '"wheel_diameter_m": $1.0');
      if (output) output.textContent = outStr;"""

js = re.sub(r'setCalibrationValues\(gatewayId, data\);[\s\r\n]+if \(output\) output.textContent = JSON.stringify\(data, null, 2\);', lambda m: replacement, js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
