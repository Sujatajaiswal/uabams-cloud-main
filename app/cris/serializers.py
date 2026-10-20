"""CRIS export serializers.

Converts database records into CSV-compatible text files for each export type.
Column order and header names are provisional pending final CRIS/TMS spec.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

# ── Column definitions per export type ────────────────────────────────────────

SPATIAL_COLUMNS = [
    "id",
    "train_id",
    "gateway_id",
    "logical_gateway_id",
    "session_name",
    "archive_sha256",
    "position_mm",
    "latitude",
    "longitude",
    "gps_valid",
    "bearing",
    "speed",
    # ADXL Left
    "al_x_g",
    "al_y_g",
    "al_z_g",
    # ADXL Right
    "ar_x_g",
    "ar_y_g",
    "ar_z_g",
    # Bogie
    "bg_x_g",
    "bg_y_g",
    "bg_z_g",
    "created_at",
]

PROCESSED_COLUMNS = [
    "id",
    "train_id",
    "gateway_id",
    "logical_gateway_id",
    "archive_sha256",
    "window_start_mm",
    "window_end_mm",
    "position_mm",
    "start_km",
    "end_km",
    "speed_kmph",
    "avg_speed_kmph",
    "min_speed_kmph",
    "max_speed_kmph",
    "valid_mask",
    "alert_generated",
    "alerts_count",
    "latitude",
    "longitude",
    "axes",
    "created_at",
]

ALERTS_COLUMNS = [
    "id",
    "train_no",
    "gateway_id",
    "logical_gateway_id",
    "alert_type",
    "sensor",
    "axis",
    "channel",
    "latitude",
    "longitude",
    "position_mm",
    "location_km",
    "start_km",
    "end_km",
    "session_name",
    "archive_sha256",
    "source",
    "peak_axis",
    "peak_value_g",
    "threshold_g",
    "speed_kmph",
    "window_speed_kmph",
    "min_speed_kmph",
    "max_speed_kmph",
    "alerts_count",
    "alert",
    "session_status",
    "zone",
    "division",
    "section",
    "archived_at",
    "created_at",
]

# Map export type name → (columns list, DB table name)
EXPORT_TYPE_MAP: dict[str, tuple[list[str], str]] = {
    "SPATIAL":   (SPATIAL_COLUMNS,   "rms_records"),
    "PROCESSED": (PROCESSED_COLUMNS, "peak_records"),
    "ALERTS":    (ALERTS_COLUMNS,    "alert_events"),
}


def _format_value(value: Any) -> str:
    """Normalise a single cell value for CSV output."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, float):
        # Avoid scientific notation for very small/large floats
        return f"{value:f}".rstrip("0").rstrip(".")
    return str(value)


def serialize_records(
    records: list[dict[str, Any]],
    export_type: str,
    delimiter: str = ",",
    encoding: str = "UTF-8",
) -> bytes:
    """Serialise a list of DB row dicts to CSV bytes.

    Args:
        records:     List of row dicts from PostgreSQL.
        export_type: One of ``"SPATIAL"``, ``"PROCESSED"``, ``"ALERTS"``.
        delimiter:   Column separator character (default comma).
        encoding:    Output encoding (default UTF-8).

    Returns:
        Encoded CSV bytes (header + data rows).
    """
    if export_type not in EXPORT_TYPE_MAP:
        raise ValueError(f"Unknown export_type: {export_type!r}. "
                         f"Expected one of {list(EXPORT_TYPE_MAP)}")

    columns, _ = EXPORT_TYPE_MAP[export_type]

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL,
                        lineterminator="\r\n")
    writer.writerow(columns)
    for row in records:
        writer.writerow([_format_value(row.get(col)) for col in columns])

    return buf.getvalue().encode(encoding)
