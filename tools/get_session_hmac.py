import os
import sys
import argparse
import asyncio
import asyncpg
import hmac
from hashlib import sha256
from dotenv import load_dotenv
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

env_path = r"c:\Users\Pilabs\OneDrive\Desktop\Uabams_cloud\.env"
load_dotenv(dotenv_path=env_path, override=True)

PG_URL = os.getenv("DATABASE_URL")

async def get_hmac(session_id: str):
    if not PG_URL:
        print("DATABASE_URL not found!")
        return

    conn = await asyncpg.connect(PG_URL)
    row = await conn.fetchrow("""
        SELECT server_private_key_hex, client_public_key_hex, nonce 
        FROM handshake_sessions 
        WHERE session_id = $1
    """, session_id)
    await conn.close()

    if not row:
        print(f"Error: Session ID [{session_id}] not found in database. Make sure you executed /handshake/hello first!")
        return

    # 1. Load keys
    try:
        server_private_key = serialization.load_der_private_key(
            bytes.fromhex(row["server_private_key_hex"]),
            password=None
        )
        client_pub_bytes = bytes.fromhex(row["client_public_key_hex"])
        client_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(),
            client_pub_bytes
        )
    except Exception as exc:
        print(f"\nError decoding public/private keys for session [{session_id}]: {exc}")
        print("Cause: The clientPublicKey saved during /handshake/hello was literal 'string' or malformed.")
        print("Fix: Run /api/v1/handshake/hello again and replace 'string' with a valid 130-character hex public key!")
        return

    # 2. Compute Shared Secret & Derive Session Key
    shared_secret = server_private_key.exchange(ec.ECDH(), client_public_key)
    session_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"uabams-handshake-session-key",
    ).derive(shared_secret)

    # 3. Compute expected HMAC
    client_hmac = hmac.new(
        session_key,
        row["nonce"].encode("utf-8"),
        digestmod=sha256
    ).hexdigest()

    print("\n==========================================================")
    print("SUCCESS: Computed HMAC Signature for Swagger UI")
    print("==========================================================")
    print("Paste this JSON into your POST /api/v1/handshake/verify body:")
    print("----------------------------------------------------------")
    print(f'{{\n  "sessionId": "{session_id}",\n  "clientHmac": "{client_hmac}"\n}}')
    print("----------------------------------------------------------")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id", type=str, help="The active sessionId from /handshake/hello response")
    args = parser.parse_args()
    asyncio.run(get_hmac(args.session_id))
