import urllib.request
import json
import os
import sys
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import hmac
from hashlib import sha256

def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/encrypt_file.py <path_to_zip_file>")
        return

    zip_path = sys.argv[1]
    if not os.path.exists(zip_path):
        # Check if it exists in current dir as well
        if os.path.exists(os.path.basename(zip_path)):
            zip_path = os.path.basename(zip_path)
        else:
            print(f"Error: File '{zip_path}' not found.")
            return

    # Using the registered API Key from your Swagger output
    api_key = "b13f9dab4123f182d5f0c427361b9959"
    gateway_id = "GW_UABAMS_BOGIE_01"
    base_url = "http://127.0.0.1:8000"

    print(f"Reading file: {zip_path}")
    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    try:
        # 1. Ephemeral keys
        client_private_key = ec.generate_private_key(ec.SECP256R1())
        client_pub_hex = client_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        ).hex()

        # 2. Hello
        hello_url = f"{base_url}/api/v1/handshake/hello"
        hello_data = json.dumps({"gatewayId": gateway_id, "clientPublicKey": client_pub_hex}).encode("utf-8")
        req = urllib.request.Request(
            hello_url,
            data=hello_data,
            headers={"Content-Type": "application/json", "X-Api-Key": api_key},
            method="POST"
        )
        print("Contacting server /hello...")
        with urllib.request.urlopen(req) as res:
            hello_res = json.loads(res.read().decode("utf-8"))

        server_pub_hex = hello_res["serverPublicKey"]
        nonce = hello_res["nonce"]
        session_id = hello_res["sessionId"]

        # 3. Derive key and compute HMAC
        server_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(server_pub_hex))
        shared_secret = client_private_key.exchange(ec.ECDH(), server_pub)
        session_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"uabams-handshake-session-key"
        ).derive(shared_secret)

        client_hmac = hmac.new(session_key, nonce.encode("utf-8"), digestmod=sha256).hexdigest()

        # 4. Verify
        verify_url = f"{base_url}/api/v1/handshake/verify"
        verify_data = json.dumps({"sessionId": session_id, "clientHmac": client_hmac}).encode("utf-8")
        req = urllib.request.Request(
            verify_url,
            data=verify_data,
            headers={"Content-Type": "application/json", "X-Api-Key": api_key},
            method="POST"
        )
        print("Contacting server /verify...")
        with urllib.request.urlopen(req) as res:
            verify_res = json.loads(res.read().decode("utf-8"))

        if verify_res.get("status") != "verified":
            print("Verification failed")
            return

        # 5. Encrypt zip
        iv = os.urandom(12)
        aesgcm = AESGCM(session_key)
        encrypted_bytes = aesgcm.encrypt(iv, zip_bytes, None)

        out_path = "encrypted_payload.bin"
        with open(out_path, "wb") as f:
            f.write(encrypted_bytes)

        # 6. Calculate sha256
        file_sha256 = sha256(encrypted_bytes).hexdigest()

        print("\n=============================================================")
        print("Real Zip File Encrypted Successfully!")
        print(f"Saved encrypted binary payload to:\n{os.path.abspath(out_path)}")
        print("=============================================================")
        print("Copy and paste these fields into Swagger PUT /api/v1/archive:")
        print("=============================================================")
        print(f"  * X-Session-Id: {session_id}")
        print(f"  * X-Session-Iv: {iv.hex()}")
        print(f"  * X-Sha256:     {file_sha256}")
        print("=============================================================")
        print("In Swagger: Click 'Choose File' and select 'encrypted_payload.bin'")
        print("=============================================================\n")

    except Exception as exc:
        print(f"Error: {exc}")

if __name__ == "__main__":
    main()
