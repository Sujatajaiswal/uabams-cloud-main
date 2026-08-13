import urllib.request
import json
import os
import sys
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import hmac as python_hmac
from hashlib import sha256

def main():
    # Load API Key from environment or defaults
    api_key = "b13f9dab4123f182d5f0c427361b9959"
    gateway_id = "GW_UABAMS_BOGIE_01"
    base_url = "http://127.0.0.1:8000"

    print(f"Connecting to server at: {base_url}")
    print(f"Using Gateway ID: {gateway_id}")

    try:
        # 1. Generate client keys
        client_private_key = ec.generate_private_key(ec.SECP256R1())
        client_pub_hex = client_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        ).hex()

        # 2. Call hello endpoint
        hello_url = f"{base_url.rstrip('/')}/api/v1/handshake/hello"
        hello_payload = {
            "gatewayId": gateway_id,
            "clientPublicKey": client_pub_hex
        }
        
        data_bytes = json.dumps(hello_payload).encode("utf-8")
        req = urllib.request.Request(
            hello_url, 
            data=data_bytes, 
            headers={"Content-Type": "application/json", "X-Api-Key": api_key}, 
            method="POST"
        )
        
        print("Initiating handshake hello...")
        with urllib.request.urlopen(req, timeout=10) as response:
            res = json.loads(response.read().decode("utf-8"))

        server_pub_hex = res["serverPublicKey"]
        nonce = res["nonce"]
        session_id = res["sessionId"]

        # 3. Compute Shared Secret & Derive Session Key
        server_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(),
            bytes.fromhex(server_pub_hex)
        )
        shared_key = client_private_key.exchange(ec.ECDH(), server_public_key)
        
        session_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"uabams-handshake-session-key",
        ).derive(shared_key)

        # 4. Compute HMAC of challenge nonce
        client_hmac = python_hmac.new(
            session_key,
            nonce.encode("utf-8"),
            digestmod=sha256
        ).hexdigest()

        # 5. Output verify payload
        verify_payload = {
            "sessionId": session_id,
            "clientHmac": client_hmac
        }

        print("\n=============================================================")
        print("🎉 SUCCESS! Handshake initiated.")
        print("Copy and paste this JSON directly into your Swagger '/verify' body:")
        print("=============================================================")
        print(json.dumps(verify_payload, indent=2))
        print("=============================================================\n")

    except Exception as exc:
        print(f"Error during helper generation: {exc}")
        print("Make sure your local FastAPI server is running on port 8000.")

if __name__ == "__main__":
    main()
