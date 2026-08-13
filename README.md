# UABAMS Cloud Prototype

FastAPI backend and browser dashboard for UABAMS gateway communication.

## Local Run

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open:

```text
Dashboard: http://127.0.0.1:8000/dashboard
Swagger:   http://127.0.0.1:8000/docs
Health:    http://127.0.0.1:8000/
```

## Gateway APIs

```text
PUT  /api/v1/archive
POST /api/v1/alert
GET  /api/v1/calibration/{gatewayId}
GET  /api/v1/map/alerts?train_id=019456
GET  /api/v1/map/rms?train_id=019456
GET  /api/v1/trains/{trainNo}/dashboard
POST /api/v1/sessions/reset
```

Gateway upload, alert, and calibration APIs require:

```text
X-Api-Key
```

Dashboard access requires operator login:

```text
OPERATOR_USERNAME
OPERATOR_PASSWORD
```

Reset from the browser requires the administrator password re-entry while logged in. `X-Admin-Key` remains supported for non-browser clients.

## Gateway Command Flow

Gateways poll for reset and calibration commands through `POST /api/v1/heartbeat`. Authenticate the heartbeat with either the existing `token` field, an `Authorization: Bearer <token>` header, or `X-Api-Key`.

The heartbeat response contains command metadata only. Calibration payloads are downloaded from:

```text
https://<cloud-host>/api/v1/calibration/<gatewayId>/payload/<commandId>
```

Calibration downloads require the owning gateway's `X-Api-Key`. Configure the externally reachable base URL so heartbeat responses do not contain an internal Docker address:

```text
CLOUD_PUBLIC_BASE_URL=https://cloud.example.com
```

Commands move through `pending`, `delivered`, and then `success` or `failed`. Only pending commands are sent. A newer command of the same type marks older unfinished commands as `superseded`.

## Frontend Screens

- Dashboard with separate GW1/GW2 status boxes
- Dashboard gateway selector to view all gateways or one selected gateway
- Online gateway boxes show green, offline boxes show red
- Calibration split into GW1 and GW2 panels
- Calibration save is blocked until "Destination reached" is selected
- Alert screen uses Leaflet + OpenStreetMap with separate maps for GW1 and GW2
- Archive upload history with parsed RMS/peak/fault counts
- Protected reset session screen
- Admin targeted cleanup by time range and/or location


## Archive Parsing

`PUT /api/v1/archive` now opens the uploaded ZIP and parses:

- `session_metadata.json` for session identity/status
- `rms/rms_25cm.bin` into `rms_records` for the route map
- `peak/peak_50m.bin` into `peak_records`; generated peak alerts are inserted into `alert_events`
- `faults/faults.bin` into `fault_records`

The route maps call `GET /api/v1/map/rms?train_id=019456` and draw colored OpenStreetMap route points from parsed RMS records.

## Spatial Validation, Compensation, and Retention

- RMS records are validated against a fixed 250 mm interval with a +/- 25 mm tolerance. Each record stores `spatialIntervalMm` and `spatialIntervalValid`; the archive stores a validation summary and warning count.
- The latest saved calibration is applied during archive ingestion. The backend uses `(leftWheelFactor + rightWheelFactor) / 2` to compensate distance and speed, while preserving `rawPositionMm` and `rawSpeedKmph`.
- Spatial records, alerts, faults, and archive metadata are stored in PostgreSQL with 30-day retention metadata.
- Raw `raw/*.bin` time-domain files are stored on the configured filesystem volume; file metadata and expiry timestamps are stored in PostgreSQL.
## Render Deployment

Use `render.yaml`, then add these environment variables in Render:

```text
DATABASE_URL
CLOUD_PUBLIC_BASE_URL
GATEWAY_API_KEY_GW01
GATEWAY_API_KEY_GW02
JWT_SECRET
ADMIN_RESET_KEY
OPERATOR_USERNAME
OPERATOR_PASSWORD
```

Do not commit `.env`.
