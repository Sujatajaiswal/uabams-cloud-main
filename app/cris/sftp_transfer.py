"""SFTP transfer implementation for CRIS file delivery.

Uses Paramiko with strict host-key verification.
Password or private-key authentication is supported.

IMPORTANT: This is a provisional implementation.
Obtain the actual CRIS destination hostname, credentials, host key, and
approved protocol from your CRIS/TMS team before enabling production transfer.
"""
from __future__ import annotations

import os

from app.cris.transfer import CrisTransferClient


class SftpTransferClient(CrisTransferClient):
    """SFTP client that uploads a local file to a remote CRIS server.

    Args:
        host:            SFTP server hostname.
        port:            SFTP server port (default 22).
        username:        SFTP login username.
        password:        Plain-text password (optional if private_key given).
        private_key:     PEM-encoded private key string (RSA, Ed25519, ECDSA).
        known_host_line: A single line from a known_hosts file, e.g.
                         ``"sftp.cris.example.com ssh-ed25519 AAAA..."``.
                         Required for strict host-key verification.
        remote_base_dir: Remote directory where files are placed.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 22,
        username: str,
        password: str | None = None,
        private_key: str | None = None,
        known_host_line: str | None = None,
        remote_base_dir: str = "/exports",
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key = private_key
        self.known_host_line = known_host_line
        self.remote_base_dir = remote_base_dir.rstrip("/")

    # ------------------------------------------------------------------
    def upload(self, local_path: str, remote_name: str) -> None:
        """Upload *local_path* to the CRIS SFTP server.

        The file is first written as ``<remote_name>.part`` then atomically
        renamed to ``<remote_name>`` to avoid CRIS reading a partial file.

        Raises:
            RuntimeError: On any SFTP error.
        """
        try:
            import paramiko  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "paramiko is required for SFTP transfer. "
                "Install it with: pip install paramiko"
            ) from exc

        if not self.known_host_line:
            raise RuntimeError(
                "CRIS_SFTP_HOST_KEY is required for strict host-key verification. "
                "Set it to a single known_hosts line for the CRIS SFTP server."
            )

        # Build a HostKeys store from the provided known_hosts line
        host_keys = paramiko.HostKeys()
        try:
            import io as _io
            host_keys.load(
                _io.StringIO(self.known_host_line)  # type: ignore[arg-type]
            )
        except Exception:
            # Fallback: manually parse "hostname keytype base64key"
            parts = self.known_host_line.strip().split()
            if len(parts) < 3:
                raise RuntimeError(
                    f"Invalid CRIS_SFTP_HOST_KEY format: {self.known_host_line!r}"
                )
            hostname_entry, keytype, key_b64 = parts[0], parts[1], parts[2]
            key_cls = {
                "ssh-rsa": paramiko.RSAKey,
                "ssh-ed25519": paramiko.Ed25519Key,
                "ecdsa-sha2-nistp256": paramiko.ECDSAKey,
            }.get(keytype)
            if key_cls is None:
                raise RuntimeError(f"Unsupported SFTP host key type: {keytype!r}")
            import base64
            host_keys.add(
                hostname_entry, keytype,
                key_cls(data=base64.b64decode(key_b64))
            )

        transport = None
        sftp = None
        try:
            transport = paramiko.Transport((self.host, self.port))
            transport.connect()

            # Verify host key against our known_hosts before authenticating
            remote_key = transport.get_remote_server_key()
            expected = host_keys.get(self.host, remote_key.get_name())
            if expected is None or expected.asbytes() != remote_key.asbytes():
                raise RuntimeError(
                    f"SFTP host key verification failed for {self.host}. "
                    "Check CRIS_SFTP_HOST_KEY."
                )

            # Authenticate
            if self.private_key:
                pkey = self._load_private_key(self.private_key, paramiko)
                transport.auth_publickey(self.username, pkey)
            elif self.password:
                transport.auth_password(self.username, self.password)
            else:
                raise RuntimeError(
                    "No SFTP authentication method configured. "
                    "Set CRIS_SFTP_PASSWORD or CRIS_SFTP_PRIVATE_KEY."
                )

            sftp = paramiko.SFTPClient.from_transport(transport)
            assert sftp is not None

            # Ensure remote directory exists
            try:
                sftp.stat(self.remote_base_dir)
            except FileNotFoundError:
                sftp.mkdir(self.remote_base_dir)

            remote_final = f"{self.remote_base_dir}/{remote_name}"
            remote_part  = f"{remote_final}.part"

            sftp.put(local_path, remote_part)
            sftp.rename(remote_part, remote_final)

        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"SFTP transfer to {self.host}:{self.port} failed: {exc}"
            ) from exc
        finally:
            if sftp:
                sftp.close()
            if transport:
                transport.close()

    # ------------------------------------------------------------------
    @staticmethod
    def _load_private_key(pem: str, paramiko: Any) -> Any:  # noqa: ANN401
        import io as _io
        for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
            try:
                return cls.from_private_key(_io.StringIO(pem))
            except Exception:
                pass
        raise RuntimeError("Could not load CRIS_SFTP_PRIVATE_KEY as RSA, Ed25519, or ECDSA key.")


# Convenience alias
from typing import Any
