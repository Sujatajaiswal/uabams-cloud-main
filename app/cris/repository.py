"""CRIS export repository.

All SQL for reading source telemetry data (rms_records, peak_records,
alert_events) and for reading/writing the cris_exports tracking table.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.database import db


# ── Source data readers ────────────────────────────────────────────────────────

async def fetch_rms_records(
    archive_sha256: str,
    gateway_id: str,
    train_id: str | None = None,
    batch_size: int = 1000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Read a batch of rms_records for the given archive."""
    query = """
        SELECT
            id, train_id, gateway_id, logical_gateway_id, session_name,
            archive_sha256, position_mm, latitude, longitude, gps_valid,
            bearing, speed,
            al_x_g, al_y_g, al_z_g,
            ar_x_g, ar_y_g, ar_z_g,
            bg_x_g, bg_y_g, bg_z_g,
            created_at
        FROM rms_records
        WHERE archive_sha256 = $1
          AND gateway_id    = $2
    """
    params: list[Any] = [archive_sha256, gateway_id]
    if train_id:
        query += " AND train_id = $3"
        params.append(train_id)
    query += " ORDER BY position_mm ASC, id ASC LIMIT $%d OFFSET $%d" % (
        len(params) + 1, len(params) + 2
    )
    params += [batch_size, offset]
    rows = await db.pg_pool.fetch(query, *params)
    return [dict(r) for r in rows]


async def fetch_peak_records(
    archive_sha256: str,
    gateway_id: str,
    train_id: str | None = None,
    batch_size: int = 1000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Read a batch of peak_records for the given archive."""
    query = """
        SELECT
            id, train_id, gateway_id, logical_gateway_id, archive_sha256,
            window_start_mm, window_end_mm, position_mm,
            start_km, end_km, speed_kmph, avg_speed_kmph,
            min_speed_kmph, max_speed_kmph, valid_mask,
            alert_generated, alerts_count, latitude, longitude, axes,
            created_at
        FROM peak_records
        WHERE archive_sha256 = $1
          AND gateway_id    = $2
    """
    params: list[Any] = [archive_sha256, gateway_id]
    if train_id:
        query += " AND train_id = $3"
        params.append(train_id)
    query += " ORDER BY window_start_mm ASC, id ASC LIMIT $%d OFFSET $%d" % (
        len(params) + 1, len(params) + 2
    )
    params += [batch_size, offset]
    rows = await db.pg_pool.fetch(query, *params)
    return [dict(r) for r in rows]


async def fetch_alert_events(
    archive_sha256: str,
    gateway_id: str,
    train_id: str | None = None,
    batch_size: int = 1000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Read a batch of alert_events for the given archive."""
    query = """
        SELECT
            id, train_no, gateway_id, logical_gateway_id,
            alert_type, sensor, axis, channel,
            latitude, longitude, position_mm, location_km,
            start_km, end_km, session_name, archive_sha256,
            source, peak_axis, peak_value_g, threshold_g,
            speed_kmph, window_speed_kmph, min_speed_kmph, max_speed_kmph,
            alerts_count, alert, session_status,
            zone, division, section, archived_at, created_at
        FROM alert_events
        WHERE archive_sha256 = $1
          AND gateway_id    = $2
    """
    params: list[Any] = [archive_sha256, gateway_id]
    if train_id:
        query += " AND train_no = $3"
        params.append(train_id)
    query += " ORDER BY created_at ASC, id ASC LIMIT $%d OFFSET $%d" % (
        len(params) + 1, len(params) + 2
    )
    params += [batch_size, offset]
    rows = await db.pg_pool.fetch(query, *params)
    return [dict(r) for r in rows]


# ── cris_exports CRUD ─────────────────────────────────────────────────────────

async def get_export(export_id: int) -> dict[str, Any] | None:
    """Fetch a single export tracking row by primary key."""
    row = await db.pg_pool.fetchrow(
        "SELECT * FROM cris_exports WHERE id = $1", export_id
    )
    return dict(row) if row else None


async def find_existing_export(
    archive_sha256: str,
    gateway_id: str,
    export_type: str,
) -> dict[str, Any] | None:
    """Look up an export row by the unique (archive_sha256, gateway_id, export_type) key."""
    row = await db.pg_pool.fetchrow(
        """
        SELECT * FROM cris_exports
        WHERE archive_sha256 = $1
          AND gateway_id     = $2
          AND export_type    = $3
        ORDER BY created_at DESC
        LIMIT 1
        """,
        archive_sha256, gateway_id, export_type,
    )
    return dict(row) if row else None


async def create_export_record(
    *,
    archive_sha256: str,
    gateway_id: str,
    export_type: str,
    train_id: str | None = None,
    logical_gateway_id: str | None = None,
    session_name: str | None = None,
    status: str = "PENDING",
) -> int:
    """Insert a new cris_exports row and return its id."""
    row = await db.pg_pool.fetchrow(
        """
        INSERT INTO cris_exports
            (archive_sha256, gateway_id, export_type, train_id,
             logical_gateway_id, session_name, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        archive_sha256, gateway_id, export_type,
        train_id, logical_gateway_id, session_name, status,
    )
    return row["id"]


async def update_export_generated(
    export_id: int,
    *,
    file_path: str,
    file_name: str,
    file_size: int,
    file_sha256: str,
    record_count: int,
    generated_at: datetime,
) -> None:
    """Mark an export as GENERATED with file metadata."""
    await db.pg_pool.execute(
        """
        UPDATE cris_exports SET
            status        = 'GENERATED',
            file_path     = $1,
            file_name     = $2,
            file_size     = $3,
            file_sha256   = $4,
            record_count  = $5,
            generated_at  = $6,
            last_error    = NULL
        WHERE id = $7
        """,
        file_path, file_name, file_size, file_sha256,
        record_count, generated_at, export_id,
    )


async def update_export_uploading(export_id: int) -> None:
    await db.pg_pool.execute(
        """
        UPDATE cris_exports SET
            status             = 'UPLOADING',
            transfer_attempts  = transfer_attempts + 1
        WHERE id = $1
        """,
        export_id,
    )


async def update_export_uploaded(export_id: int, uploaded_at: datetime) -> None:
    await db.pg_pool.execute(
        """
        UPDATE cris_exports SET
            status      = 'UPLOADED',
            uploaded_at = $1,
            last_error  = NULL
        WHERE id = $2
        """,
        uploaded_at, export_id,
    )


async def update_export_failed(export_id: int, error: str) -> None:
    await db.pg_pool.execute(
        """
        UPDATE cris_exports SET
            status     = 'FAILED',
            last_error = $1
        WHERE id = $2
        """,
        error, export_id,
    )


async def list_exports(
    *,
    status: str | None = None,
    train_id: str | None = None,
    gateway_id: str | None = None,
    export_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List cris_exports rows with optional filters."""
    conditions: list[str] = []
    params: list[Any] = []

    if status:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    if train_id:
        params.append(train_id)
        conditions.append(f"train_id = ${len(params)}")
    if gateway_id:
        params.append(gateway_id)
        conditions.append(f"gateway_id = ${len(params)}")
    if export_type:
        params.append(export_type)
        conditions.append(f"export_type = ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]
    rows = await db.pg_pool.fetch(
        f"SELECT * FROM cris_exports {where} "
        f"ORDER BY created_at DESC LIMIT ${len(params)-1} OFFSET ${len(params)}",
        *params,
    )
    return [dict(r) for r in rows]
