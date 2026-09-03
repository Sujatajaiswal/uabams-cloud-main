import hashlib
import hmac
import uuid
from secrets import token_hex
from datetime import timedelta
from passlib.hash import bcrypt

import re

def validate_username(username: str):
    if not re.match(r'^[A-Za-z0-9_]+$', username):
        raise HTTPException(status_code=400, detail="Invalid Username. Use only letters, numbers, and underscores (no spaces).")

def validate_password(password: str):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")
    if not re.search(r'[A-Z]', password):
        raise HTTPException(status_code=400, detail="Password must contain at least 1 uppercase letter.")
    if not re.search(r'[a-z]', password):
        raise HTTPException(status_code=400, detail="Password must contain at least 1 lowercase letter.")
    if not re.search(r'\d', password):
        raise HTTPException(status_code=400, detail="Password must contain at least 1 number.")
    if not re.search(r'[@#$%!]', password):
        raise HTTPException(status_code=400, detail="Password must contain at least 1 special character (@, #, $, %, !).")

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from hmac import compare_digest

from fastapi import APIRouter, HTTPException, Request

from app.database import db, settings
from app.models import (
    HandshakeRequest,
    HandshakeVerifyRequest,
    HandshakeVerifyResponse,
    HandshakeHelloRequest,
    HandshakeHelloResponse,
    AuthRequest,
    UserCreateRequest,
    UserUpdateRequest
)
from app.utils import utc_now, create_gateway_token, operator_session_payload

router = APIRouter()

@router.post("/api/v1/handshake")
async def handshake(data: HandshakeRequest, request: Request):
    try:
        now = utc_now()
        cert_pem = request.headers.get("X-Client-Cert") or data.clientCertPem
        cert_fingerprint = None
        cert_gateway_id = None

        if cert_pem:
            try:
                cert_bytes = cert_pem.replace("\\n", "\n").encode("utf-8")
                cert_fingerprint = hashlib.sha256(cert_bytes).hexdigest()
                cert = x509.load_pem_x509_certificate(cert_bytes, default_backend())
                cn_attributes = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
                if cn_attributes:
                    cert_gateway_id = cn_attributes[0].value
            except Exception as exc:
                raise HTTPException(status_code=403, detail=f"Invalid or untrusted device certificate: {exc}")

        gateway_id = cert_gateway_id or data.gatewayId

        # Validate sshPublicKey if provided
        if data.sshPublicKey:
            cleaned_key = data.sshPublicKey.strip()
            valid_prefixes = ("ssh-rsa", "ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-dss")
            if not any(cleaned_key.startswith(p) for p in valid_prefixes):
                raise HTTPException(status_code=400, detail="Invalid SSH public key format")
                
            other_auth = await db.pg_pool.fetchrow(
                "SELECT gateway_id FROM gateway_auth WHERE ssh_public_key = $1", 
                cleaned_key
            )
            if other_auth and other_auth["gateway_id"] != gateway_id:
                raise HTTPException(status_code=400, detail="SSH public key is already associated with another gateway")

        await db.pg_pool.execute(
            """
            INSERT INTO gateways (gateway_id, train_id, gateway_serial, firmware_version, status, last_seen, updated_at, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (gateway_id) DO UPDATE SET
                train_id = EXCLUDED.train_id,
                gateway_serial = EXCLUDED.gateway_serial,
                firmware_version = EXCLUDED.firmware_version,
                status = EXCLUDED.status,
                last_seen = EXCLUDED.last_seen,
                updated_at = EXCLUDED.updated_at
            """,
            gateway_id, data.trainId, data.gatewaySerial, data.firmwareVersion, "active", now, now, now
        )

        auth_doc = await db.pg_pool.fetchrow(
            "SELECT secret_key AS \"apiKey\" FROM gateway_auth WHERE gateway_id = $1 AND train_id = $2",
            gateway_id, data.trainId
        )
        api_key = None
        if auth_doc:
            api_key = auth_doc.get("apiKey")
        if not api_key:
            api_key = token_hex(32)

        ssh_pub_key = data.sshPublicKey.strip() if data.sshPublicKey else None
        upload_base_path = f"/incoming/{data.trainId}/{gateway_id}" if data.sshPublicKey else None
        upload_enabled = True if data.sshPublicKey else False

        await db.pg_pool.execute(
            """
            INSERT INTO gateway_auth (gateway_id, train_id, secret_key, cert_fingerprint, ssh_public_key, upload_enabled, upload_base_path, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (gateway_id, train_id) DO UPDATE SET
                secret_key = EXCLUDED.secret_key,
                cert_fingerprint = EXCLUDED.cert_fingerprint,
                ssh_public_key = COALESCE(EXCLUDED.ssh_public_key, gateway_auth.ssh_public_key),
                upload_enabled = COALESCE(EXCLUDED.upload_enabled, gateway_auth.upload_enabled),
                upload_base_path = COALESCE(EXCLUDED.upload_base_path, gateway_auth.upload_base_path)
            """,
            gateway_id, data.trainId, api_key, cert_fingerprint, ssh_pub_key, upload_enabled, upload_base_path, now
        )
        
        await db.pg_pool.execute(
            """
            INSERT INTO gateway_status (gateway_id, train_id, last_handshake)
            VALUES ($1, $2, $3)
            ON CONFLICT (gateway_id) DO UPDATE SET
                train_id = EXCLUDED.train_id,
                last_handshake = EXCLUDED.last_handshake
            """,
            gateway_id, data.trainId, now
        )

        upload_config = {
            "enabled": True,
            "host": settings["ssh_host"],
            "port": settings["ssh_port"],
            "user": settings["ssh_user"],
            "basePath": f"/incoming/{data.trainId}/{gateway_id}",
            "sshHostKey": settings["ssh_host_key"]
        }

        return {
            "status": "success",
            "message": "Handshake successful and API key provisioned",
            "gatewayId": gateway_id,
            "apiKey": api_key,
            "upload": upload_config
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Handshake error: {exc}")


@router.post("/api/v1/handshake/hello", response_model=HandshakeHelloResponse)
async def handshake_hello(data: HandshakeHelloRequest):
    try:
        bytes.fromhex(data.clientPublicKey)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid clientPublicKey hex format. Please provide a valid 130-character hex string (SECP256R1 uncompressed point starting with 04).")

    gateway = await db.pg_pool.fetchrow("SELECT gateway_id FROM gateways WHERE gateway_id = $1", data.gatewayId)
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not registered")

    # 1. Generate server ephemeral key pair
    server_private_key = ec.generate_private_key(ec.SECP256R1())
    server_public_key = server_private_key.public_key()

    # 2. Serialize keys to hex
    server_pub_bytes = server_public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    server_pub_hex = server_pub_bytes.hex()

    server_priv_bytes = server_private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    server_priv_hex = server_priv_bytes.hex()

    # 3. Create challenge nonce & session ID
    nonce = token_hex(16)
    session_id = token_hex(16)

    # 4. Save session state in PostgreSQL
    await db.pg_pool.execute(
        """
        INSERT INTO handshake_sessions (session_id, gateway_id, server_private_key_hex, client_public_key_hex, nonce, verified, authenticated, created_at)
        VALUES ($1, $2, $3, $4, $5, FALSE, FALSE, $6)
        """,
        session_id, data.gatewayId, server_priv_hex, data.clientPublicKey, nonce, utc_now()
    )

    return HandshakeHelloResponse(
        serverPublicKey=server_pub_hex,
        nonce=nonce,
        sessionId=session_id
    )


@router.post("/api/v1/handshake/verify", response_model=HandshakeVerifyResponse)
async def handshake_verify(data: HandshakeVerifyRequest):
    session = await db.pg_pool.fetchrow(
        "SELECT session_id AS \"sessionId\", server_private_key_hex AS \"serverPrivateKeyHex\", client_public_key_hex AS \"clientPublicKeyHex\", nonce, authenticated FROM handshake_sessions WHERE session_id = $1",
        data.sessionId
    )
    if not session:
        raise HTTPException(status_code=404, detail="Handshake session not found or expired")

    if not session.get("authenticated"):
        raise HTTPException(status_code=403, detail="Session not authenticated. Run /api/v1/authenticate first.")

    try:
        # 1. Load keys
        server_private_key = serialization.load_der_private_key(
            bytes.fromhex(session["serverPrivateKeyHex"]),
            password=None
        )
        client_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(),
            bytes.fromhex(session["clientPublicKeyHex"])
        )

        # 2. Compute Diffie-Hellman Shared Secret
        shared_key = server_private_key.exchange(ec.ECDH(), client_public_key)

        # 3. Derive symmetric key via HKDF
        session_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"uabams-handshake-session-key",
        ).derive(shared_key)

        # 4. Compute expected HMAC
        expected_hmac = hmac.new(
            session_key,
            session["nonce"].encode("utf-8"),
            digestmod=hashlib.sha256
        ).hexdigest()

        # 5. Compare signatures using timing-safe compare_digest
        if not compare_digest(data.clientHmac.lower(), expected_hmac.lower()):
            raise HTTPException(status_code=401, detail="HMAC verification failed")

        # 6. Save derived session key & verify session
        await db.pg_pool.execute(
            """
            UPDATE handshake_sessions 
            SET verified = TRUE, session_key_hex = $1, verified_at = $2 
            WHERE session_id = $3
            """,
            session_key.hex(), utc_now(), data.sessionId
        )

        return HandshakeVerifyResponse(
            status="verified",
            message="Handshake verified successfully",
            sessionToken=data.sessionId
        )

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid public key: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Handshake error: {exc}")


@router.post("/api/v1/authenticate")
async def authenticate(data: AuthRequest):
    gateway_id = data.gatewayId
    train_id = data.trainId

    # 1. Verify session exists
    session = await db.pg_pool.fetchrow(
        "SELECT session_id FROM handshake_sessions WHERE session_id = $1",
        data.sessionId
    )
    if not session:
        raise HTTPException(status_code=404, detail="Handshake session not found or expired")

    # 2. Look up compound gateway_auth by both gatewayId and trainId
    auth_doc = await db.pg_pool.fetchrow(
        "SELECT secret_key AS \"apiKey\", cert_fingerprint AS \"certFingerprint\" FROM gateway_auth WHERE gateway_id = $1 AND train_id = $2",
        gateway_id, train_id
    )
    if not auth_doc:
        return {"status": "failed", "message": f"Gateway {gateway_id} on Train {train_id} not registered"}

    stored_key = auth_doc.get("apiKey")
    if stored_key != data.apiKey:
        return {"status": "failed", "message": "Invalid API Key"}

    # 3. Generate token and update session to authenticated
    token = create_gateway_token(gateway_id, train_id)
    
    await db.pg_pool.execute(
        """
        UPDATE handshake_sessions 
        SET authenticated = TRUE, train_id = $1, gateway_id = $2 
        WHERE session_id = $3
        """,
        train_id, gateway_id, data.sessionId
    )

    fingerprint = auth_doc.get("certFingerprint")
    if not fingerprint:
        sec_key = stored_key or "default_secret"
        fingerprint = hashlib.sha256(sec_key.encode("utf-8")).hexdigest()

    await db.pg_pool.execute(
        """
        UPDATE gateway_auth 
        SET last_authenticated = $1, cert_fingerprint = $2 
        WHERE gateway_id = $3 AND train_id = $4
        """,
        utc_now(), fingerprint, gateway_id, train_id
    )

    return {
        "status": "authenticated",
        "token": token,
        "gatewayId": gateway_id,
        "trainId": train_id,
        "sessionId": data.sessionId
    }


@router.get("/api/v1/users")
async def get_users(request: Request):
    payload = operator_session_payload(request)
    if not payload or not payload.get("can_manage_users"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    if db.pg_pool:
        users = await db.pg_pool.fetch(
            "SELECT id, username, role, can_configure_thresholds AS \"can_configure_thresholds\", can_manage_users AS \"can_manage_users\", can_view_alerts AS \"can_view_alerts\", is_active AS \"is_active\", created_at AS \"created_at\" FROM users ORDER BY id ASC"
        )
        return [dict(u) for u in users]
    return []

@router.post("/api/v1/users")
async def create_user(data: UserCreateRequest, request: Request):
    payload = operator_session_payload(request)
    if not payload or not payload.get("can_manage_users"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    if db.pg_pool:
        existing = await db.pg_pool.fetchrow("SELECT id FROM users WHERE username = $1", data.username)
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")
        
        validate_username(data.username)
        validate_password(data.password)
        
        hashed_pw = bcrypt.hash(data.password)
        await db.pg_pool.execute(
            "INSERT INTO users (username, password_hash, role, can_configure_thresholds, can_manage_users, can_view_alerts, is_active) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            data.username, hashed_pw, data.role.lower(), data.can_configure_thresholds, data.can_manage_users, data.can_view_alerts, True
        )
        return {"status": "success", "message": "User created"}
    return {"status": "error"}

@router.put("/api/v1/users/{user_id}")
async def update_user(user_id: int, data: UserUpdateRequest, request: Request):
    payload = operator_session_payload(request)
    if not payload or not payload.get("can_manage_users"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    if db.pg_pool:
        user = await db.pg_pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if data.username is not None and data.username != user['username']:
            validate_username(data.username)
            existing = await db.pg_pool.fetchrow("SELECT id FROM users WHERE username = $1", data.username)
            if existing:
                raise HTTPException(status_code=400, detail="Username already exists")
            
            # Prevent renaming the default admin
            if user['username'] == 'admin':
                raise HTTPException(status_code=400, detail="Cannot change the username of the default admin account")

        
        updates = []
        params = []
        idx = 1
        if data.username is not None and data.username != user['username']:
            updates.append(f"username = ${idx}")
            params.append(data.username)
            idx += 1
        if data.role is not None:
            updates.append(f"role = ${idx}")
            params.append(data.role.lower())
            idx += 1
        if data.password:
            updates.append(f"password_hash = ${idx}")
            params.append(bcrypt.hash(data.password))
            idx += 1
        if data.can_configure_thresholds is not None:
            updates.append(f"can_configure_thresholds = ${idx}")
            params.append(data.can_configure_thresholds)
            idx += 1
        if data.can_manage_users is not None:
            updates.append(f"can_manage_users = ${idx}")
            params.append(data.can_manage_users)
            idx += 1
        if data.can_view_alerts is not None:
            updates.append(f"can_view_alerts = ${idx}")
            params.append(data.can_view_alerts)
            idx += 1
        if data.is_active is not None:
            updates.append(f"is_active = ${idx}")
            params.append(data.is_active)
            idx += 1
            
        if updates:
            params.append(user_id)
            query = f"UPDATE users SET {', '.join(updates)} WHERE id = ${idx}"
            await db.pg_pool.execute(query, *params)
        
        return {"status": "success", "message": "User updated"}
    return {"status": "error"}

@router.delete("/api/v1/users/{user_id}")
async def delete_user(user_id: int, request: Request):
    payload = operator_session_payload(request)
    if not payload or not payload.get("can_manage_users"):
        raise HTTPException(status_code=403, detail="Permission denied")
        
    if db.pg_pool:
        # Prevent deleting the admin account
        user = await db.pg_pool.fetchrow("SELECT username FROM users WHERE id = $1", user_id)
        if user and user['username'] == 'admin':
            raise HTTPException(status_code=400, detail="Cannot delete default admin user")
        await db.pg_pool.execute("DELETE FROM users WHERE id = $1", user_id)
        return {"status": "success", "message": "User deleted"}
    return {"status": "error"}

@router.get('/api/v1/auth/me')
async def get_me(request: Request):
    payload = operator_session_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail='Not authenticated')
    return {
        'username': payload.get('sub', ''),
        'role': payload.get('role', 'operator').lower(),
        'permissions': {
            'can_configure_thresholds': payload.get('can_configure_thresholds', False),
            'can_manage_users': payload.get('can_manage_users', False),
            'can_view_alerts': payload.get('can_view_alerts', True)
        }
    }
