import asyncio
import asyncpg
import json
import hashlib

DATABASE_URL = "postgresql://uabams_user:uabams_pass@localhost:5432/uabams_db"
cmd_id = "cmd-063465f4-7b01-47ee-aa38-6685b55c6d9c"

def canonical_json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

async def test():
    conn = await asyncpg.connect(DATABASE_URL)
    sql = "SELECT gateway_id, sha256, payload FROM gateway_commands WHERE command_id = $1"
    row = await conn.fetchrow(sql, cmd_id)
    if not row:
        print("Command not found!")
        return
        
    db_sha = row['sha256']
    # asyncpg parses JSONB to string if we don't configure json codecs, let's see what it returned
    payload = row['payload']
    print(f"Payload Type: {type(payload)}")
    
    if isinstance(payload, str):
        payload = json.loads(payload)
        
    payload_bytes = canonical_json_bytes(payload)
    actual_sha = hashlib.sha256(payload_bytes).hexdigest()
    
    print(f"DB SHA256: {db_sha}")
    print(f"Actual SHA256: {actual_sha}")
    print(f"Match: {db_sha == actual_sha}")

asyncio.run(test())
