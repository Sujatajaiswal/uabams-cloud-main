"""CRIS export engine.

Reads existing data from PostgreSQL (rms_records, peak_records, alert_events),
serialises it to CSV text files, records export tracking rows in cris_exports,
and optionally triggers SFTP transfer.

Output format and transfer protocol are PROVISIONAL pending final CRIS/TMS spec.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import UTC, datetime
from typing import Any

from app.cris import repository as repo
from app.cris.serializers import EXPORT_TYPE_MAP, serialize_records
from app.database import settings


# ── helpers ───────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_name(value: str) -> str:
    """Remove characters that are not safe in a filename component."""
    return re.sub(r"[^\w\-]", "_", value)


def _export_dir(export_type: str) -> str:
    """Return the local directory for the given export type, creating it if needed."""
    base = settings.get("cris_export_base_dir", "/app/cris_exports")
    sub  = export_type.lower()
    path = os.path.join(base, sub)
    os.makedirs(path, exist_ok=True)
    return path


def _build_filename(
    export_type: str,
    train_id: str,
    gateway_id: str,
    session_name: str,
    sha10: str,
) -> str:
    """Build the output filename, e.g. UABAMS_SPATIAL_22151_GW1_SESSION_abc1234567.txt"""
    parts = [
        "UABAMS",
        export_type,
        _safe_name(train_id or "UNK"),
        _safe_name(gateway_id or "UNK"),
        _safe_name(session_name or "UNK"),
        sha10,
    ]
    return "_".join(parts) + ".txt"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_transfer_client():
    """Build the configured transfer client, or return None if transfer is disabled."""
    if not settings.get("cris_transfer_enabled", False):
        return None
    protocol = settings.get("cris_transfer_protocol", "sftp").lower()
    if protocol != "sftp":
        raise RuntimeError(f"Unsupported CRIS transfer protocol: {protocol!r}")

    from app.cris.sftp_transfer import SftpTransferClient

    return SftpTransferClient(
        host=settings.get("cris_sftp_host", ""),
        port=int(settings.get("cris_sftp_port", 22) or 22),
        username=settings.get("cris_sftp_username", ""),
        password=settings.get("cris_sftp_password") or None,
        private_key=settings.get("cris_sftp_private_key") or None,
        known_host_line=settings.get("cris_sftp_host_key") or None,
        remote_base_dir=settings.get("cris_sftp_remote_base_path", "/exports"),
    )


# ── main exporter ─────────────────────────────────────────────────────────────

class CrisExporter:
    """Generate and optionally transfer CRIS export files."""

    def __init__(self) -> None:
        self._batch_size: int = int(settings.get("cris_export_batch_size", 1000) or 1000)
        self._delimiter:  str = settings.get("cris_export_delimiter", ",") or ","
        self._encoding:   str = settings.get("cris_export_encoding", "UTF-8") or "UTF-8"

    # ------------------------------------------------------------------
    async def generate_export(
        self,
        *,
        archive_sha256: str,
        gateway_id: str,
        export_type: str,
        train_id: str | None = None,
        session_name: str | None = None,
        logical_gateway_id: str | None = None,
        force_regenerate: bool = False,
    ) -> dict[str, Any]:
        """Generate a single export file.

        If an export row already exists and the file is present, returns the
        existing record unless *force_regenerate* is True.

        Returns a dict with the export record fields.
        """
        if export_type not in EXPORT_TYPE_MAP:
            raise ValueError(f"Unknown export_type: {export_type!r}")

        # ── idempotency check ─────────────────────────────────────────────
        existing = await repo.find_existing_export(archive_sha256, gateway_id, export_type)
        if existing and not force_regenerate:
            fp = existing.get("file_path")
            if fp and os.path.isfile(fp):
                return existing  # already generated — return cached record

        # ── create / reuse tracking row ───────────────────────────────────
        if existing:
            export_id = existing["id"]
        else:
            export_id = await repo.create_export_record(
                archive_sha256=archive_sha256,
                gateway_id=gateway_id,
                export_type=export_type,
                train_id=train_id,
                logical_gateway_id=logical_gateway_id,
                session_name=session_name,
            )

        # ── fetch & write data in batches ─────────────────────────────────
        fetch_fn = {
            "SPATIAL":   repo.fetch_rms_records,
            "PROCESSED": repo.fetch_peak_records,
            "ALERTS":    repo.fetch_alert_events,
        }[export_type]

        export_dir = _export_dir(export_type)
        sha10      = archive_sha256[:10] if archive_sha256 else "unknown"
        file_name  = _build_filename(
            export_type,
            train_id or "UNK",
            gateway_id,
            session_name or "UNK",
            sha10,
        )
        file_path  = os.path.join(export_dir, file_name)

        # Security: prevent path traversal
        if not os.path.abspath(file_path).startswith(os.path.abspath(export_dir)):
            raise RuntimeError("Unsafe file path detected. Aborting.")

        tmp_path = file_path + ".tmp"
        total_records = 0

        try:
            with open(tmp_path, "wb") as fh:
                offset = 0
                first  = True
                while True:
                    batch = await fetch_fn(
                        archive_sha256, gateway_id,
                        train_id=train_id,
                        batch_size=self._batch_size,
                        offset=offset,
                    )
                    if not batch:
                        break
                    csv_bytes = serialize_records(
                        batch, export_type,
                        delimiter=self._delimiter,
                        encoding=self._encoding,
                    )
                    if not first:
                        # Strip header row on subsequent batches
                        newline_pos = csv_bytes.find(b"\r\n")
                        if newline_pos != -1:
                            csv_bytes = csv_bytes[newline_pos + 2:]
                    fh.write(csv_bytes)
                    total_records += len(batch)
                    offset        += len(batch)
                    first          = False

            # Rename .tmp → final
            os.replace(tmp_path, file_path)

        except Exception as exc:
            # Clean up partial file
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            await repo.update_export_failed(export_id, str(exc))
            raise

        file_size   = os.path.getsize(file_path)
        file_sha256 = _sha256_file(file_path)
        generated_at = _utc_now()

        await repo.update_export_generated(
            export_id,
            file_path=file_path,
            file_name=file_name,
            file_size=file_size,
            file_sha256=file_sha256,
            record_count=total_records,
            generated_at=generated_at,
        )

        # ── optional auto-transfer ─────────────────────────────────────────
        if settings.get("cris_transfer_enabled", False):
            try:
                await self.transfer_export(export_id, file_path=file_path, file_name=file_name)
            except Exception as transfer_exc:
                print(f"[CRIS] Transfer failed for export {export_id}: {transfer_exc}")

        return await repo.get_export(export_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    async def transfer_export(
        self,
        export_id: int,
        *,
        file_path: str | None = None,
        file_name: str | None = None,
    ) -> dict[str, Any]:
        """Transfer an already-generated export file to the CRIS server."""
        record = await repo.get_export(export_id)
        if not record:
            raise ValueError(f"Export {export_id} not found")

        fp = file_path or record.get("file_path")
        fn = file_name or record.get("file_name")
        if not fp or not os.path.isfile(fp):
            raise RuntimeError(f"Export file not found: {fp!r}")

        client = _get_transfer_client()
        if client is None:
            raise RuntimeError("CRIS transfer is disabled (CRIS_TRANSFER_ENABLED=false)")

        await repo.update_export_uploading(export_id)
        try:
            client.upload(fp, fn)
            await repo.update_export_uploaded(export_id, _utc_now())
        except Exception as exc:
            await repo.update_export_failed(export_id, str(exc))
            raise

        return await repo.get_export(export_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    async def export_all_for_archive(
        self,
        *,
        archive_sha256: str,
        gateway_id: str,
        train_id: str | None = None,
        session_name: str | None = None,
        logical_gateway_id: str | None = None,
        force_regenerate: bool = False,
    ) -> list[dict[str, Any]]:
        """Generate all three export types (SPATIAL, PROCESSED, ALERTS).

        Failures on individual types are recorded and execution continues.
        """
        results: list[dict[str, Any]] = []
        for export_type in ("SPATIAL", "PROCESSED", "ALERTS"):
            try:
                record = await self.generate_export(
                    archive_sha256=archive_sha256,
                    gateway_id=gateway_id,
                    export_type=export_type,
                    train_id=train_id,
                    session_name=session_name,
                    logical_gateway_id=logical_gateway_id,
                    force_regenerate=force_regenerate,
                )
                results.append(record)
            except Exception as exc:
                print(f"[CRIS] Failed to generate {export_type} export "
                      f"for archive {archive_sha256}: {exc}")
                # Attempt to persist a FAILED row so it can be retried
                try:
                    existing = await repo.find_existing_export(
                        archive_sha256, gateway_id, export_type
                    )
                    if existing:
                        await repo.update_export_failed(existing["id"], str(exc))
                        results.append(await repo.get_export(existing["id"]))
                    else:
                        eid = await repo.create_export_record(
                            archive_sha256=archive_sha256,
                            gateway_id=gateway_id,
                            export_type=export_type,
                            train_id=train_id,
                            logical_gateway_id=logical_gateway_id,
                            session_name=session_name,
                            status="FAILED",
                        )
                        await repo.update_export_failed(eid, str(exc))
                        results.append(await repo.get_export(eid))
                except Exception as inner_exc:
                    print(f"[CRIS] Could not persist FAILED record: {inner_exc}")
        return results
