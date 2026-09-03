import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# Fix setCalibrationValues to use toFixed()
target1 = """  if (field(card, 'wheelDiameterM')) field(card, 'wheelDiameterM').value = encoder.wheel_diameter_m || 0.915;
  if (field(card, 'encoderPpr')) field(card, 'encoderPpr').value = encoder.encoder_ppr || 100;
  if (field(card, 'spatialIntervalMm')) field(card, 'spatialIntervalMm').value = encoder.spatial_interval_mm || 250;
  if (field(card, 'triggerStartSpeedKmph')) field(card, 'triggerStartSpeedKmph').value = encoder.trigger_start_speed_kmph || 20.0;"""

replacement1 = """  if (field(card, 'wheelDiameterM')) field(card, 'wheelDiameterM').value = Number(encoder.wheel_diameter_m || 0.915).toFixed(3);
  if (field(card, 'encoderPpr')) field(card, 'encoderPpr').value = encoder.encoder_ppr || 100;
  if (field(card, 'spatialIntervalMm')) field(card, 'spatialIntervalMm').value = encoder.spatial_interval_mm || 250;
  if (field(card, 'triggerStartSpeedKmph')) field(card, 'triggerStartSpeedKmph').value = Number(encoder.trigger_start_speed_kmph || 20.0).toFixed(1);"""

js = js.replace(target1, replacement1)

# Fix JSON output string formatting
target2 = """      setCalibrationValues(gatewayId, data);
      if (output) output.textContent = JSON.stringify(data, null, 2);
      const status = card?.querySelector('[data-role="calStatus"]');"""

replacement2 = """      setCalibrationValues(gatewayId, data);
      let outStr = JSON.stringify(data, null, 2);
      outStr = outStr.replace(/"trigger_start_speed_kmph":\\s*(\\d+)(?![\\.\\d])/g, '"trigger_start_speed_kmph": $1.0');
      outStr = outStr.replace(/"wheel_diameter_m":\\s*(\\d+)(?![\\.\\d])/g, '"wheel_diameter_m": $1.0');
      if (output) output.textContent = outStr;
      const status = card?.querySelector('[data-role="calStatus"]');"""

js = js.replace(target2, replacement2)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
