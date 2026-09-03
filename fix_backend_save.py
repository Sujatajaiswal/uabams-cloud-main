import re

with open('app/routers/gateways.py', 'r', encoding='utf-8') as f:
    py = f.read()

target = """    current_adxl_left = {**default_adxl, **parse_jsonb(existing.get("adxl_left"))}
    current_adxl_right = {**default_adxl, **parse_jsonb(existing.get("adxl_right"))}
    current_bogie = {**default_bogie, **parse_jsonb(existing.get("bogie"))}
    current_encoder = {**default_encoder, **parse_jsonb(existing.get("encoder"))}"""

replacement = """    current_adxl_left = {**default_adxl, **parse_jsonb(existing.get("adxl_left"))}
    current_adxl_right = {**default_adxl, **parse_jsonb(existing.get("adxl_right"))}
    current_bogie = {**default_bogie, **parse_jsonb(existing.get("bogie"))}
    
    existing_encoder_raw = parse_jsonb(existing.get("encoder"))
    current_encoder = {}
    for k, default_val in default_encoder.items():
        val = existing_encoder_raw.get(k)
        current_encoder[k] = val if val else default_val"""

py = py.replace(target, replacement)

with open('app/routers/gateways.py', 'w', encoding='utf-8') as f:
    f.write(py)
