import re

with open('app/routers/telemetry.py', 'r') as f:
    c = f.read()

pattern = r"(\s+if not points:\s+import random\s+from datetime import timedelta\s+base_time.*?axes_data\[axis_name\] = axis_obj\.get\(\"peakValueG\"\) or 0\.0\s+points\.append\(\{.*?\}\)\s+)if not points:[\s\S]*?\]\s*\}\)"

# Instead of complex regex, let's just find the string literal and replace it
