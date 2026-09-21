#!/usr/bin/env python3
"""
UABAMS Cloud / Gateway Binary Files Reader & Parser
===================================================
Reads and parses binary telemetry files generated in UABAMS session zip files:
  - raw/adxl_left.bin, raw/adxl_right.bin
  - raw/bogie.bin
  - raw/encoder.bin
  - rms/rms_25cm.bin
  - peak/peak_50m.bin
  - faults/faults.bin
  - session_metadata.json

Usage:
  # Summarize a zip archive or unzipped directory
  python read_bins.py <path_to_session.zip_or_dir>

  # Read specific file (e.g. encoder, bogie, adxl_left, faults, rms, peak)
  python read_bins.py <path_to_session.zip_or_dir> --file encoder --head 20
  python read_bins.py <path_to_session.zip_or_dir> --file faults
  python read_bins.py <path_to_session.zip_or_dir> --file adxl_left --tail 10

  # Read a standalone .bin file directly
  python read_bins.py path/to/encoder.bin --head 15

  # Export all data from a zip to CSV directory
  python read_bins.py <path_to_session.zip> --export-csv ./exported_csvs/
"""

import argparse
import csv
import io
import json
import math
import os
import struct
import sys
import zipfile
from typing import BinaryIO, Dict, Generator, Iterator, List, Optional, Tuple, Union

# ==========================================
# Binary Formats & Constants
# ==========================================

# ADXL: Batch Header = 9 bytes (<QB), Samples = 20 bytes each (<Qfff)
ADXL_BATCH_HEADER_FMT = "<QB"
ADXL_BATCH_HEADER_SIZE = struct.calcsize(ADXL_BATCH_HEADER_FMT)  # 9 bytes
ADXL_SAMPLE_FMT = "<Qfff"
ADXL_SAMPLE_SIZE = struct.calcsize(ADXL_SAMPLE_FMT)  # 20 bytes

# Bogie: Batch Header = 9 bytes (<QB), Samples = 44 bytes each (<Qfffffffff)
BOGIE_BATCH_HEADER_FMT = "<QB"
BOGIE_BATCH_HEADER_SIZE = struct.calcsize(BOGIE_BATCH_HEADER_FMT)  # 9 bytes
BOGIE_SAMPLE_FMT = "<Qfffffffff"
BOGIE_SAMPLE_SIZE = struct.calcsize(BOGIE_SAMPLE_FMT)  # 44 bytes

# Encoder: Record = 20 bytes (<QIihbB)
ENCODER_FMT = "<QIihbB"
ENCODER_SIZE = struct.calcsize(ENCODER_FMT)  # 20 bytes

# RMS: Gateway format (70 bytes) vs Legacy format (66 bytes)
RMS_GATEWAY_FMT = "<Qifdd?B9f"
RMS_GATEWAY_SIZE = struct.calcsize(RMS_GATEWAY_FMT)  # 70 bytes
RMS_LEGACY_FMT = "<Qidd?B9I"
RMS_LEGACY_SIZE = struct.calcsize(RMS_LEGACY_FMT)  # 66 bytes

# Peak: Window Header (24 bytes) + 9 axes (36 bytes each) = 348 bytes
PEAK_HEADER_FMT = "<ii3f4B"
PEAK_HEADER_SIZE = struct.calcsize(PEAK_HEADER_FMT)  # 24 bytes
PEAK_AXIS_FMT = "<fifQdd"
PEAK_AXIS_SIZE = struct.calcsize(PEAK_AXIS_FMT)  # 36 bytes
PEAK_RECORD_SIZE = PEAK_HEADER_SIZE + (9 * PEAK_AXIS_SIZE)  # 348 bytes

# Faults: 75 bytes (<QBBB64s)
FAULT_FMT = "<QBBB64s"
FAULT_SIZE = struct.calcsize(FAULT_FMT)  # 75 bytes

AXES = ["AL_X", "AL_Y", "AL_Z", "AR_X", "AR_Y", "AR_Z", "BG_X", "BG_Y", "BG_Z"]
AXIS_NAMES_LOWER = [a.lower() for a in AXES]

FAULT_CODES = {
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


# ==========================================
# Parsers / Generators
# ==========================================

def parse_adxl_stream(stream: BinaryIO) -> Generator[Dict, None, None]:
    """Yields parsed ADXL samples from binary stream."""
    sample_idx = 0
    while True:
        header = stream.read(ADXL_BATCH_HEADER_SIZE)
        if len(header) < ADXL_BATCH_HEADER_SIZE:
            break
        start_count, sample_count = struct.unpack(ADXL_BATCH_HEADER_FMT, header)
        for _ in range(sample_count):
            sample_data = stream.read(ADXL_SAMPLE_SIZE)
            if len(sample_data) < ADXL_SAMPLE_SIZE:
                return
            count, x, y, z = struct.unpack(ADXL_SAMPLE_FMT, sample_data)
            yield {
                "sample_index": sample_idx,
                "master_count": count,
                "x_g": round(x, 4),
                "y_g": round(y, 4),
                "z_g": round(z, 4),
            }
            sample_idx += 1


def parse_bogie_stream(stream: BinaryIO) -> Generator[Dict, None, None]:
    """Yields parsed Bogie IMU samples from binary stream."""
    sample_idx = 0
    while True:
        header = stream.read(BOGIE_BATCH_HEADER_SIZE)
        if len(header) < BOGIE_BATCH_HEADER_SIZE:
            break
        start_count, sample_count = struct.unpack(BOGIE_BATCH_HEADER_FMT, header)
        for _ in range(sample_count):
            sample_data = stream.read(BOGIE_SAMPLE_SIZE)
            if len(sample_data) < BOGIE_SAMPLE_SIZE:
                return
            unpacked = struct.unpack(BOGIE_SAMPLE_FMT, sample_data)
            count = unpacked[0]
            iis_x, iis_y, iis_z, imu_x, imu_y, imu_z, gyr_x, gyr_y, gyr_z = unpacked[1:]
            yield {
                "sample_index": sample_idx,
                "master_count": count,
                "iis_x": round(iis_x, 4),
                "iis_y": round(iis_y, 4),
                "iis_z": round(iis_z, 4),
                "imu_x": round(imu_x, 4),
                "imu_y": round(imu_y, 4),
                "imu_z": round(imu_z, 4),
                "gyr_x": round(gyr_x, 4),
                "gyr_y": round(gyr_y, 4),
                "gyr_z": round(gyr_z, 4),
            }
            sample_idx += 1


def parse_encoder_stream(stream: BinaryIO) -> Generator[Dict, None, None]:
    """Yields parsed Encoder records from binary stream."""
    record_idx = 0
    while True:
        chunk = stream.read(ENCODER_SIZE)
        if len(chunk) < ENCODER_SIZE:
            break
        count, event_idx, pos_mm, spd_ck, direct, flags = struct.unpack(ENCODER_FMT, chunk)
        # speed in cK is centi-km/h (speed_kmph = spd_ck / 100.0)
        yield {
            "record_index": record_idx,
            "master_count": count,
            "event_index": event_idx,
            "position_mm": pos_mm,
            "position_m": round(pos_mm / 1000.0, 3),
            "speed_ck": spd_ck,
            "speed_kmph": round(spd_ck / 100.0, 2),
            "direction": direct,
            "flags": f"0x{flags:02X}",
        }
        record_idx += 1


def parse_rms_stream(stream: BinaryIO, raw_len: Optional[int] = None) -> Generator[Dict, None, None]:
    """Yields parsed RMS 25cm spatial records."""
    data = stream.read()
    total_len = len(data)
    record_idx = 0

    if total_len % RMS_GATEWAY_SIZE == 0 and total_len > 0:
        for offset in range(0, total_len, RMS_GATEWAY_SIZE):
            chunk = data[offset : offset + RMS_GATEWAY_SIZE]
            unpacked = struct.unpack(RMS_GATEWAY_FMT, chunk)
            count, pos_mm, spd_kmph, lat, lon, gps_val, val_mask, *axis_g = unpacked
            rec = {
                "record_index": record_idx,
                "master_count": count,
                "position_mm": pos_mm,
                "position_m": round(pos_mm / 1000.0, 3),
                "speed_kmph": round(spd_kmph, 2),
                "latitude": round(lat, 7) if lat else 0.0,
                "longitude": round(lon, 7) if lon else 0.0,
                "gps_valid": bool(gps_val),
                "valid_mask": val_mask,
            }
            for name, val in zip(AXIS_NAMES_LOWER, axis_g):
                rec[name] = round(val, 4) if math.isfinite(val) else None
            yield rec
            record_idx += 1
    elif total_len >= RMS_LEGACY_SIZE:
        usable = total_len - (total_len % RMS_LEGACY_SIZE)
        for offset in range(0, usable, RMS_LEGACY_SIZE):
            chunk = data[offset : offset + RMS_LEGACY_SIZE]
            unpacked = struct.unpack(RMS_LEGACY_FMT, chunk)
            count, pos_mm, lat, lon, gps_val, val_mask, *axis_mg = unpacked
            rec = {
                "record_index": record_idx,
                "master_count": count,
                "position_mm": pos_mm,
                "position_m": round(pos_mm / 1000.0, 3),
                "speed_kmph": None,
                "latitude": round(lat, 7) if lat else 0.0,
                "longitude": round(lon, 7) if lon else 0.0,
                "gps_valid": bool(gps_val),
                "valid_mask": val_mask,
            }
            for name, val in zip(AXIS_NAMES_LOWER, axis_mg):
                rec[name] = round(val / 1000.0, 4) if val != 0xFFFFFFFF else None
            yield rec
            record_idx += 1


def parse_peak_stream(stream: BinaryIO) -> Generator[Dict, None, None]:
    """Yields parsed Peak 50m window records."""
    data = stream.read()
    total_len = len(data)
    usable = total_len - (total_len % PEAK_RECORD_SIZE)
    record_idx = 0

    for offset in range(0, usable, PEAK_RECORD_SIZE):
        chunk = data[offset : offset + PEAK_RECORD_SIZE]
        header = struct.unpack(PEAK_HEADER_FMT, chunk[:PEAK_HEADER_SIZE])
        win_start, win_end, avg_speed, min_speed, max_speed, valid_mask, alert_gen, alerts_cnt, res = header

        rec = {
            "record_index": record_idx,
            "window_start_mm": win_start,
            "window_end_mm": win_end,
            "window_start_m": round(win_start / 1000.0, 2),
            "window_end_m": round(win_end / 1000.0, 2),
            "avg_speed_kmph": round(avg_speed, 2),
            "min_speed_kmph": round(min_speed, 2),
            "max_speed_kmph": round(max_speed, 2),
            "valid_mask": valid_mask,
            "alert_generated": bool(alert_gen),
            "alerts_count": alerts_cnt,
        }

        ax_offset = PEAK_HEADER_SIZE
        for axis_name in AXES:
            ax_chunk = chunk[ax_offset : ax_offset + PEAK_AXIS_SIZE]
            peak_g, pos_mm, peak_speed, master_cnt, lat, lon = struct.unpack(PEAK_AXIS_FMT, ax_chunk)
            low = axis_name.lower()
            rec[f"{low}_peak_g"] = round(peak_g, 4) if math.isfinite(peak_g) else 0.0
            rec[f"{low}_pos_mm"] = pos_mm
            rec[f"{low}_speed_kmph"] = round(peak_speed, 2)
            rec[f"{low}_count"] = master_cnt
            rec[f"{low}_lat"] = round(lat, 7) if lat else 0.0
            rec[f"{low}_lon"] = round(lon, 7) if lon else 0.0
            ax_offset += PEAK_AXIS_SIZE

        yield rec
        record_idx += 1


def parse_fault_stream(stream: BinaryIO) -> Generator[Dict, None, None]:
    """Yields parsed Fault records."""
    record_idx = 0
    while True:
        chunk = stream.read(FAULT_SIZE)
        if len(chunk) < FAULT_SIZE:
            break
        ts_ms, code, node, sev, desc_raw = struct.unpack(FAULT_FMT, chunk)
        desc = desc_raw.split(b"\x00", 1)[0].decode("latin1", errors="replace").strip()
        yield {
            "record_index": record_idx,
            "timestamp_ms": ts_ms,
            "fault_code": code,
            "fault_name": FAULT_CODES.get(code, f"UNKNOWN_0x{code:02X}"),
            "node_id": node,
            "severity": sev,
            "description": desc,
        }
        record_idx += 1


# ==========================================
# Archive & Directory Loader
# ==========================================

class SessionReader:
    """Helper to access binary files from a .zip file or an unzipped folder."""

    def __init__(self, source_path: str):
        self.source_path = source_path
        self.is_zip = zipfile.is_zipfile(source_path) if os.path.isfile(source_path) else False
        self.is_dir = os.path.isdir(source_path)
        self.is_single_file = os.path.isfile(source_path) and not self.is_zip
        self._zip_file: Optional[zipfile.ZipFile] = None

        if self.is_zip:
            self._zip_file = zipfile.ZipFile(source_path, "r")

    def close(self):
        if self._zip_file:
            self._zip_file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_metadata(self) -> Optional[Dict]:
        """Reads session metadata JSON if present."""
        candidates = ["session_metadata.json", "metadata.json", "manifest.json"]
        if self.is_zip:
            for cand in candidates:
                for name in self._zip_file.namelist():
                    if name.lower().endswith(cand.lower()):
                        return json.loads(self._zip_file.read(name).decode("utf-8"))
        elif self.is_dir:
            for cand in candidates:
                for root, _, files in os.walk(self.source_path):
                    for f in files:
                        if f.lower() == cand.lower():
                            with open(os.path.join(root, f), "r", encoding="utf-8") as fp:
                                return json.load(fp)
        return None

    def get_file_stream(self, file_key: str) -> Optional[Tuple[BinaryIO, int]]:
        """Returns (stream, size_bytes) for a given file name/type."""
        # Normalize key
        norm_key = file_key.lower().strip()
        if not norm_key.endswith(".bin") and not norm_key.endswith(".json"):
            norm_key += ".bin"

        if self.is_single_file:
            if os.path.basename(self.source_path).lower() == os.path.basename(norm_key).lower() or not file_key:
                return open(self.source_path, "rb"), os.path.getsize(self.source_path)
            return open(self.source_path, "rb"), os.path.getsize(self.source_path)

        if self.is_zip:
            for name in self._zip_file.namelist():
                if name.lower().endswith(norm_key):
                    data = self._zip_file.read(name)
                    return io.BytesIO(data), len(data)

        if self.is_dir:
            for root, _, files in os.walk(self.source_path):
                for f in files:
                    if f.lower().endswith(norm_key) or os.path.join(root, f).lower().endswith(norm_key):
                        full_path = os.path.join(root, f)
                        return open(full_path, "rb"), os.path.getsize(full_path)

        return None

    def list_files(self) -> List[Tuple[str, int]]:
        """List all .bin and .json files in the session with their sizes."""
        results = []
        if self.is_zip:
            for info in self._zip_file.infolist():
                if not info.is_dir():
                    results.append((info.filename, info.file_size))
        elif self.is_dir:
            for root, _, files in os.walk(self.source_path):
                for f in files:
                    full_p = os.path.join(root, f)
                    rel_p = os.path.relpath(full_p, self.source_path)
                    results.append((rel_p, os.path.getsize(full_p)))
        elif self.is_single_file:
            results.append((os.path.basename(self.source_path), os.path.getsize(self.source_path)))
        return sorted(results, key=lambda x: x[0])


def detect_file_parser(filename: str):
    """Detects appropriate parser function from filename."""
    fn = filename.lower()
    if "adxl" in fn:
        return "adxl", parse_adxl_stream
    if "bogie" in fn:
        return "bogie", parse_bogie_stream
    if "encoder" in fn:
        return "encoder", parse_encoder_stream
    if "rms" in fn:
        return "rms", parse_rms_stream
    if "peak" in fn:
        return "peak", parse_peak_stream
    if "fault" in fn:
        return "faults", parse_fault_stream
    return None, None


# ==========================================
# CLI Display & Export Utilities
# ==========================================

def format_table(records: List[Dict], max_cols: int = 12) -> str:
    """Formats list of dict records as an aligned ASCII table."""
    if not records:
        return "No records found."

    keys = list(records[0].keys())[:max_cols]
    col_widths = {k: len(str(k)) for k in keys}

    for r in records:
        for k in keys:
            val_str = str(r.get(k, ""))
            if len(val_str) > col_widths[k]:
                col_widths[k] = min(len(val_str), 25)

    header = " | ".join(f"{k:>{col_widths[k]}}" for k in keys)
    sep = "-+-".join("-" * col_widths[k] for k in keys)

    lines = [header, sep]
    for r in records:
        row_str = " | ".join(f"{str(r.get(k, ''))[:25]:>{col_widths[k]}}" for k in keys)
        lines.append(row_str)

    return "\n".join(lines)


def print_summary(reader: SessionReader):
    """Prints a structured summary of the session and all binary files."""
    print("=" * 80)
    print(" 🛰️  UABAMS SESSION DATA SUMMARY")
    print("=" * 80)
    print(f"Source: {reader.source_path}")

    meta = reader.get_metadata()
    if meta:
        print("\n--- Session Metadata ---")
        for k, v in meta.items():
            print(f"  {k:20s}: {v}")

    print("\n--- Files Contained ---")
    files = reader.list_files()
    if not files:
        print("  No files found.")
        return

    print(f"  {'Filename':<35} | {'Size':>12} | {'Estimated Records':>18}")
    print("  " + "-" * 70)

    for fn, size in files:
        ftype, _ = detect_file_parser(fn)
        est_str = "-"
        if ftype == "adxl":
            # approximate: 20 bytes per sample + 9 bytes per batch (100 samples)
            approx_samples = size // 20
            est_str = f"~{approx_samples:,} samples"
        elif ftype == "bogie":
            approx_samples = size // 44
            est_str = f"~{approx_samples:,} samples"
        elif ftype == "encoder":
            est_str = f"{size // ENCODER_SIZE:,} records"
        elif ftype == "rms":
            count = size // RMS_GATEWAY_SIZE if size % RMS_GATEWAY_SIZE == 0 else size // RMS_LEGACY_SIZE
            est_str = f"{count:,} records"
        elif ftype == "peak":
            est_str = f"{size // PEAK_RECORD_SIZE:,} windows"
        elif ftype == "faults":
            est_str = f"{size // FAULT_SIZE:,} faults"

        size_str = f"{size / (1024 * 1024):.2f} MB" if size > 1024 * 1024 else f"{size / 1024:.1f} KB" if size > 1024 else f"{size} B"
        print(f"  {fn:<35} | {size_str:>12} | {est_str:>18}")

    print("=" * 80)


def export_to_csv(reader: SessionReader, output_dir: str):
    """Exports all parsed binary files to CSV files in output_dir."""
    os.makedirs(output_dir, exist_ok=True)
    print(f"Exporting parsed CSV files to: {output_dir}")

    files = reader.list_files()
    for fn, size in files:
        ftype, parser_fn = detect_file_parser(fn)
        if not parser_fn or size == 0:
            continue

        base_name = os.path.splitext(os.path.basename(fn))[0]
        csv_path = os.path.join(output_dir, f"{base_name}.csv")

        stream_info = reader.get_file_stream(fn)
        if not stream_info:
            continue

        stream, _ = stream_info
        try:
            generator = parser_fn(stream)
            first_rec = next(generator, None)
            if not first_rec:
                print(f"  [SKIPPED] {fn} (empty)")
                continue

            with open(csv_path, "w", newline="", encoding="utf-8") as f_out:
                writer = csv.DictWriter(f_out, fieldnames=list(first_rec.keys()))
                writer.writeheader()
                writer.writerow(first_rec)
                count = 1
                for rec in generator:
                    writer.writerow(rec)
                    count += 1
                print(f"  [SAVED] {csv_path} ({count:,} rows)")
        finally:
            stream.close()


def main():
    parser = argparse.ArgumentParser(
        description="UABAMS Gateway Binary Files Reader & Parser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python read_bins.py session.zip
  python read_bins.py session.zip --file encoder --head 10
  python read_bins.py session.zip --file faults
  python read_bins.py path/to/raw/adxl_left.bin --tail 20
  python read_bins.py session.zip --export-csv ./csv_out/
        """,
    )
    parser.add_argument("source", help="Path to .zip archive, session directory, or a specific .bin file")
    parser.add_argument("-f", "--file", help="File to inspect (e.g. encoder, bogie, adxl_left, adxl_right, rms, peak, faults)")
    parser.add_argument("-H", "--head", type=int, default=None, help="Print first N records")
    parser.add_argument("-T", "--tail", type=int, default=None, help="Print last N records")
    parser.add_argument("-a", "--all", action="store_true", help="Print all records (warning: can be large)")
    parser.add_argument("--export-csv", metavar="DIR", help="Export all session files to CSV directory")
    parser.add_argument("--export-json", metavar="FILE", help="Export inspected file records to JSON file")

    args = parser.parse_args()

    if not os.path.exists(args.source):
        print(f"Error: Source '{args.source}' does not exist.", file=sys.stderr)
        sys.exit(1)

    with SessionReader(args.source) as reader:
        # 1. Export CSV mode
        if args.export_csv:
            export_to_csv(reader, args.export_csv)
            return

        # 2. Specific file inspection
        target_file = args.file
        if not target_file and reader.is_single_file:
            target_file = os.path.basename(args.source)

        if target_file:
            stream_info = reader.get_file_stream(target_file)
            if not stream_info:
                print(f"Error: File matching '{target_file}' not found in source.", file=sys.stderr)
                sys.exit(1)

            stream, size = stream_info
            _, parser_fn = detect_file_parser(target_file)
            if not parser_fn:
                print(f"Error: No parser available for '{target_file}'.", file=sys.stderr)
                sys.exit(1)

            print(f"\n--- Reading {target_file} (Size: {size:,} bytes) ---\n")
            generator = parser_fn(stream)

            # Collection based on head / tail / all
            if args.head is not None:
                records = []
                for _ in range(args.head):
                    try:
                        records.append(next(generator))
                    except StopIteration:
                        break
            elif args.tail is not None:
                from collections import deque
                records = list(deque(generator, maxlen=args.tail))
            elif args.all:
                records = list(generator)
            else:
                # Default preview: first 15 records
                records = []
                for _ in range(15):
                    try:
                        records.append(next(generator))
                    except StopIteration:
                        break

            print(format_table(records))
            if not args.head and not args.tail and not args.all:
                print("\n[Showing first 15 records. Use --head N, --tail N, or --all to customize]")

            if args.export_json:
                with open(args.export_json, "w", encoding="utf-8") as jf:
                    json.dump(records, jf, indent=2)
                print(f"\nSaved {len(records)} records to {args.export_json}")

            stream.close()
            return

        # 3. Default: Print summary of session
        print_summary(reader)
        print("\nTip: Use `--file <type> [--head N | --tail N]` to inspect specific binary files.")
        print("     Supported file types: adxl_left, adxl_right, bogie, encoder, rms, peak, faults")


if __name__ == "__main__":
    main()
