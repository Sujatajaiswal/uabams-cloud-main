"""Tests for CRIS export functionality.

All tests use mock repositories so no real database or SFTP connection is needed.
Run with: pytest tests/test_cris_export.py -v
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from datetime import datetime, UTC
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Serializer tests ───────────────────────────────────────────────────────────

def test_serialize_spatial_columns():
    """SPATIAL output contains expected column headers."""
    from app.cris.serializers import serialize_records, SPATIAL_COLUMNS
    records = [
        {
            "id": 1, "train_id": "22151", "gateway_id": "GW1",
            "logical_gateway_id": None, "session_name": "SESSION_1",
            "archive_sha256": "abc123", "position_mm": 1000,
            "latitude": 12.9716, "longitude": 77.5946, "gps_valid": True,
            "bearing": 45.0, "speed": 60.0,
            "al_x_g": 1.25, "al_y_g": 1.10, "al_z_g": 0.95,
            "ar_x_g": 1.20, "ar_y_g": 1.05, "ar_z_g": 0.90,
            "bg_x_g": 0.80, "bg_y_g": 0.75, "bg_z_g": 0.70,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    ]
    csv_bytes = serialize_records(records, "SPATIAL")
    csv_text = csv_bytes.decode("UTF-8")
    lines = csv_text.strip().split("\r\n")
    assert len(lines) == 2  # header + 1 data row
    headers = lines[0].split(",")
    assert headers == SPATIAL_COLUMNS


def test_serialize_processed_columns():
    """PROCESSED output contains expected column headers."""
    from app.cris.serializers import serialize_records, PROCESSED_COLUMNS
    csv_bytes = serialize_records([], "PROCESSED")
    csv_text = csv_bytes.decode("UTF-8")
    header_line = csv_text.strip()
    assert header_line == ",".join(PROCESSED_COLUMNS)


def test_serialize_alerts_columns():
    """ALERTS output contains expected column headers."""
    from app.cris.serializers import serialize_records, ALERTS_COLUMNS
    csv_bytes = serialize_records([], "ALERTS")
    csv_text = csv_bytes.decode("UTF-8")
    header_line = csv_text.strip()
    assert header_line == ",".join(ALERTS_COLUMNS)


def test_serialize_none_becomes_empty_string():
    """None values are serialised as empty strings."""
    from app.cris.serializers import serialize_records
    null_keys = [
        "gateway_id", "logical_gateway_id", "session_name", "archive_sha256",
        "position_mm", "latitude", "longitude", "gps_valid", "bearing", "speed",
        "al_x_g", "al_y_g", "al_z_g", "ar_x_g", "ar_y_g", "ar_z_g",
        "bg_x_g", "bg_y_g", "bg_z_g", "created_at",
    ]
    row = {"id": 1, "train_id": None}
    row.update({k: None for k in null_keys})
    csv_bytes = serialize_records([row], "SPATIAL")
    data_line = csv_bytes.decode().strip().split("\r\n")[1]
    assert data_line.startswith("1,,")  # id=1, train_id empty


def test_serialize_bool_formatting():
    """Booleans are formatted as true/false (lowercase)."""
    from app.cris.serializers import _format_value
    assert _format_value(True)  == "true"
    assert _format_value(False) == "false"


def test_serialize_datetime_iso():
    """datetime values are formatted as ISO 8601 strings."""
    from app.cris.serializers import _format_value
    dt = datetime(2026, 9, 15, 10, 30, 0, tzinfo=UTC)
    result = _format_value(dt)
    assert "2026-09-15" in result
    assert "10:30:00" in result


def test_serialize_unknown_export_type_raises():
    """Unknown export_type raises ValueError."""
    from app.cris.serializers import serialize_records
    with pytest.raises(ValueError, match="Unknown export_type"):
        serialize_records([], "INVALID")


# ── Repository tests (mocked DB) ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_existing_export_returns_none_when_missing():
    """find_existing_export returns None when no row exists."""
    with patch("app.cris.repository.db") as mock_db:
        mock_db.pg_pool = AsyncMock()
        mock_db.pg_pool.fetchrow = AsyncMock(return_value=None)

        from app.cris import repository as repo
        result = await repo.find_existing_export("sha256abc", "GW1", "SPATIAL")
        assert result is None


@pytest.mark.asyncio
async def test_create_export_record_returns_id():
    """create_export_record inserts a row and returns its id."""
    with patch("app.cris.repository.db") as mock_db:
        mock_db.pg_pool = AsyncMock()
        mock_db.pg_pool.fetchrow = AsyncMock(return_value={"id": 42})

        from app.cris import repository as repo
        eid = await repo.create_export_record(
            archive_sha256="sha256abc",
            gateway_id="GW1",
            export_type="SPATIAL",
            train_id="22151",
        )
        assert eid == 42


@pytest.mark.asyncio
async def test_update_export_generated():
    """update_export_generated calls execute with GENERATED status."""
    with patch("app.cris.repository.db") as mock_db:
        mock_db.pg_pool = AsyncMock()
        mock_db.pg_pool.execute = AsyncMock(return_value=None)

        from app.cris import repository as repo
        await repo.update_export_generated(
            1,
            file_path="/tmp/file.txt",
            file_name="file.txt",
            file_size=1024,
            file_sha256="abc",
            record_count=10,
            generated_at=datetime.now(UTC),
        )
        mock_db.pg_pool.execute.assert_called_once()
        call_sql = mock_db.pg_pool.execute.call_args[0][0]
        assert "GENERATED" in call_sql


# ── Exporter tests (mocked repo + FS) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_export_returns_existing_when_file_present():
    """generate_export returns cached record when file already exists."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp.write(b"existing content")
        tmp_path = tmp.name

    existing_record = {
        "id": 7, "archive_sha256": "sha256abc", "gateway_id": "GW1",
        "export_type": "SPATIAL", "status": "GENERATED",
        "file_path": tmp_path, "file_name": os.path.basename(tmp_path),
    }

    try:
        with patch("app.cris.exporter.repo") as mock_repo:
            mock_repo.find_existing_export = AsyncMock(return_value=existing_record)
            mock_repo.get_export              = AsyncMock(return_value=existing_record)

            from app.cris.exporter import CrisExporter
            result = await CrisExporter().generate_export(
                archive_sha256="sha256abc",
                gateway_id="GW1",
                export_type="SPATIAL",
            )
            assert result["id"] == 7
            mock_repo.find_existing_export.assert_called_once()
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_export_all_for_archive_continues_on_failure():
    """export_all_for_archive continues when one type fails."""
    with patch("app.cris.exporter.repo") as mock_repo:
        call_count = 0

        async def _find_existing(sha, gw, et):
            return None

        async def _create_record(**kwargs):
            return 1

        async def _update_failed(eid, err):
            pass

        async def _get_export(eid):
            return {"id": eid, "status": "FAILED", "export_type": "SPATIAL"}

        mock_repo.find_existing_export = _find_existing
        mock_repo.create_export_record = _create_record
        mock_repo.update_export_failed = _update_failed
        mock_repo.get_export = _get_export

        from app.cris.exporter import CrisExporter
        exporter = CrisExporter()

        # Patch generate_export to fail on SPATIAL but succeed for others
        original_generate = exporter.generate_export

        async def _failing_generate(**kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("export_type") == "SPATIAL":
                raise RuntimeError("Simulated failure")
            return {"id": call_count, "export_type": kwargs["export_type"], "status": "GENERATED"}

        exporter.generate_export = _failing_generate

        results = await exporter.export_all_for_archive(
            archive_sha256="sha256abc",
            gateway_id="GW1",
        )
        # Should still return results for all 3 types (one FAILED, two GENERATED)
        assert len(results) == 3


# ── SFTP transfer tests ────────────────────────────────────────────────────────

def test_sftp_transfer_raises_without_host_key():
    """SftpTransferClient.upload raises RuntimeError when no host key is set."""
    from app.cris.sftp_transfer import SftpTransferClient
    client = SftpTransferClient(
        host="sftp.example.com",
        username="user",
        password="pass",
        known_host_line=None,   # ← missing
    )
    with pytest.raises(RuntimeError, match="CRIS_SFTP_HOST_KEY is required"):
        client.upload("/some/file.txt", "file.txt")


# ── Router authentication tests ───────────────────────────────────────────────

def test_cris_router_auth_rejects_unauthenticated():
    """Unauthenticated requests to CRIS router are rejected with 401."""
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.routers.cris import require_operator_auth

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/cris/exports",
        "headers": [],
        "query_string": b"",
    }
    req = Request(scope)
    with pytest.raises(HTTPException) as exc_info:
        require_operator_auth(req)
    assert exc_info.value.status_code == 401


def test_cris_router_auth_allows_authenticated():
    """Authenticated requests to CRIS router pass dependency check."""
    from starlette.requests import Request
    from app.routers.cris import require_operator_auth
    from app.utils import create_operator_session, OPERATOR_COOKIE_NAME

    token = create_operator_session("admin", "admin")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/cris/exports",
        "headers": [(b"cookie", f"{OPERATOR_COOKIE_NAME}={token}".encode())],
        "query_string": b"",
    }
    req = Request(scope)
    # Should not raise
    require_operator_auth(req)
