# CRIS Export Implementation

> **Status: PROVISIONAL** — Output format, file naming, column layout, and SFTP
> protocol are placeholders pending final confirmation from the CRIS/TMS team.
> Do NOT treat these exports as production-ready until the CRIS interface
> specification is confirmed.

---

## Overview

The CRIS export module reads data already stored in the UABAMS Cloud PostgreSQL
database and generates text/CSV files that can be transferred to the CRIS/TMS
server.

No acceleration values are recalculated — the files are a straight dump of
`rms_records`, `peak_records`, and `alert_events`.

---

## Architecture

```
UABAMS Gateway
    │  (archive ZIP)
    ▼
UABAMS Cloud ingestion  ──► PostgreSQL
    │  (asyncio.create_task)
    ▼
CrisExporter.export_all_for_archive()
    │
    ├── SPATIAL   ◄── rms_records
    ├── PROCESSED ◄── peak_records
    └── ALERTS    ◄── alert_events
          │
          ▼
    Local export directory  (CRIS_EXPORT_BASE_DIR)
          │
          └──[if CRIS_TRANSFER_ENABLED=true]──► SFTP → CRIS/TMS server
```

---

## New Files

| File | Purpose |
|---|---|
| `app/cris/__init__.py` | Python package marker |
| `app/cris/serializers.py` | Column definitions + CSV formatting |
| `app/cris/repository.py` | All SQL (source reads + cris_exports CRUD) |
| `app/cris/exporter.py` | Main export engine |
| `app/cris/transfer.py` | Abstract transfer interface |
| `app/cris/sftp_transfer.py` | SFTP implementation (Paramiko) |
| `app/routers/cris.py` | REST API (5 endpoints) |
| `tests/test_cris_export.py` | 13 unit tests (no DB/VPN required) |

---

## Export Types

### SPATIAL (`rms_records`)
25 cm spatial acceleration records. Ordered by `position_mm ASC`.

Key columns: `position_mm`, `latitude`, `longitude`, `gps_valid`, `bearing`,
`speed`, `al_x_g / al_y_g / al_z_g` (axle-left), `ar_*_g` (axle-right),
`bg_*_g` (bogie).

### PROCESSED (`peak_records`)
50 m window peak measurements. Ordered by `window_start_mm ASC`.

Key columns: `window_start_mm`, `window_end_mm`, `start_km`, `end_km`,
`alert_generated`, `alerts_count`, `axes` (JSONB).

### ALERTS (`alert_events`)
Safety event reports. Ordered by `created_at ASC`.

Key columns: `alert_type`, `peak_value_g`, `threshold_g`, `sensor`, `axis`,
`channel`, `zone`, `division`, `section`.

---

## File Naming

```
UABAMS_<TYPE>_<TRAIN>_<GATEWAY>_<SESSION>_<SHA10>.txt
```

Example:
```
UABAMS_SPATIAL_22151_GW1_SESSION_20260911_abc1234567.txt
```

---

## Export Lifecycle

```
PENDING → GENERATED → UPLOADING → UPLOADED
                   ↘             ↗
                    FAILED ──────
```

All state transitions are tracked in the `cris_exports` table.

---

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/cris/exports` | Generate export(s) |
| GET  | `/api/v1/cris/exports` | List exports (filters: status, trainId, gatewayId, exportType) |
| GET  | `/api/v1/cris/exports/{id}` | Get single export |
| POST | `/api/v1/cris/exports/{id}/transfer` | Transfer generated file |
| POST | `/api/v1/cris/exports/{id}/retry` | Retry FAILED export |

### Generate request body
```json
{
  "archiveSha256": "abc123...",
  "gatewayId": "GW1_22151_BOGIE_01",
  "trainId": "22151",
  "sessionName": "SESSION_20260911_111245",
  "exportType": "SPATIAL",
  "forceRegenerate": false
}
```
Omit `exportType` to generate all three types in one call.

---

## Configuration Variables

Add these to your server `.env`:

```env
# Transfer (keep false until CRIS destination is confirmed)
CRIS_TRANSFER_ENABLED=false
CRIS_TRANSFER_PROTOCOL=sftp

# SFTP destination (supplied by CRIS/TMS team)
CRIS_SFTP_HOST=
CRIS_SFTP_PORT=22
CRIS_SFTP_USERNAME=
CRIS_SFTP_PASSWORD=
CRIS_SFTP_PRIVATE_KEY=
CRIS_SFTP_HOST_KEY=
CRIS_SFTP_REMOTE_BASE_PATH=/exports

# Local storage
CRIS_EXPORT_BASE_DIR=/app/cris_exports
CRIS_EXPORT_DELIMITER=,
CRIS_EXPORT_ENCODING=UTF-8
CRIS_EXPORT_BATCH_SIZE=1000
```

> **Never commit** production passwords, private keys, or SFTP host keys to Git.

---

## Database Table: `cris_exports`

```sql
CREATE TABLE IF NOT EXISTS cris_exports (
    id                 SERIAL PRIMARY KEY,
    archive_sha256     VARCHAR(64)  NOT NULL,
    gateway_id         VARCHAR(100) NOT NULL,
    export_type        VARCHAR(20)  NOT NULL,
    train_id           VARCHAR(50),
    logical_gateway_id VARCHAR(100),
    session_name       VARCHAR(100),
    file_name          VARCHAR(255),
    file_path          TEXT,
    file_size          BIGINT,
    file_sha256        VARCHAR(64),
    record_count       INTEGER,
    status             VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    transfer_attempts  INTEGER      NOT NULL DEFAULT 0,
    last_error         TEXT,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    generated_at       TIMESTAMP WITH TIME ZONE,
    uploaded_at        TIMESTAMP WITH TIME ZONE,
    UNIQUE (archive_sha256, gateway_id, export_type)
);
```

The table is created automatically on application startup (`CREATE TABLE IF NOT EXISTS`).

---

## Deployment Steps

1. Copy `app/cris/` and `app/routers/cris.py` to the server.
2. Merge changes in `app/main.py`, `app/database.py`, `app/routers/telemetry.py`,
   `requirements.txt`, and `.env`.
3. `pip install paramiko`
4. Add `CRIS_*` variables to the server `.env` (leave transfer disabled).
5. Ensure `CRIS_EXPORT_BASE_DIR` is writable and persistent.
6. Restart the application — `cris_exports` table is created on startup.
7. Test: `POST /api/v1/cris/exports` for an existing archive.
8. Enable transfer only after SFTP credentials and host key are confirmed.

---

## Important Notes

- **No production format confirmed yet.** Column names and file naming are provisional.
- **No auth on CRIS endpoints.** Add operator authentication before going live.
- **Batching uses LIMIT/OFFSET.** May be slow on very large spatial datasets; replace with keyset pagination if needed.
- **SFTP strict host-key verification.** `CRIS_SFTP_HOST_KEY` is required; AutoAdd policy is never used.
