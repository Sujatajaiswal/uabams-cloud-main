import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

# Fix placeholders
js = js.replace('placeholder="Enter offset of X-axis"', 'placeholder="Enter offset of X-axis (default 0)"')
js = js.replace('placeholder="Enter offset of Y-axis"', 'placeholder="Enter offset of Y-axis (default 0)"')
js = js.replace('placeholder="Enter offset of Z-axis"', 'placeholder="Enter offset of Z-axis (default 0)"')
js = js.replace('placeholder="Enter IIS offset of X-axis"', 'placeholder="Enter IIS offset of X-axis (default 0)"')
js = js.replace('placeholder="Enter IIS offset of Y-axis"', 'placeholder="Enter IIS offset of Y-axis (default 0)"')
js = js.replace('placeholder="Enter IIS offset of Z-axis"', 'placeholder="Enter IIS offset of Z-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Accel offset of X-axis"', 'placeholder="Enter IMU Accel offset of X-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Accel offset of Y-axis"', 'placeholder="Enter IMU Accel offset of Y-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Accel offset of Z-axis"', 'placeholder="Enter IMU Accel offset of Z-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Gyro offset of X-axis"', 'placeholder="Enter IMU Gyro offset of X-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Gyro offset of Y-axis"', 'placeholder="Enter IMU Gyro offset of Y-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Gyro offset of Z-axis"', 'placeholder="Enter IMU Gyro offset of Z-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Accel offset \nof X-axis"', 'placeholder="Enter IMU Accel offset of X-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Accel offset \nof Y-axis"', 'placeholder="Enter IMU Accel offset of Y-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Accel offset \nof Z-axis"', 'placeholder="Enter IMU Accel offset of Z-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Gyro offset of \nX-axis"', 'placeholder="Enter IMU Gyro offset of X-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Gyro offset of \nY-axis"', 'placeholder="Enter IMU Gyro offset of Y-axis (default 0)"')
js = js.replace('placeholder="Enter IMU Gyro offset of \nZ-axis"', 'placeholder="Enter IMU Gyro offset of Z-axis (default 0)"')


# Fix empty string handling for all fields
def repl(m):
    field_name = m.group(1)
    default_val = m.group(2)
    return f"{field_name}: (field(card, '{m.group(3)}')?.value !== '' ? Number(field(card, '{m.group(3)}')?.value) : {default_val})"

# Regex to find `key: Number(field(card, 'val')?.value ?? default)`
js = re.sub(r'([a-z_]+):\s*Number\(field\(card,\s*\'([^\']+)\'\)\?\.value\s*\?\?\s*([0-9.]+)\)', 
            lambda m: f"{m.group(1)}: (field(card, '{m.group(2)}')?.value !== '' ? Number(field(card, '{m.group(2)}')?.value) : {m.group(3)})", js)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
