import re

with open('app/routers/gateways.py', 'r', encoding='utf-8') as f:
    py = f.read()

target = """    encoder_data = parse_jsonb(calibration.get("encoder"))

    return {
        "gatewayId": gateway_id,
        "version": calibration.get("version"),
        "adxl_left": {**default_adxl, **adxl_left_data},
        "adxl_right": {**default_adxl, **adxl_right_data},
        "bogie": {**default_bogie, **bogie_data},
        "encoder": {**default_encoder, **encoder_data},
    }"""

replacement = """    encoder_data = parse_jsonb(calibration.get("encoder"))
    
    scrubbed_encoder = {}
    for k, default_val in default_encoder.items():
        val = encoder_data.get(k)
        scrubbed_encoder[k] = val if val else default_val

    return {
        "gatewayId": gateway_id,
        "version": calibration.get("version"),
        "adxl_left": {**default_adxl, **adxl_left_data},
        "adxl_right": {**default_adxl, **adxl_right_data},
        "bogie": {**default_bogie, **bogie_data},
        "encoder": scrubbed_encoder,
    }"""

py = py.replace(target, replacement)

with open('app/routers/gateways.py', 'w', encoding='utf-8') as f:
    f.write(py)
