import argparse
import json
import math
import os
import random
import struct
import time
import urllib.error
import urllib.request
import zipfile
from hashlib import sha256
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Constants matching UABAMS binary protocols
RMS_FORMAT = "<Qifdd?B9f"
PEAK_HEADER_FORMAT = "<iifB?"
PEAK_AXIS_FORMAT = "<fIQdd"
FAULT_FORMAT = "<QBBB64s"
AXIS_COUNT = 9

# Default gateway configuration
GW1_ID = "GW_UABAMS_BOGIE_01"
GW2_ID = "GW_UABAMS_BOGIE_02"
GW1_KEY = os.getenv("GATEWAY_API_KEY_GW01", "replace-with-gw1-api-key")
GW2_KEY = os.getenv("GATEWAY_API_KEY_GW02", "replace-with-gw2-api-key")

def lerp(a: float, b: float, ratio: float) -> float:
    return a + (b - a) * ratio

def route_point(index: int, total: int, train_idx: int) -> tuple[float, float]:
    # Distribute routes slightly based on train index so different trains don't overlap completely
    offset_lat = (train_idx % 10) * 0.002
    offset_lon = ((train_idx // 10) % 10) * 0.002
    ratio = min(1.0, index / max(total - 1, 1))
    
    base_lat = lerp(12.9716, 13.0350, ratio) + offset_lat
    base_lon = lerp(77.5946, 77.6400, ratio) + offset_lon
    return base_lat, base_lon

def build_rms(train_idx: int, records: int) -> bytes:
    chunks = []
    for i in range(records):
        lat, lon = route_point(i, records, train_idx)
        # Occasional random g-force peaks
        max_g = random.uniform(8, 35) if (i % 15 != 0) else random.uniform(45, 92)
        axes = [random.uniform(0.5, max(1.0, max_g * 0.45)) for _ in range(AXIS_COUNT)]
        axes[random.randrange(AXIS_COUNT)] = max_g
        chunks.append(struct.pack(
            RMS_FORMAT,
            i,                       # masterCount
            i * 250,                 # positionMm (25cm steps)
            random.uniform(72, 105), # speedKmph
            lat,
            lon,
            True,                    # gpsValid
            0xFF,                    # validMask
            *axes,
        ))
    return b"".join(chunks)

def build_peak(train_idx: int, records: int) -> bytes:
    output = bytearray()
    num_records = max(2, records)
    for i in range(num_records):
        window_start = i * 50000
        window_end = window_start + 50000
        
        scenario = train_idx % 6
        if scenario == 0:
            # RED only
            max_g = random.uniform(82, 95)
        elif scenario == 1:
            # YELLOW only
            max_g = random.uniform(55, 75)
        elif scenario == 2:
            # RED and YELLOW
            max_g = random.uniform(82, 95) if i == 0 else random.uniform(55, 75)
        elif scenario == 3:
            # YELLOW and GREEN
            max_g = random.uniform(55, 75) if i == 0 else random.uniform(15, 45)
        elif scenario == 4:
            # RED and GREEN
            max_g = random.uniform(82, 95) if i == 0 else random.uniform(15, 45)
        else:
            # GREEN only
            max_g = random.uniform(15, 45)
            
        alert_generated = True
        output += struct.pack(PEAK_HEADER_FORMAT, window_start, window_end, random.uniform(75, 105), 0xFF, alert_generated)
        for axis in range(AXIS_COUNT):
            peak_g = max_g if axis == 0 else random.uniform(2, max_g * 0.35)
            lat, lon = route_point(i, num_records, train_idx)
            output += struct.pack(PEAK_AXIS_FORMAT, peak_g, window_start + axis * 500, i, lat, lon)
    return bytes(output)

def build_fault(train_idx: int) -> bytes:
    # Trigger a fault simulation for 1 in every 5 trains
    if train_idx % 5 != 0:
        return b""
    description = f"LOAD TEST fault on train {train_idx}".encode("ascii")[:64].ljust(64, b"\0")
    return struct.pack(FAULT_FORMAT, int(time.time() * 1000), 0x30, 1, 2, description)

def build_zip(train_id: str, gateway_id: str, train_idx: int, rms_recs: int, peak_recs: int) -> bytes:
    metadata = {
        "sessionName": f"SESSION_{train_id}",
        "trainId": train_id,
        "gatewayId": gateway_id,
        "sessionStatus": "active",
        "simulated": True
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("session_metadata.json", json.dumps(metadata, indent=2))
        zf.writestr("rms/rms_25cm.bin", build_rms(train_idx, rms_recs))
        zf.writestr("peak/peak_50m.bin", build_peak(train_idx, peak_recs))
        zf.writestr("faults/faults.bin", build_fault(train_idx))
    return buffer.getvalue()

def post_json(url: str, payload: dict, timeout: int, headers: dict | None = None) -> dict:
    data_bytes = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data_bytes, method="POST", headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def perform_handshake(base_url: str, gateway_id: str, api_key: str, timeout: int) -> tuple[str, bytes]:
    # 1. Generate client ECDH keys
    client_private_key = ec.generate_private_key(ec.SECP256R1())
    client_public_key = client_private_key.public_key()
    
    client_pub_bytes = client_public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    client_pub_hex = client_pub_bytes.hex()
    
    # 2. Call /api/v1/handshake/hello
    hello_url = f"{base_url.rstrip('/')}/api/v1/handshake/hello"
    hello_payload = {
        "gatewayId": gateway_id,
        "clientPublicKey": client_pub_hex
    }
    hello_headers = {"X-Api-Key": api_key}
    hello_res = post_json(hello_url, hello_payload, timeout, hello_headers)
    
    server_pub_hex = hello_res["serverPublicKey"]
    nonce = hello_res["nonce"]
    session_id = hello_res["sessionId"]
    
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
    
    # 4. Compute HMAC of nonce
    client_hmac = python_hmac.new(
        session_key,
        nonce.encode("utf-8"),
        digestmod=sha256
    ).hexdigest()
    
    # 5. Call /api/v1/handshake/verify
    verify_url = f"{base_url.rstrip('/')}/api/v1/handshake/verify"
    verify_payload = {
        "sessionId": session_id,
        "clientHmac": client_hmac
    }
    verify_headers = {"X-Api-Key": api_key}
    verify_res = post_json(verify_url, verify_payload, timeout, verify_headers)
    
    if verify_res.get("status") != "verified":
        raise Exception("Handshake verification failed on server")
        
    return session_id, session_key

def upload_archive(base_url: str, gateway_id: str, api_key: str, payload: bytes, session_info: tuple[str, bytes] | None, timeout: int) -> dict:
    headers = {}
    
    if session_info:
        session_id, session_key = session_info
        iv = os.urandom(12)
        aesgcm = AESGCM(session_key)
        encrypted_payload = aesgcm.encrypt(iv, payload, None)
        
        headers["X-Session-Id"] = session_id
        headers["X-Session-Iv"] = iv.hex()
        headers["X-Sha256"] = sha256(encrypted_payload).hexdigest()
        headers["X-Gateway-Id"] = gateway_id
        headers["Content-Type"] = "application/octet-stream"
        data = encrypted_payload
    else:
        headers["X-Api-Key"] = api_key
        headers["X-Sha256"] = sha256(payload).hexdigest()
        headers["X-Gateway-Id"] = gateway_id
        headers["Content-Type"] = "application/zip"
        data = payload

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/v1/archive",
        data=data,
        method="PUT",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def simulate_single_gateway(base_url: str, train_id: str, gateway_id: str, api_key: str, train_idx: int, secure: bool, rms_recs: int, peak_recs: int, timeout: int) -> tuple[str, str, bool, str]:
    session_info = None
    try:
        if secure:
            session_info = perform_handshake(base_url, gateway_id, api_key, timeout)
        
        payload = build_zip(train_id, gateway_id, train_idx, rms_recs, peak_recs)
        result = upload_archive(base_url, gateway_id, api_key, payload, session_info, timeout)
        return (train_id, gateway_id, True, f"Success: rms={result.get('rmsRecords')} peakAlerts={result.get('peakAlerts')}")
    except Exception as exc:
        return (train_id, gateway_id, False, f"Failed: {exc}")

def main():
    parser = argparse.ArgumentParser(description="UABAMS Multi-Train Ingestion Load Simulator")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Base URL of the server")
    parser.add_argument("--trains", type=int, default=100, help="Number of trains to simulate")
    parser.add_argument("--secure", action="store_true", help="Enable cryptographic handshake for each upload")
    parser.add_argument("--workers", type=int, default=15, help="Number of concurrent threads for uploads")
    parser.add_argument("--rms-records", type=int, default=30, help="Number of RMS records per zip")
    parser.add_argument("--peak-records", type=int, default=1, help="Number of Peak records per zip")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    
    args = parser.parse_args()

    print("=============================================================")
    print("STARTING MULTI-TRAIN INGESTION SIMULATOR")
    print(f"Server Target: {args.url}")
    print(f"Total Trains:  {args.trains} (2 gateways per train -> {args.trains * 2} uploads)")
    print(f"Secure Mode:   {args.secure}")
    print(f"Concurrency:   {args.workers} worker threads")
    print("=============================================================")

    # Establish tasks to run in thread pool
    tasks = []
    for i in range(1, args.trains + 1):
        train_id = f"TR_{i:03d}"
        # Schedule Gateway 1
        tasks.append((args.url, train_id, GW1_ID, GW1_KEY, i, args.secure, args.rms_records, args.peak_records, args.timeout))
        # Schedule Gateway 2
        tasks.append((args.url, train_id, GW2_ID, GW2_KEY, i, args.secure, args.rms_records, args.peak_records, args.timeout))

    start_time = time.time()
    success_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(simulate_single_gateway, *task): task 
            for task in tasks
        }

        for index, future in enumerate(as_completed(futures)):
            train_id, gateway_id, is_success, msg = future.result()
            if is_success:
                success_count += 1
                status_symbol = "[OK] "
            else:
                fail_count += 1
                status_symbol = "[ERR]"
            
            # Print a status progress bar
            progress = (index + 1) / len(tasks) * 100
            print(f"[{progress:5.1f}%] {status_symbol} Train {train_id} ({gateway_id}): {msg}")

    duration = time.time() - start_time
    print("=============================================================")
    print("INGESTION RUN FINISHED!")
    print(f"Total Time:      {duration:.2f} seconds")
    print(f"Successful:      {success_count} / {len(tasks)}")
    print(f"Failed:          {fail_count} / {len(tasks)}")
    print("=============================================================")

if __name__ == "__main__":
    main()
