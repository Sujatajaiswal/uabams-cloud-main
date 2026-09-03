import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# 1. Update load logic for Encoder (use || instead of ?? so 0 becomes default)
js = re.sub(r"encoder\.wheel_diameter_m \?\? 0\.915", "encoder.wheel_diameter_m || 0.915", js)
js = re.sub(r"encoder\.encoder_ppr \?\? 100", "encoder.encoder_ppr || 100", js)
js = re.sub(r"encoder\.spatial_interval_mm \?\? 250", "encoder.spatial_interval_mm || 250", js)
js = re.sub(r"encoder\.trigger_start_speed_kmph \?\? 20\.0", "encoder.trigger_start_speed_kmph || 20.0", js)

# 2. Add min/max to ADXL / Bogie
js = re.sub(r'(<input data-field="(adxl|iis|imu)[a-zA-Z]+" type="number")', r'\1 min="-32768" max="32767"', js)

# 3. Add min/max to Encoder Settings
js = re.sub(r'(<input data-field="wheelDiameterM" type="number" step="0\.001")', r'\1 min="0.8" max="1.0"', js)
js = re.sub(r'(<input data-field="encoderPpr" type="number")', r'\1 min="100" max="4096"', js)
js = re.sub(r'(<input data-field="spatialIntervalMm" type="number")', r'\1 min="100" max="500"', js)
js = re.sub(r'(<input data-field="triggerStartSpeedKmph" type="number" step="0\.1")', r'\1 min="10.0" max="40.0"', js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
