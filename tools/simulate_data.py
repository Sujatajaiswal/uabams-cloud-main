from __future__ import annotations

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
import hmac as python_hmac
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RMS_FORMAT = "<Qifdd?B9f"
PEAK_HEADER_FORMAT = "<iifB?"
PEAK_AXIS_FORMAT = "<fIQdd"
FAULT_FORMAT = "<QBBB64s"
AXIS_COUNT = 9


def lerp(a: float, b: float, ratio: float) -> float:
    return a + (b - a) * ratio


def route_point(index: int, total: int, upload_index: int) -> tuple[float, float]:
    ratio = min(1.0, (upload_index * total + index) / max((200 * total), 1))
    base_lat = lerp(12.9716, 13.0350, ratio)
    base_lon = lerp(77.5946, 77.6400, ratio)
    return base_lat + math.sin(ratio * 12) * 0.0015, base_lon + math.cos(ratio * 10) * 0.0015


def severity_peak(upload_index: int, record_index: int) -> float:
    if (upload_index + record_index) % 37 == 0:
        return random.uniform(82, 95)
    if (upload_index + record_index) % 13 == 0:
        return random.uniform(52, 70)
    return random.uniform(8, 42)


def build_rms(upload_index: int, records: int) -> bytes:
    chunks = []
    for i in range(records):
        lat, lon = route_point(i, records, upload_index)
        max_g = severity_peak(upload_index, i)
        axes = [random.uniform(0.5, max(1.0, max_g * 0.45)) for _ in range(AXIS_COUNT)]
        axes[random.randrange(AXIS_COUNT)] = max_g
        chunks.append(struct.pack(
            RMS_FORMAT,
            upload_index * 100000 + i,
            upload_index * records * 250 + i * 250,
            random.uniform(72, 105),
            lat,
            lon,
            True,
            0xFF,
            *axes,
        ))
    return b"".join(chunks)


def build_peak(upload_index: int, records: int) -> bytes:
    output = bytearray()
    for i in range(records):
        window_start = upload_index * 50000 + i * 50000
        window_end = window_start + 50000
        max_g = severity_peak(upload_index, i * 3)
        alert_generated = max_g > 80
        output += struct.pack(PEAK_HEADER_FORMAT, window_start, window_end, random.uniform(75, 105), 0xFF, alert_generated)
        for axis in range(AXIS_COUNT):
            peak_g = max_g if axis == 0 else random.uniform(2, max(3, max_g * 0.35))
            lat, lon = route_point(i * 4 + axis, 30, upload_index)
            output += struct.pack(PEAK_AXIS_FORMAT, peak_g, window_start + axis * 500, upload_index * 100000 + i, lat, lon)
    return bytes(output)


def build_fault(upload_index: int) -> bytes:
    if upload_index % 20 != 0:
        return b""
    description = f"SIM fault upload {upload_index}".encode("ascii")[:64].ljust(64, b"\0")
    return struct.pack(FAULT_FORMAT, int(time.time() * 1000), 0x30, 1, 2, description)


def build_zip(args: argparse.Namespace, upload_index: int) -> bytes:
    metadata = {
        "sessionName": f"SIM_{args.train_id}_{upload_index:04d}",
        "trainId": args.train_id,
        "gatewayId": args.gateway_id,
        "sessionStatus": "active",
        "simulated": True,
        "uploadIndex": upload_index,
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("session_metadata.json", json.dumps(metadata, indent=2))
        zf.writestr("rms/rms_25cm.bin", build_rms(upload_index, args.rms_records))
        zf.writestr("peak/peak_50m.bin", build_peak(upload_index, args.peak_records))
        zf.writestr("faults/faults.bin", build_fault(upload_index))
    return buffer.getvalue()


def post_json(url: str, payload: dict, timeout: int, headers: dict | None = None) -> dict:
    data_bytes = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data_bytes, method="POST", headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def perform_handshake(args: argparse.Namespace) -> tuple[str, bytes]:
    # 1. Generate client ECDH keys
    client_private_key = ec.generate_private_key(ec.SECP256R1())
    client_public_key = client_private_key.public_key()
    
    client_pub_bytes = client_public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    client_pub_hex = client_pub_bytes.hex()
    
    # 2. Call /api/v1/handshake/hello
    hello_url = f"{args.base_url.rstrip('/')}/api/v1/handshake/hello"
    hello_payload = {
        "gatewayId": args.gateway_id,
        "clientPublicKey": client_pub_hex
    }
    hello_headers = {"X-Api-Key": args.api_key}
    hello_res = post_json(hello_url, hello_payload, args.timeout, hello_headers)
    
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
    verify_url = f"{args.base_url.rstrip('/')}/api/v1/handshake/verify"
    verify_payload = {
        "sessionId": session_id,
        "clientHmac": client_hmac
    }
    verify_headers = {"X-Api-Key": args.api_key}
    verify_res = post_json(verify_url, verify_payload, args.timeout, verify_headers)
    
    if verify_res.get("status") != "verified":
        raise Exception("Handshake verification failed on server")
        
    return session_id, session_key


def upload_archive(args: argparse.Namespace, payload: bytes, session_info: tuple[str, bytes] | None = None) -> dict:
    headers = {}
    
    if session_info:
        session_id, session_key = session_info
        iv = os.urandom(12)
        aesgcm = AESGCM(session_key)
        encrypted_payload = aesgcm.encrypt(iv, payload, None)
        
        headers["X-Session-Id"] = session_id
        headers["X-Session-Iv"] = iv.hex()
        headers["X-Sha256"] = sha256(encrypted_payload).hexdigest()
        headers["Content-Type"] = "application/octet-stream"
        data = encrypted_payload
    else:
        headers["X-Api-Key"] = args.api_key
        headers["X-Sha256"] = sha256(payload).hexdigest()
        headers["Content-Type"] = "application/zip"
        data = payload

    req = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/api/v1/archive",
        data=data,
        method="PUT",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=args.timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and upload UABAMS synthetic archive ZIP files.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--gateway-id", default="GW_UABAMS_BOGIE_01")
    parser.add_argument("--train-id", default="019456")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--rms-records", type=int, default=50)
    parser.add_argument("--peak-records", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--save-dir", default="")
    parser.add_argument("--secure", action="store_true", help="Enable ECDH handshake and symmetric encryption")
    args = parser.parse_args()

    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    session_info = None
    if args.secure:
        try:
            print("Performing cryptographic handshake...")
            session_info = perform_handshake(args)
            print(f"Handshake successful. Established Session ID: {session_info[0]}")
        except Exception as exc:
            print(f"Handshake failed: {exc}")
            return

    success = 0
    for index in range(args.count):
        payload = build_zip(args, index)
        if save_dir:
            (save_dir / f"{args.gateway_id}__{args.train_id}__SIM_{index:04d}.zip").write_bytes(payload)
        try:
            result = upload_archive(args, payload, session_info)
            success += 1
            print(f"{index + 1}/{args.count} uploaded: rms={result.get('rmsRecords')} peakAlerts={result.get('peakAlerts')}")
        except urllib.error.HTTPError as exc:
            print(f"{index + 1}/{args.count} failed: HTTP {exc.code} {exc.read().decode('utf-8', errors='replace')}")
        except Exception as exc:
            print(f"{index + 1}/{args.count} failed: {exc}")
    print(f"Completed {success}/{args.count} uploads")


if __name__ == "__main__":
    main()
