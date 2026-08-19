import os
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv(override=True)

@lru_cache
def get_settings() -> dict[str, str]:
    db_url = os.getenv("DATABASE_URL", "")
    return {
        "database_type": "postgres",
        "database_url": db_url,
        "jwt_secret": os.getenv("JWT_SECRET", "change-this-secret"),
        "jwt_algorithm": os.getenv("JWT_ALGORITHM", "HS256"),
        "admin_reset_key": os.getenv("ADMIN_RESET_KEY", ""),
        "admin_username": os.getenv("ADMIN_USERNAME", "admin"),
        "admin_password": os.getenv("ADMIN_PASSWORD", "admin123"),
        "operator_username": os.getenv("OPERATOR_USERNAME", "operator"),
        "operator_password": os.getenv("OPERATOR_PASSWORD", "operator123"),
        "cloud_public_base_url": os.getenv("CLOUD_PUBLIC_BASE_URL", "").rstrip("/"),
        "ssh_host": os.getenv("SSH_HOST", "uabams-cloud-1.onrender.com"),
        "ssh_port": int(os.getenv("SSH_PORT", "22")),
        "ssh_user": os.getenv("SSH_USER", "uabams_upload"),
        "ssh_host_key": os.getenv("SSH_HOST_KEY", "uabams-cloud-1.onrender.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICa5f57B8H7wW70QGqE/D8w4bHcxPDmlcnNf2V2Rl0tH"),
        "authorized_keys_path": os.getenv("AUTHORIZED_KEYS_PATH", "/home/uabams_upload/.ssh/authorized_keys"),
        "upload_base_dir": os.getenv("UPLOAD_BASE_DIR", "incoming"),
    }

settings = get_settings()

class Database:
    def __init__(self):
        self.pg_pool = None

db = Database()
