import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# Replace all ADXL and Bogie placeholders
js = re.sub(r'placeholder="Enter (?:IIS |IMU Accel |IMU Gyro )?offset of [XYZ]-axis \(default 0\)"', 'placeholder="Default: 0"', js)

# Replace all Encoder placeholders
js = re.sub(r'placeholder="Enter wheel diameter \(default 0\.915\)"', 'placeholder="Default: 0.915"', js)
js = re.sub(r'placeholder="Enter encoder PPR \(default 100\)"', 'placeholder="Default: 100"', js)
js = re.sub(r'placeholder="Enter spatial interval in mm \(default 250\)"', 'placeholder="Default: 250"', js)
js = re.sub(r'placeholder="Enter trigger start speed in km/h \(default 20\.0\)"', 'placeholder="Default: 20.0"', js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
