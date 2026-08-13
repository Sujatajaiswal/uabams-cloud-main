# 🛰️ UABAMS Cloud Gateway Firmware Integration Guide

This specification document outlines the complete 5-stage integration lifecycle, cryptographic algorithms, key derivation formulas, and implementation code examples for gateway firmware engineers.

---

## 📋 Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Cryptographic Algorithm Reference](#2-cryptographic-algorithm-reference)
3. [5-Stage Integration Pipeline](#3-5-stage-integration-pipeline)
   - [Stage 1: Provisioning & API Key Generation](#stage-1-provisioning--api-key-generation)
   - [Stage 2: Key Exchange Hello](#stage-2-key-exchange-hello)
   - [Stage 3: Session Authentication](#stage-3-session-authentication)
   - [Stage 4: Signature Verification](#stage-4-signature-verification)
   - [Stage 5: Heartbeat & Archive Upload](#stage-5-heartbeat--archive-upload)
4. [Python Firmware Reference Code](#4-python-firmware-reference-code)
5. [C/C++ mbedTLS Firmware Guidelines](#5-cc-mbedtls-firmware-guidelines)

---

## 1. Architecture Overview

The UABAMS Cloud uses a **dual-authentication security pipeline**:
* **Provisioned API Key (`X-Api-Key`):** Stored in non-volatile flash memory on the gateway after initial registration. Used for high-speed automated binary zip uploads (`PUT /api/v1/archive`).
* **Ephemeral ECDH Handshake Session:** Uses Elliptic Curve Diffie-Hellman (SECP256R1) and HMAC-SHA256 to establish verified session keys for dynamic telemetry and command streams.

---

## 2. Cryptographic Algorithm Reference

| Parameter | Specification / Algorithm | Standard Format & Details |
| :--- | :--- | :--- |
| **Client Certificate** | X.509 v3 Certificate | PEM encoded (`-----BEGIN CERTIFICATE-----`). Subject `CN` must equal `gatewayId`. |
| **Elliptic Curve** | SECP256R1 / prime256v1 | NIST P-256 Elliptic Curve. |
| **Public Key Format** | ANSI X9.62 Uncompressed Point | 65 bytes $\rightarrow$ **130 hex characters** starting with `04` (`04` + X-coord + Y-coord). |
| **Key Exchange** | ECDH | Standard Elliptic Curve Diffie-Hellman. |
| **Key Derivation (KDF)**| HKDF-SHA256 | Output: `32 bytes`, Salt: `null`, Info: `b"uabams-handshake-session-key"`. |
| **Handshake Signature**| HMAC-SHA256 | Key: `SessionKey` (32 bytes), Message: `nonce` string. Output: 64-char lowercase hex. |
| **Session Token** | JWT (JSON Web Token) | Algorithm: `HS256`, Validity: 12 Hours. |

---

## 3. 5-Stage Integration Pipeline

### Stage 1: Provisioning & API Key Generation
* **Endpoint:** `POST /api/v1/handshake`
* **Content-Type:** `application/json`
* **Request Body:**
  ```json
  {
    "gatewayId": "GW_UABAMS_BOGIE_01",
    "trainId": "019456",
    "gatewaySerial": "UABAMS_PIL_01",
    "firmwareVersion": "10.06.26",
    "clientCertPem": "-----BEGIN CERTIFICATE-----\n..."
  }
  ```
* **Response (200 OK):**
  ```json
  {
    "status": "success",
    "message": "Handshake successful and API key provisioned",
    "gatewayId": "GW_UABAMS_BOGIE_01",
    "apiKey": "<PROVISIONED_API_KEY>"
  }
  ```
* **Firmware Action:** Save `apiKey` into Non-Volatile Flash Memory.

---

### Stage 2: Key Exchange Hello
* **Endpoint:** `POST /api/v1/handshake/hello`
* **Request Body:**
  ```json
  {
    "gatewayId": "GW_UABAMS_BOGIE_01",
    "clientPublicKey": "048d02bbfffc1c06f99d977557681fb21da893c7834969e127a38f24b78bf76d6b9a16620b52c606d3065ffef8f5b1d7529defdb56649e78f08df9bfd2853e7e77"
  }
  ```
* **Response (200 OK):**
  ```json
  {
    "serverPublicKey": "04453f8dfc04b79b91cf12ae80e9ffd4ceed11d3838589ac0a1ae0ddbed408499084521e86bb80f54d4bba5bf7e5f09917b4fc7e298408e1a72b0d02a77d7575a0",
    "nonce": "d2f6f622d2b6daaa1e656cb8e50d4860",
    "sessionId": "526fc160d6425e6ef468befa810fa7ee"
  }
  ```

---

### Stage 3: Session Authentication
* **Endpoint:** `POST /api/v1/authenticate`
* **Request Body:**
  ```json
  {
    "gatewayId": "GW_UABAMS_BOGIE_01",
    "trainId": "019456",
    "apiKey": "<PROVISIONED_API_KEY>",
    "sessionId": "526fc160d6425e6ef468befa810fa7ee"
  }
  ```
* **Response (200 OK):**
  ```json
  {
    "status": "authenticated",
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "gatewayId": "GW_UABAMS_BOGIE_01",
    "trainId": "019456",
    "sessionId": "526fc160d6425e6ef468befa810fa7ee"
  }
  ```

---

### Stage 4: Signature Verification

#### Mathematical Formulas for Firmware:
1. **Shared Secret:**
   $$\text{SharedSecret} = \text{ECDH\_Exchange}(\text{ClientPrivateKey}, \text{ServerPublicKey})$$
2. **Session Key (HKDF-SHA256):**
   $$\text{SessionKey} = \text{HKDF-SHA256}(\text{ikm}=\text{SharedSecret}, \text{salt}=\text{null}, \text{info}=\text{"uabams-handshake-session-key"}, \text{length}=32)$$
3. **HMAC Signature (HMAC-SHA256):**
   $$\text{clientHmac} = \text{HMAC-SHA256}(\text{key}=\text{SessionKey}, \text{message}=\text{nonce}).\text{hexdigest}()$$

* **Endpoint:** `POST /api/v1/handshake/verify`
* **Request Body:**
  ```json
  {
    "sessionId": "526fc160d6425e6ef468befa810fa7ee",
    "clientHmac": "e71505c7a718234ecddfa1e65999f5c76c18f2e7f2114c83de5a37a0b494d8df"
  }
  ```
* **Response (200 OK):**
  ```json
  {
    "status": "verified",
    "message": "Handshake verified successfully",
    "sessionToken": "526fc160d6425e6ef468befa810fa7ee"
  }
  ```

---

### Stage 5: Heartbeat & Archive Upload

#### A. Periodic Heartbeat (`POST /api/v1/heartbeat`)
* **Frequency:** Every 30 seconds.
* **Request Body:**
  ```json
  {
    "gatewayId": "GW_UABAMS_BOGIE_01",
    "token": "<JWT_TOKEN_FROM_STAGE_3>"
  }
  ```

#### B. Binary ZIP Archive Upload (`PUT /api/v1/archive`)
* **Headers:**
  * `Content-Type: application/octet-stream`
  * `X-Api-Key: <PROVISIONED_API_KEY>`
* **Body:** Binary payload of `SESSION_YYYYMMDD_XXXXXXXXX.zip`.

---

## 4. Python Firmware Reference Code

```python
import hmac
import requests
from hashlib import sha256
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

BASE_URL = "https://uabams-cloud-1.onrender.com"
GATEWAY_ID = "GW_UABAMS_BOGIE_01"
TRAIN_ID = "019456"

# 1. Device Provisioning (Obtain API Key)
cert_pem = "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"
prov_res = requests.post(f"{BASE_URL}/api/v1/handshake", json={
    "gatewayId": GATEWAY_ID,
    "trainId": TRAIN_ID,
    "gatewaySerial": "UABAMS_PIL_01",
    "firmwareVersion": "10.06.26",
    "clientCertPem": cert_pem
}).json()
api_key = prov_res["apiKey"]

# 2. Key Exchange Hello
client_private_key = ec.generate_private_key(ec.SECP256R1())
client_pub_bytes = client_private_key.public_key().public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)
hello_res = requests.post(f"{BASE_URL}/api/v1/handshake/hello", json={
    "gatewayId": GATEWAY_ID,
    "clientPublicKey": client_pub_bytes.hex()
}).json()

server_pub_hex = hello_res["serverPublicKey"]
nonce = hello_res["nonce"]
session_id = hello_res["sessionId"]

# 3. Session Authentication
auth_res = requests.post(f"{BASE_URL}/api/v1/authenticate", json={
    "gatewayId": GATEWAY_ID,
    "trainId": TRAIN_ID,
    "apiKey": api_key,
    "sessionId": session_id
}).json()
token = auth_res["token"]

# 4. Derive Session Key & Compute HMAC
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

verify_res = requests.post(f"{BASE_URL}/api/v1/handshake/verify", json={
    "sessionId": session_id,
    "clientHmac": client_hmac
}).json()

# 5. Heartbeat & Archive Upload
requests.post(f"{BASE_URL}/api/v1/heartbeat", json={"gatewayId": GATEWAY_ID, "token": token})

with open("SESSION_DATA.zip", "rb") as f:
    requests.put(
        f"{BASE_URL}/api/v1/archive",
        headers={"X-Api-Key": api_key, "Content-Type": "application/octet-stream"},
        data=f.read()
    )
```

---

## 5. C/C++ mbedTLS Firmware Guidelines

For embedded microcontrollers (e.g. ESP32 using `mbedtls` or STM32):

1. **ECDH Init:** Use `mbedtls_ecdh_init()`, setup group `MBEDTLS_ECP_DP_SECP256R1`.
2. **Key Generation:** Call `mbedtls_ecdh_gen_public()` to create public key bytes.
3. **Parse Server Point:** Load server's 65-byte uncompressed point using `mbedtls_ecp_point_read_binary()`.
4. **Compute Shared Secret:** Call `mbedtls_ecdh_compute_shared()`.
5. **HKDF Derivation:** Use `mbedtls_hkdf(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), NULL, 0, shared_secret, shared_secret_len, "uabams-handshake-session-key", 28, session_key, 32)`.
6. **HMAC Calculation:** Call `mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), session_key, 32, nonce, nonce_len, hmac_output)`.
7. **Hex String Conversion:** Convert 32-byte `hmac_output` to a 64-character lowercase hex string.
