import os
import hmac
import asyncio
import asyncpg
import requests
from dotenv import load_dotenv
from hashlib import sha256
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

load_dotenv(override=True)
PG_URL = os.getenv("DATABASE_URL")
BASE_URL = "http://127.0.0.1:8002"

async def get_or_create_auth_key(gateway_id: str, train_id: str):
    conn = await asyncpg.connect(PG_URL)
    
    # 1. Register gateway in gateways table
    gw_row = await conn.fetchrow("SELECT gateway_id FROM gateways WHERE gateway_id = $1", gateway_id)
    if not gw_row:
        await conn.execute("""
            INSERT INTO gateways (gateway_id, train_id, status, provision_status) 
            VALUES ($1, $2, 'online', 'active')
        """, gateway_id, train_id)
        print(f"Registered gateway [{gateway_id}] in gateways table.")

    # 2. Register API Key in gateway_auth table
    row = await conn.fetchrow("SELECT secret_key FROM gateway_auth WHERE gateway_id = $1 AND train_id = $2", gateway_id, train_id)
    if row:
        secret_key = row["secret_key"]
    else:
        # Generate new api key and save it
        import secrets
        secret_key = secrets.token_hex(32)
        await conn.execute("""
            INSERT INTO gateway_auth (gateway_id, train_id, secret_key) 
            VALUES ($1, $2, $3)
        """, gateway_id, train_id, secret_key)
        print(f"Provisioned new key in PostgreSQL: {secret_key}")
    await conn.close()
    return secret_key

def run_test():
    gateway_id = "TEST_GATEWAY"
    train_id = "019456"

    # Step 0: Ensure we have a valid key in PostgreSQL
    print("Connecting to DB to check/create API Key...")
    api_key = asyncio.run(get_or_create_auth_key(gateway_id, train_id))

    # Step 1: Ephemeral ECC key pair
    print("\n--- STEP 1: Handshake Hello ---")
    client_private_key = ec.generate_private_key(ec.SECP256R1())
    client_public_key = client_private_key.public_key()
    client_pub_hex = client_public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    ).hex()

    hello_res = requests.post(f"{BASE_URL}/api/v1/handshake/hello", json={
        "gatewayId": gateway_id,
        "clientPublicKey": client_pub_hex
    })
    print("Hello Status Code:", hello_res.status_code)
    hello_data = hello_res.json()
    print("Hello Response:", hello_data)

    server_pub_hex = hello_data["serverPublicKey"]
    nonce = hello_data["nonce"]
    session_id = hello_data["sessionId"]

    # Step 2: Authenticate Stage
    print("\n--- STEP 2: Authenticate Session ---")
    auth_payload = {
        "gatewayId": gateway_id,
        "trainId": train_id,
        "apiKey": api_key,
        "sessionId": session_id
    }
    auth_res = requests.post(f"{BASE_URL}/api/v1/authenticate", json=auth_payload)
    print("Authenticate Status Code:", auth_res.status_code)
    print("Authenticate Response:", auth_res.json())

    # Step 3: Compute HMAC and verify
    print("\n--- STEP 3: Verify Session ---")
    server_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(),
        bytes.fromhex(server_pub_hex)
    )
    shared_secret = client_private_key.exchange(ec.ECDH(), server_public_key)
    session_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"uabams-handshake-session-key",
    ).derive(shared_secret)

    client_hmac = hmac.new(session_key, nonce.encode("utf-8"), digestmod=sha256).hexdigest()

    verify_payload = {
        "sessionId": session_id,
        "clientHmac": client_hmac
    }
    verify_res = requests.post(f"{BASE_URL}/api/v1/handshake/verify", json=verify_payload)
    print("Verify Status Code:", verify_res.status_code)
    print("Verify Response:", verify_res.json())

    print("\n==========================================================")
    print("SWAGGER UI PAYLOAD DEMO")
    print("==========================================================")
    print("1. Hello Request body:")
    print(f'{{\n  "gatewayId": "{gateway_id}",\n  "clientPublicKey": "{client_pub_hex}"\n}}')
    print("\n2. Authenticate Request body:")
    print(f'{{\n  "gatewayId": "{gateway_id}",\n  "trainId": "{train_id}",\n  "apiKey": "{api_key}",\n  "sessionId": "{session_id}"\n}}')
    print("\n3. Verify Request body:")
    print(f'{{\n  "sessionId": "{session_id}",\n  "clientHmac": "{client_hmac}"\n}}')

if __name__ == "__main__":
    run_test()
