# UABAMS Backend Authentication Spec

Base URL local: `http://127.0.0.1:8000`
Base URL deployed: `https://your-render-service.onrender.com`

The gateway is outbound-only. The FastAPI backend is the passive receiver. Do not implement a raw TCP handshake; HTTP over TCP already handles that.

## Required Gateway Header

Gateway upload/authentication now requires only one header:

```text
X-Api-Key: shared-secret-key
```

The backend identifies the gateway from the API key:

```text
GW1 key -> GW_UABAMS_BOGIE_01
GW2 key -> GW_UABAMS_BOGIE_02
```

`gatewayId` and `trainId` are still stored internally in PostgreSQL, but the person uploading the file does not need to type them. For archive uploads, `trainId` is read from `session_metadata.json` when present; otherwise the backend uses the gateway's registered train.

Keep API keys private. Share them with the gateway developer offline only.

## Protected Endpoints

These gateway endpoints require `X-Api-Key`:

```text
PUT  /api/v1/archive
POST /api/v1/alert
GET  /api/v1/calibration/{gatewayId}
POST /api/v1/calibration/{gatewayId}
```

For calibration, the API key must belong to the same gateway as the `{gatewayId}` path value.

On auth failure:

```text
401 = missing API key
403 = wrong API key
```

## 1. Upload Archive

`PUT /api/v1/archive`

Headers:

```text
Content-Type: application/zip
X-Api-Key: <secret>
X-Sha256: <optional sha256 of zip body>
```

The backend calculates SHA-256. If `X-Sha256` is present and does not match, the API returns `400`.

Success response:

```json
{
  "status": "success",
  "sha256": "calculated_hash",
  "sizeBytes": 12345,
  "rmsRecords": 24,
  "peakRecords": 3,
  "faultRecords": 2,
  "peakAlerts": 3,
  "rmsIntervalValidation": {
    "expectedIntervalMm": 250,
    "toleranceMm": 25,
    "invalidIntervals": 0
  },
  "wheelCompensation": {
    "leftWheelFactor": 1.0,
    "rightWheelFactor": 1.0,
    "combinedFactor": 1.0
  },
  "rawTimeDomainFiles": 4,
  "retention": {
    "spatialAndAlertsDays": 30,
    "timeDomainDays": 7
  }
}
```

PostgreSQL tables: `archives`, `rms_records`, `peak_records`, `fault_records`, `alert_events`, `time_domain_files`, `time_domain_chunks`

## 2. Send Alert

`POST /api/v1/alert`

Headers:

```text
X-Api-Key: <secret>
```

Request:

```json
{
  "latitude": 12.9,
  "longitude": 77.5,
  "peakValueG": 90
}
```

Backend threshold logic:

```text
peakValueG > 80 = RED
peakValueG > 50 = YELLOW
else            = GREEN
```

PostgreSQL table: `alert_events`

## 3. Fetch Calibration

`GET /api/v1/calibration/GW_UABAMS_BOGIE_01`

Header required:

```text
X-Api-Key: <GW1 secret>
```

The API key must belong to `GW_UABAMS_BOGIE_01`. The endpoint reads the latest calibration document for the gateway from MongoDB. If no calibration is saved yet, it returns default values of `1.0`.

PostgreSQL table: `calibration_versions`

## 4. Dashboard Gateway Details

Operators can select one gateway on the dashboard and view only that gateway's status, latest alert, RMS count, peak count, fault count, archive uploads, and recent alert history.

## 5. Targeted Data Cleanup

Admin cleanup endpoint:

```text
POST /api/v1/data/reset
Header: X-Admin-Key
```

Use this only when data is bad because of server/test/upload issues. Provide a time range and/or location so only matching records are removed.

## What Gateway Developer Should Do

1. Add `X-Api-Key` to upload, alert, and calibration requests.
2. Put `gatewayId` and `trainId` inside `session_metadata.json` for archive uploads.
3. Send archives with `PUT /api/v1/archive`.
4. Send alerts with `POST /api/v1/alert`.
5. Fetch calibration after boot using `GET /api/v1/calibration/{gatewayId}` with the matching gateway API key.
6. Treat HTTP `2xx` as success. Treat `401`, `403`, `400`, `500` as failure.

## Archive ZIP Parsing

When the gateway uploads a ZIP to `PUT /api/v1/archive`, the backend validates SHA-256, opens the ZIP, and parses these ICD files:

- `session_metadata.json`
- `rms/rms_25cm.bin` fixed 66-byte records
- `peak/peak_50m.bin` fixed 302-byte records
- `faults/faults.bin` fixed 75-byte records

Parsed RMS records feed `/api/v1/map/rms`. Peak records with `alertGenerated=true` are inserted as alert events for the dashboard.