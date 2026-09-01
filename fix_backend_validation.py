import re

with open('app/routers/telemetry.py', 'r', encoding='utf-8') as f:
    py = f.read()

old_validation = """    if not legacy_key_ok and not password_ok:
        raise HTTPException(status_code=403, detail="Invalid admin reset key or password")
    if not data.startTime and not data.endTime and (data.latitude is None or data.longitude is None):
        raise HTTPException(status_code=400, detail="Provide a time range or location for targeted cleanup")"""

new_validation = """    if not legacy_key_ok and not password_ok:
        raise HTTPException(status_code=403, detail="Invalid admin reset key or password")
    
    if not data.gatewayId or data.gatewayId == "All Gateways":
        raise HTTPException(status_code=400, detail="A specific gateway must be selected for targeted cleanup")
    if not data.startTime or not data.endTime:
        raise HTTPException(status_code=400, detail="Both Start Time and End Time are required for targeted cleanup")
    if (data.latitude is not None and data.longitude is None) or (data.latitude is None and data.longitude is not None):
        raise HTTPException(status_code=400, detail="Latitude and Longitude must be provided together as a pair")"""

py = py.replace(old_validation, new_validation)

with open('app/routers/telemetry.py', 'w', encoding='utf-8') as f:
    f.write(py)
