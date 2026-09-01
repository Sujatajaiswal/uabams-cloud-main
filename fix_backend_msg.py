import re

with open('app/routers/telemetry.py', 'r', encoding='utf-8') as f:
    py = f.read()

old_validation = """    if not data.gatewayId or data.gatewayId == "All Gateways":
        raise HTTPException(status_code=400, detail="A specific gateway must be selected for targeted cleanup")
    if not data.startTime or not data.endTime:
        raise HTTPException(status_code=400, detail="Both Start Time and End Time are required for targeted cleanup")"""

new_validation = """    if not data.gatewayId or data.gatewayId == "All Gateways" or not data.startTime or not data.endTime:
        raise HTTPException(status_code=400, detail="Please select a specific Gateway. or Please provide both Start Time and End Time.")"""

py = py.replace(old_validation, new_validation)

with open('app/routers/telemetry.py', 'w', encoding='utf-8') as f:
    f.write(py)
