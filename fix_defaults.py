import re

with open('app/static/app-main.js', 'r', encoding='utf-8') as f:
    js = f.read()

target = """      encoder: {
        wheel_diameter_m: Number(field(card, 'wheelDiameterM')?.value ?? 0.915),
        encoder_ppr: Number(field(card, 'encoderPpr')?.value ?? 100),
        spatial_interval_mm: Number(field(card, 'spatialIntervalMm')?.value ?? 250),
        trigger_start_speed_kmph: Number(field(card, 'triggerStartSpeedKmph')?.value ?? 20.0),
      },"""

replacement = """      encoder: {
        wheel_diameter_m: Number(field(card, 'wheelDiameterM')?.value || 0.915),
        encoder_ppr: Number(field(card, 'encoderPpr')?.value || 100),
        spatial_interval_mm: Number(field(card, 'spatialIntervalMm')?.value || 250),
        trigger_start_speed_kmph: Number(field(card, 'triggerStartSpeedKmph')?.value || 20.0),
      },"""

js = js.replace(target, replacement)

with open('app/static/app-main.js', 'w', encoding='utf-8') as f:
    f.write(js)
