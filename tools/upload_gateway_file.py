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
        print("Usage: python tools/upload_gateway_file.py <path_to_zip_file>")
        return

    zip_path = sys.argv[1]
    if not os.path.exists(zip_path):
        if os.path.exists(os.path.basename(zip_path)):
            zip_path = os.path.basename(zip_path)
        else:
            print(f"Error: File '{zip_path}' not found.")
            return

    # Configuration (These can be set on your gateway)
    api_key = "b13f9dab4123f182d5f0c427361b9959"
    gateway_id = "GW_UABAMS_BOGIE_01"
    base_url = "http://127.0.0.1:8000"

    print(f"--- Gateway Upload Start ---")
    print(f"Target File: {zip_path}")
    print(f"Target Cloud: {base_url}")

    # Read zip bytes
    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    try:
        # Step 1: Generate Client Ephemeral Keys
        print("[Gateway] Generating Elliptic Curve key pair...")
        client_private_key = ec.generate_private_key(ec.SECP256R1())
        client_pub_hex = client_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        ).hex()

        # Step 2: Handshake Hello (Exchange Public Keys)
        print("[Gateway] Sending public key to /hello endpoint...")
        hello_url = f"{base_url}/api/v1/handshake/hello"
        hello_data = json.dumps({"gatewayId": gateway_id, "clientPublicKey": client_pub_hex}).encode("utf-8")
        req = urllib.request.Request(
            hello_url,
            data=hello_data,
            headers={"Content-Type": "application/json", "X-Api-Key": api_key},
            method="POST"
        )
        with urllib.request.urlopen(req) as res:
            hello_res = json.loads(res.read().decode("utf-8"))

        server_pub_hex = hello_res["serverPublicKey"]
        nonce = hello_res["nonce"]
        session_id = hello_res["sessionId"]
        print(f"[Gateway] Handshake Hello Success! Session ID: {session_id}")

        # Step 3: Compute Shared Secret & Derive Session Key
        print("[Gateway] Deriving symmetric session key via ECDH...")
        server_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(server_pub_hex))
        shared_secret = client_private_key.exchange(ec.ECDH(), server_pub)
        session_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"uabams-handshake-session-key"
        ).derive(shared_secret)

        # Step 4: Verify Session (HMAC Check)
        print("[Gateway] Signing server challenge nonce with HMAC...")
        client_hmac = hmac.new(session_key, nonce.encode("utf-8"), digestmod=sha256).hexdigest()
        
        verify_url = f"{base_url}/api/v1/handshake/verify"
        verify_data = json.dumps({"sessionId": session_id, "clientHmac": client_hmac}).encode("utf-8")
        req = urllib.request.Request(
            verify_url,
            data=verify_data,
            headers={"Content-Type": "application/json", "X-Api-Key": api_key},
            method="POST"
        )
        with urllib.request.urlopen(req) as res:
            verify_res = json.loads(res.read().decode("utf-8"))

        if verify_res.get("status") != "verified":
            print("[Gateway] Handshake verification failed.")
            return
        print("[Gateway] Handshake Verified & Authenticated successfully!")

        # Step 5: Encrypt ZIP File
        print("[Gateway] Encrypting ZIP archive using AES-GCM...")
        iv = os.urandom(12)
        aesgcm = AESGCM(session_key)
        encrypted_bytes = aesgcm.encrypt(iv, zip_bytes, None)
        file_sha256 = sha256(encrypted_bytes).hexdigest()

        # Step 6: Upload Encrypted Archive
        print("[Gateway] Uploading encrypted payload to /archive...")
        archive_url = f"{base_url}/api/v1/archive"
        
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Session-Id": session_id,
            "X-Session-Iv": iv.hex(),
            "X-Sha256": file_sha256
        }
        
        req = urllib.request.Request(
            archive_url,
            data=encrypted_bytes,
            headers=headers,
            method="PUT"
        )
        with urllib.request.urlopen(req) as res:
            archive_res = json.loads(res.read().decode("utf-8"))

        print("\n=============================================================")
        print("SUCCESS! File uploaded securely from Gateway to Cloud.")
        print(f"Server Response: {archive_res}")
        print("=============================================================\n")

    except Exception as exc:
        print(f"[Gateway] Error during secure upload: {exc}")

if __name__ == "__main__":
    main()
