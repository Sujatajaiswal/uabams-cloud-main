import asyncio
import json
import httpx
import jwt
from datetime import datetime, timezone, timedelta

async def test():
    token = jwt.encode({
        "gateway_id": "GW1_05052_BOGIE_02",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1)
    }, "replace-with-a-long-random-secret", algorithm="HS256")
    
    payload = {
        "gatewayId": "GW1_05052_BOGIE_02",
        "trainId": "05052",
        "token": token
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post("http://localhost:8000/api/v1/heartbeat", json=payload)
        print(resp.status_code)
        print(resp.text)

asyncio.run(test())
