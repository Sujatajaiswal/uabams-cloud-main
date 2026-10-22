import os
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv(override=True)


@lru_cache
def get_settings() -> dict[str, object]:
    # Build DATABASE_URL from POSTGRES_* parts if not provided directly
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        pg_host = os.getenv("POSTGRES_HOST", "")
        pg_port = os.getenv("POSTGRES_PORT", "5432")
        pg_user = os.getenv("POSTGRES_USER", "")
        pg_pass = os.getenv("POSTGRES_PASSWORD", "")
        pg_db   = os.getenv("POSTGRES_DB", os.getenv("DATABASE_NAME", ""))
        if pg_host and pg_user and pg_db:
            db_url = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"

    return {
        "database_type": "postgres",
        "database_url": db_url,
        # JWT — default is ≥32 chars to satisfy HS256 minimum key-length
        "jwt_secret": os.getenv(
            "JWT_SECRET",
            "change-this-to-a-long-random-secret-min-32-chars!!"
        ),
        "jwt_algorithm": os.getenv("JWT_ALGORITHM", "HS256"),
        "admin_reset_key": os.getenv("ADMIN_RESET_KEY", ""),
        "admin_username": os.getenv("ADMIN_USERNAME", "admin"),
        "admin_password": os.getenv("ADMIN_PASSWORD", "admin123"),
        "operator_username": os.getenv("OPERATOR_USERNAME", "operator"),
        "operator_password": os.getenv("OPERATOR_PASSWORD", "operator123"),
        "cloud_public_base_url": os.getenv("CLOUD_PUBLIC_BASE_URL", "").rstrip("/"),
        # Gateway SSH upload settings
        "ssh_host": os.getenv("SSH_HOST", "20.8.1.15"),
        "ssh_port": int(os.getenv("SSH_PORT") or "2222"),
        "ssh_user": os.getenv("SSH_USER", "uabams_upload"),
        "ssh_host_key": os.getenv(
            "SSH_HOST_KEY",
            "[20.8.1.15]:2222 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFQFEVhatPXMJx7b0Z0uoRqZIkS8qEDLFb8WoeI0eksr"
        ),
        "authorized_keys_path": os.getenv(
            "AUTHORIZED_KEYS_PATH",
            "/home/uabams_upload/.ssh/authorized_keys"
        ),
        "upload_base_dir": os.getenv("UPLOAD_BASE_DIR", "incoming"),
        # ── CRIS export & transfer settings ───────────────────────────────
        "cris_transfer_enabled": os.getenv("CRIS_TRANSFER_ENABLED", "false").lower() == "true",
        "cris_transfer_protocol": os.getenv("CRIS_TRANSFER_PROTOCOL", "sftp"),
        "cris_sftp_host": os.getenv("CRIS_SFTP_HOST", ""),
        "cris_sftp_port": int(os.getenv("CRIS_SFTP_PORT") or "22"),
        "cris_sftp_username": os.getenv("CRIS_SFTP_USERNAME", ""),
        "cris_sftp_password": os.getenv("CRIS_SFTP_PASSWORD", ""),
        "cris_sftp_private_key": os.getenv("CRIS_SFTP_PRIVATE_KEY", ""),
        "cris_sftp_host_key": os.getenv("CRIS_SFTP_HOST_KEY", ""),
        "cris_sftp_remote_base_path": os.getenv("CRIS_SFTP_REMOTE_BASE_PATH", "/exports"),
        "cris_export_base_dir": os.getenv("CRIS_EXPORT_BASE_DIR", "/app/cris_exports"),
        "cris_export_delimiter": os.getenv("CRIS_EXPORT_DELIMITER", ","),
        "cris_export_encoding": os.getenv("CRIS_EXPORT_ENCODING", "UTF-8"),
        "cris_export_batch_size": int(os.getenv("CRIS_EXPORT_BATCH_SIZE") or "1000"),
    }


settings = get_settings()


class Database:
    def __init__(self) -> None:
        self.pg_pool = None

    async def connect(self) -> None:
        """Connect to PostgreSQL, trying without SSL first then with SSL."""
        import asyncpg  # type: ignore[import]
        url = settings["database_url"]
        try:
            self.pg_pool = await asyncpg.create_pool(url)
        except Exception:
            self.pg_pool = await asyncpg.create_pool(url, ssl="require")

    async def close(self) -> None:
        """Close the PostgreSQL connection pool."""
        if self.pg_pool:
            await self.pg_pool.close()
            self.pg_pool = None


db = Database()
