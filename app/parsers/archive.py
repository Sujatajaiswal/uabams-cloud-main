from __future__ import annotations

import json
import math
import struct
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any


RMS_GATEWAY_RECORD_SIZE = 70
RMS_GATEWAY_FORMAT = "<Qifdd?B9f"
RMS_LEGACY_RECORD_SIZE = 66
RMS_LEGACY_FORMAT = "<Qidd?BIIIIIIIII"

PEAK_RECORD_SIZE = 302
PEAK_HEADER_FORMAT = "<iifB?"
PEAK_AXIS_FORMAT_GATEWAY = "<fIQdd"
PEAK_AXIS_FORMAT_LEGACY = "<IiQdd"
PEAK_AXIS_SIZE = 32

FAULT_RECORD_SIZE = 75
FAULT_FORMAT = "<QBBB64s"

SENTINEL_U32 = 0xFFFFFFFF
AXIS_NAMES = ("al_x", "al_y", "al_z", "ar_x", "ar_y", "ar_z", "bg_x", "bg_y", "bg_z")
EXPECTED_RMS_INTERVAL_MM = 250
RMS_INTERVAL_TOLERANCE_MM = 25

FAULT_CODE_NAMES = {
    0x00: "FAULT_NONE",
    0x10: "FAULT_NODE_TIMEOUT",
    0x11: "FAULT_CRC_ERROR",
    0x20: "FAULT_SD_CARD_MISSING",
    0x21: "FAULT_SD_CARD_FULL",
    0x22: "FAULT_STORAGE_WRITE",
    0x30: "FAULT_GPS_LOST",
    0x40: "FAULT_UPLOAD_FAILED",
    0x50: "FAULT_CONFIG_INVALID",
    0x60: "FAULT_SEGMENT_INVALID",
    0x61: "FAULT_COUNT_JUMP",
    0x62: "FAULT_ALL_VIBRATION_MISSING",
}


@dataclass
class ParsedArchive:
    metadata: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    rms_records: list[dict[str, Any]] = field(default_factory=list)
    peak_records: list[dict[str, Any]] = field(default_factory=list)
    fault_records: list[dict[str, Any]] = field(default_factory=list)
    raw_file_manifest: list[dict[str, Any]] = field(default_factory=list)
    raw_files: list[dict[str, Any]] = field(default_factory=list)
    rms_validation: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def parse_archive_zip(body: bytes | str) -> ParsedArchive:
    try:
        archive_file = body if isinstance(body, str) else BytesIO(body)
        with zipfile.ZipFile(archive_file) as archive:
            result = ParsedArchive(files=archive.namelist())
            result.metadata = _read_metadata(archive, result.warnings)
            result.raw_files = _read_raw_files(archive)
            result.raw_file_manifest = [
                {"path": item["path"], "sizeBytes": item["sizeBytes"]}
                for item in result.raw_files
            ]

            rms_name = _find_member(archive, "rms/rms_25cm.bin")
            if rms_name:
                result.rms_records = parse_rms_bytes(archive.read(rms_name), result.warnings)
                result.rms_validation = validate_rms_intervals(
                    result.rms_records,
                    result.warnings,
                )
            else:
                result.warnings.append("Missing rms/rms_25cm.bin")

            peak_name = _find_member(archive, "peak/peak_50m.bin")
            if peak_name:
                result.peak_records = parse_peak_bytes(archive.read(peak_name), result.warnings)
            else:
                result.warnings.append("Missing peak/peak_50m.bin")

            fault_name = _find_member(archive, "faults/faults.bin")
            if fault_name:
                result.fault_records = parse_fault_bytes(archive.read(fault_name), result.warnings)
            else:
                result.warnings.append("Missing faults/faults.bin")

            return result
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid ZIP file") from exc


def parse_rms_bytes(raw: bytes, warnings: list[str] | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    if len(raw) % RMS_GATEWAY_RECORD_SIZE == 0:
        record_size = RMS_GATEWAY_RECORD_SIZE
        usable = len(raw)
        for offset in range(0, usable, record_size):
            chunk = raw[offset : offset + record_size]
            unpacked = struct.unpack(RMS_GATEWAY_FORMAT, chunk)
            master_count, position_mm, speed_kmph, latitude, longitude, gps_valid, valid_mask, *axis_g = unpacked
            axis_values = {name: _g_value(value) for name, value in zip(AXIS_NAMES, axis_g)}
            valid_axis_g = [item["g"] for item in axis_values.values() if item["g"] is not None]
            max_g = max(valid_axis_g, default=0.0)

            record: dict[str, Any] = {
                "recordIndex": offset // record_size,
                "masterCount": master_count,
                "positionMm": position_mm,
                "speedKmph": round(speed_kmph, 2),
                "latitude": latitude,
                "longitude": longitude,
                "gpsValid": gps_valid,
                "validMask": valid_mask,
                "maxG": round(max_g, 4),
                "maxMg": int(round(max_g * 1000)),
                "color": _color_for_g(max_g),
                "recordFormat": "gateway_70_byte_float_g",
            }
            for axis_name, value in axis_values.items():
                record[f"{axis_name}_mg"] = value["mg"]
                record[f"{axis_name}_g"] = value["g"]
            records.append(record)
        return records

    _warn_on_remainder("rms/rms_25cm.bin", raw, RMS_LEGACY_RECORD_SIZE, warnings)
    usable = len(raw) - (len(raw) % RMS_LEGACY_RECORD_SIZE)

    for offset in range(0, usable, RMS_LEGACY_RECORD_SIZE):
        chunk = raw[offset : offset + RMS_LEGACY_RECORD_SIZE]
        unpacked = struct.unpack(RMS_LEGACY_FORMAT, chunk)
        master_count, position_mm, latitude, longitude, gps_valid, valid_mask, *axis_mg = unpacked
        axis_values = {name: _mg_value(value) for name, value in zip(AXIS_NAMES, axis_mg)}
        valid_axis_g = [item["g"] for item in axis_values.values() if item["g"] is not None]
        max_g = max(valid_axis_g, default=0.0)

        record = {
            "recordIndex": offset // RMS_LEGACY_RECORD_SIZE,
            "masterCount": master_count,
            "positionMm": position_mm,
            "latitude": latitude,
            "longitude": longitude,
            "gpsValid": gps_valid,
            "validMask": valid_mask,
            "maxG": round(max_g, 4),
            "maxMg": int(round(max_g * 1000)),
            "color": _color_for_g(max_g),
            "recordFormat": "legacy_66_byte_uint_mg",
        }
        for axis_name, value in axis_values.items():
            record[f"{axis_name}_mg"] = value["mg"]
            record[f"{axis_name}_g"] = value["g"]
        records.append(record)

    return records

def parse_peak_bytes(raw: bytes, warnings: list[str] | None = None) -> list[dict[str, Any]]:
    _warn_on_remainder("peak/peak_50m.bin", raw, PEAK_RECORD_SIZE, warnings)
    records: list[dict[str, Any]] = []
    usable = len(raw) - (len(raw) % PEAK_RECORD_SIZE)

    for offset in range(0, usable, PEAK_RECORD_SIZE):
        chunk = raw[offset : offset + PEAK_RECORD_SIZE]
        window_start, window_end, speed_kmph, valid_mask, alert_generated = struct.unpack_from(
            PEAK_HEADER_FORMAT, chunk, 0
        )
        axes: dict[str, Any] = {}

        for index, axis_name in enumerate(AXIS_NAMES):
            base = 14 + index * PEAK_AXIS_SIZE
            axes[axis_name] = _parse_peak_axis(chunk, base)

        max_axis, max_axis_data = _max_peak_axis(axes)
        max_g = max_axis_data.get("peakValueG") or 0.0
        records.append(
            {
                "recordIndex": offset // PEAK_RECORD_SIZE,
                "windowStartMm": window_start,
                "windowEndMm": window_end,
                "speedKmph": round(speed_kmph, 2),
                "validMask": valid_mask,
                "alertGenerated": alert_generated,
                "axes": axes,
                "maxPeakAxis": max_axis,
                "maxPeakMg": max_axis_data.get("peakValueMg"),
                "maxPeakG": round(max_g, 4),
                "latitude": max_axis_data.get("peakLat"),
                "longitude": max_axis_data.get("peakLon"),
                "positionMm": max_axis_data.get("peakPositionMm"),
                "masterCount": max_axis_data.get("peakMasterCount"),
                "color": _color_for_g(max_g),
            }
        )

    return records


def _parse_peak_axis(chunk: bytes, base: int) -> dict[str, Any]:
    peak_mg, peak_position, peak_master_count, peak_lat, peak_lon = struct.unpack_from(
        PEAK_AXIS_FORMAT_LEGACY, chunk, base
    )
    legacy_value = _mg_value(peak_mg)
    legacy_g = legacy_value["g"]
    if legacy_g is not None and 0 <= legacy_g <= 1000:
        return {
            "peakValueMg": legacy_value["mg"],
            "peakValueG": legacy_value["g"],
            "peakPositionMm": peak_position,
            "peakMasterCount": peak_master_count,
            "peakLat": peak_lat,
            "peakLon": peak_lon,
            "recordFormat": "legacy_uint_mg",
        }

    peak_g, peak_position, peak_master_count, peak_lat, peak_lon = struct.unpack_from(
        PEAK_AXIS_FORMAT_GATEWAY, chunk, base
    )
    if not math.isfinite(peak_g):
        peak_g = 0.0
    return {
        "peakValueMg": int(round(peak_g * 1000)),
        "peakValueG": round(peak_g, 4),
        "peakPositionMm": peak_position,
        "peakMasterCount": peak_master_count,
        "peakLat": peak_lat,
        "peakLon": peak_lon,
        "recordFormat": "gateway_float_g",
    }

def parse_fault_bytes(raw: bytes, warnings: list[str] | None = None) -> list[dict[str, Any]]:
    _warn_on_remainder("faults/faults.bin", raw, FAULT_RECORD_SIZE, warnings)
    records: list[dict[str, Any]] = []
    usable = len(raw) - (len(raw) % FAULT_RECORD_SIZE)

    for offset in range(0, usable, FAULT_RECORD_SIZE):
        timestamp_ms, fault_code, node_id, severity, description = struct.unpack(
            FAULT_FORMAT, raw[offset : offset + FAULT_RECORD_SIZE]
        )
        records.append(
            {
                "recordIndex": offset // FAULT_RECORD_SIZE,
                "timestampMs": timestamp_ms,
                "faultCode": fault_code,
                "faultName": FAULT_CODE_NAMES.get(fault_code, "FAULT_UNKNOWN"),
                "nodeId": node_id,
                "severity": severity,
                "description": description.split(b"\x00", 1)[0].decode("ascii", errors="replace"),
            }
        )

    return records


def validate_rms_intervals(
    records: list[dict[str, Any]],
    warnings: list[str] | None = None,
    expected_mm: int = EXPECTED_RMS_INTERVAL_MM,
    tolerance_mm: int = RMS_INTERVAL_TOLERANCE_MM,
) -> dict[str, Any]:
    if not records:
        return {
            "expectedIntervalMm": expected_mm,
            "toleranceMm": tolerance_mm,
            "totalIntervals": 0,
            "validIntervals": 0,
            "invalidIntervals": 0,
            "validPercent": 100.0,
        }

    records[0]["spatialIntervalMm"] = None
    records[0]["spatialIntervalValid"] = None
    intervals: list[int] = []
    invalid = 0

    previous_position = records[0].get("positionMm")
    for record in records[1:]:
        position = record.get("positionMm")
        if position is None or previous_position is None:
            record["spatialIntervalMm"] = None
            record["spatialIntervalValid"] = False
            invalid += 1
        else:
            interval = abs(int(position) - int(previous_position))
            valid = abs(interval - expected_mm) <= tolerance_mm
            record["spatialIntervalMm"] = interval
            record["spatialIntervalValid"] = valid
            intervals.append(interval)
            if not valid:
                invalid += 1
        previous_position = position

    total = len(records) - 1
    valid_count = total - invalid
    summary = {
        "expectedIntervalMm": expected_mm,
        "toleranceMm": tolerance_mm,
        "totalIntervals": total,
        "validIntervals": valid_count,
        "invalidIntervals": invalid,
        "validPercent": round((valid_count / total) * 100, 2) if total else 100.0,
        "minimumIntervalMm": min(intervals) if intervals else None,
        "maximumIntervalMm": max(intervals) if intervals else None,
        "averageIntervalMm": round(sum(intervals) / len(intervals), 2) if intervals else None,
    }
    if invalid and warnings is not None:
        warnings.append(
            f"rms/rms_25cm.bin has {invalid} of {total} intervals outside "
            f"{expected_mm} +/- {tolerance_mm} mm"
        )
    return summary


def peak_records_to_alert_events(
    peak_records: list[dict[str, Any]],
    gateway_id: str,
    train_id: str,
    session_name: str,
    archive_sha256: str,
    created_at: Any,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in peak_records:
        latitude = record.get("latitude")
        longitude = record.get("longitude")
        if latitude in (None, 0) or longitude in (None, 0):
            continue
        events.append(
            {
                "gatewayId": gateway_id,
                "trainNo": train_id,
                "sessionName": session_name,
                "archiveSha256": archive_sha256,
                "source": "peak_50m.bin",
                "peakAxis": record.get("maxPeakAxis"),
                "peakValueG": record.get("maxPeakG", 0),
                "positionMm": record.get("positionMm"),
                "speedKmph": record.get("speedKmph"),
                "latitude": latitude,
                "longitude": longitude,
                "alert": record.get("color", "GREEN"),
                "createdAt": created_at,
            }
        )
    return events


def _read_metadata(archive: zipfile.ZipFile, warnings: list[str]) -> dict[str, Any]:
    metadata_candidates = [
        "session_metadata.json",
        "metadata.json",
        "manifest.json",
        "session.json",
        "header.json"
    ]
    member = None
    for cand in metadata_candidates:
        member = _find_member(archive, cand)
        if member:
            break

    if not member:
        warnings.append("Missing metadata JSON file (expected session_metadata.json, metadata.json, or manifest.json)")
        return {}

    try:
        data = json.loads(archive.read(member).decode("utf-8"))
        if isinstance(data, dict):
            if "createdUtc" in data and "createdAt" not in data:
                data["createdAt"] = data["createdUtc"]
            if "created_utc" in data and "createdAt" not in data:
                data["createdAt"] = data["created_utc"]
        return data
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        warnings.append(f"Invalid metadata JSON ({member}): {exc}")
        return {}


def _read_raw_files(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    files = []
    for info in archive.infolist():
        normalized = _normalize_path(info.filename)
        path_parts = normalized.split("/")
        if "raw" in path_parts and not info.is_dir():
            raw_index = path_parts.index("raw")
            files.append(
                {
                    "path": "/".join(path_parts[raw_index:]),
                    "sizeBytes": info.file_size,
                    "zip_member": info.filename,
                }
            )
    return files


def _find_member(archive: zipfile.ZipFile, suffix: str) -> str | None:
    normalized_suffix = _normalize_path(suffix)
    for name in archive.namelist():
        if _normalize_path(name).endswith(normalized_suffix):
            return name
    return None


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("/").lower()


def _warn_on_remainder(name: str, raw: bytes, record_size: int, warnings: list[str] | None) -> None:
    remainder = len(raw) % record_size
    if remainder and warnings is not None:
        warnings.append(f"{name} has {remainder} trailing bytes after {len(raw) // record_size} complete records")


def _mg_value(value: int) -> dict[str, int | float | None]:
    if value == SENTINEL_U32:
        return {"mg": None, "g": None}
    return {"mg": value, "g": round(value / 1000.0, 4)}


def _g_value(value: float) -> dict[str, int | float | None]:
    if not math.isfinite(value):
        return {"mg": None, "g": None}
    rounded_g = round(value, 4)
    return {"mg": int(round(rounded_g * 1000)), "g": rounded_g}

def _color_for_g(value: float) -> str:
    if value > 80:
        return "RED"
    if value > 50:
        return "YELLOW"
    return "GREEN"

def _max_peak_axis(axes: dict[str, dict[str, Any]]) -> tuple[str | None, dict[str, Any]]:
    valid_axes = [
        (axis_name, axis_data)
        for axis_name, axis_data in axes.items()
        if axis_data.get("peakValueG") is not None
    ]
    if not valid_axes:
        return None, {}
    return max(valid_axes, key=lambda item: item[1].get("peakValueG") or 0)
