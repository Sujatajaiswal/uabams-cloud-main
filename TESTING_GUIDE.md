# UABAMS Cloud Tester Guide

## 1. Purpose

This document defines the functional test boundary for the UABAMS Cloud dashboard and gateway APIs. Use it to reproduce defects consistently and decide whether a result is inside the supported application scope.

The dashboard is a single-page application. The items called pages below are dashboard tabs, not separate HTML pages.

## 2. Environment and Access

| Area | URL | Access |
|---|---|---|
| Root status | `/` | Public |
| Health | `/health` | Public |
| Login | `/login` | Public |
| Dashboard | `/dashboard` | Authenticated operator |
| Swagger | `/docs` | Admin only |

Run locally with `python -m uvicorn app.main:app --reload`. PostgreSQL and the environment values described in `README.md` are required.

Use a test admin, a test operator, an inactive user, and at least two registered gateways:

- `GW_UABAMS_BOGIE_01` (GW1)
- `GW_UABAMS_BOGIE_02` (GW2)

Use a train with archive data, RMS records, peak records, faults, alerts, GPS points, and at least one active session. Keep a separate disposable dataset for Reset and Targeted Data Cleanup tests.

### Roles and permissions

| Capability | Admin | Operator with permission | Operator without permission |
|---|---:|---:|---:|
| Open dashboard | Yes | Yes | Yes |
| View Alerts | Yes | `can_view_alerts=true` | No; redirected to Dashboard |
| Calibration | Yes | `can_configure_thresholds=true` | No; redirected to Dashboard |
| User Management | Yes / permission | `can_manage_users=true` | No; redirected to Dashboard |
| Reset and Logs | Yes | No; redirected to Dashboard | No |
| Swagger | Yes | No; redirected to Dashboard | No |

For restricted actions, test both the UI state and a direct API request. The API should return `401` without an operator session and `403` when the session lacks permission.

## 3. Common Navigation and Session

### Purpose

The header, profile menu, train search, gateway selector, tab navigation, API status indicator, and logout control are shared by all dashboard tabs.

### Checks

1. Unauthenticated `/dashboard` redirects to `/login`.
2. Valid login redirects to `/dashboard` and shows the username.
3. Invalid credentials stay on Login with an error.
4. Inactive users cannot log in.
5. Logout ends access; reopening `/dashboard` redirects to Login.
6. Valid train search loads the selected train.
7. Gateway selection supports All Gateways, GW1, and GW2.
8. Tab changes do not require a full page reload.
9. API failures show an error state rather than stale or broken content.

### Boundary

- A dashboard session is an operator browser cookie, not a gateway API key.
- Train and gateway values are constrained by registered records.
- Gateway aliases are normalized for authentication: GW1 aliases include `GW1`, `GW01`, `1`; equivalent GW2 aliases are supported.
- Browser-only hiding is not sufficient security evidence; verify direct endpoint responses.

## 4. Dashboard / Overview

### Purpose

Shows train summary, latest trip, status, gateway count, last data, archive count, critical alerts, gateway online/offline cards, and optional details for one gateway.

### Checks

- Search a valid train and compare values with the API/database.
- Search an unknown train and verify a clear not-found/error state.
- Confirm online gateways are green and offline gateways are red.
- A gateway is online only when its last heartbeat is no more than 1 hour old; older or missing heartbeats are offline.
- Test All Gateways, GW1, and GW2. Selected-gateway details appear only for one gateway.
- Verify heartbeat, latest alert, RMS/peak/fault counts, archive count, recent alerts, and recent archives.
- Verify empty data shows placeholders or empty tables.

### Boundary

- At most 30 non-archived alerts and 20 archives are returned for the train dashboard.
- At most 20 gateways associated with the train are used for dashboard cards.
- Summary data must remain scoped to the selected train.

## 5. Calibration (Permission Controlled)

### Purpose

Loads and saves calibration for GW1/GW2. Saving creates a new version and queues a `calibration_update` gateway command.

### Checks

- Load one gateway and Load All.
- Verify defaults when no calibration exists: ADXL offsets `0,0,0`; wheel diameter `0.915 m`; encoder PPR `100`; spatial interval `250 mm`; trigger speed `20.0 km/h`.
- Save valid ADXL left/right, bogie, and encoder values.
- Save one section only and confirm other sections are retained.
- Confirm version increment and pending command creation.
- Save a newer calibration while an older one is pending/delivered; the older command becomes superseded.
- Verify command history/status after a gateway heartbeat.
- Verify Save is blocked until `Destination reached` is selected.

### Boundary

- At least one section is required; empty save returns `400`.
- Calibration is gateway-specific and requires a registered gateway.
- Only the latest calibration is used for later archive compensation.
- Test numeric extremes, negative values, decimals, blank values, and malformed values. Record missing limits as a defect or requirement clarification instead of inventing a limit.

## 6. Alerts

### Purpose

Displays alert totals, Zone/Division/Section/Level/Date filters, two OpenStreetMap route maps, colored route points/markers, and the alert table.

### Checks

- Test Critical/RED, Warning/YELLOW, Normal/GREEN, and Total cards.
- Confirm selecting a card applies its filter.
- Verify GW1 and GW2 maps are independent and show gateway state.
- Verify legend colors: green normal, yellow warning, red critical.
- Verify table fields: time, gateway, zone, division, section, peak G, alert, location.
- Location links open Alerts and center both maps.
- Test no alerts, missing GPS, invalid/zero coordinates, and a large alert set.

### Boundary

- Map alerts return at most 200 records; RMS map data returns at most 10,000 GPS-valid records.
- Alert classification is RED when peak G > 80, YELLOW when > 50, otherwise GREEN.
- Archived sessions are excluded from the normal alert view.
- Leaflet/OpenStreetMap CDN or tile failure is separate from an API/data failure.

## 7. Archives

### Purpose

Shows processed archive upload history for the selected train.

### Checks

- Verify dates, size, RMS count, peak count, alert count, and processing status.
- Upload a valid ZIP through the gateway API and confirm it appears after refresh.
- Upload the same hash twice and verify no duplicate parsed dataset is created.
- Test malformed ZIP, missing files, bad SHA-256, and unsupported layout.
- Verify parser warnings are visible where applicable.

### Boundary

- `PUT /api/v1/archive` requires `X-Api-Key`; browser login alone is insufficient.
- Supported archive inputs include `session_metadata.json`, `rms/rms_25cm.bin`, `peak/peak_50m.bin`, `faults/faults.bin`, and supported raw time-domain files.
- Dashboard shows at most 20 archives; the direct listing endpoint returns at most 50.
- RMS validation expects a 250 mm interval with +/-25 mm tolerance.
- Spatial/alert retention is 30 days and raw time-domain retention is 7 days unless deployment configuration changes them.

## 8. Reset and Targeted Data Cleanup (Admin Only)

### Purpose

Reset closes/resets the active train session. Targeted cleanup deletes matching data by train, optional gateway, time range, and optional location radius.

### Checks

- Reset requires admin password re-entry. Test correct and incorrect password.
- Verify reset response, session status, and dashboard state.
- Test cleanup by gateway, time range, location plus radius, and combined filters.
- Test empty filters, reversed time range, missing latitude/longitude pair, zero/negative radius, and special characters in reason.
- Verify confirmation/result output identifies affected data.
- Verify non-admin UI and API access is denied.

### Boundary

- Destructive: use disposable data and capture IDs/hashes first.
- Gateway selection is All, GW1, or GW2.
- Radius defaults to 100 meters; latitude and longitude must be a pair for location filtering.
- Browser reset uses password re-entry. Non-browser clients may use configured `X-Admin-Key` where supported.

## 9. Logs (Admin Only)

### Purpose

Displays activity logs containing time, user, page, action, severity/error, IP, and location.

### Checks

- Refresh and verify newest entries appear first.
- Confirm applicable login, navigation, API error, calibration, reset, cleanup, and user actions are logged.
- Test empty data and API/database failure.

### Boundary

- The UI requests the latest 100 logs.
- API `limit` is clamped to 1 through 500.
- API filters are optional username and page.
- Logs require an authenticated operator.

## 10. User Management (Permission Controlled)

### Purpose

Authorized users can list, create, edit, deactivate, and delete operator accounts.

### Checks

- Verify username, role, active status, permissions, created date, and actions.
- Add a user with username, password, role, permissions, and active state.
- Password is mandatory for new users; blank password during edit preserves the existing password.
- Edit role, permissions, active status, and password. Username remains read-only.
- Duplicate username returns a clear error.
- Delete a non-admin user only after confirmation.
- The built-in `admin` account has no Delete action.
- Log in as the changed user and verify permissions affect tabs and APIs.

### Boundary

- UI roles are `admin` and `operator`.
- Username uniqueness is enforced by the API.
- User endpoints require `can_manage_users`.
- No client-side password strength rule is currently defined in the page; record a requirement if one is needed.

## 11. Repeated Alarm

### Purpose

Loads repeated rolling-stock alarm counts, locally filters loaded rows by RID, opens an Alarm Log report, and exports CSV/Excel/PDF.

### Checks

- Load valid dates and verify RID, count, and location.
- Search exact and partial RID after loading.
- Test no results, missing dates, reversed dates, and overlong range.
- View > Alarm Log carries RID and dates to Alarm Log Reports.
- Verify each export format and downloaded content.

### Boundary

- Date controls enforce a maximum range of 31 days.
- Data loads only after Load Report; RID typing filters loaded rows locally.
- Export uses the current date range, not only visible local search rows.

## 12. Alarm Log Reports

### Purpose

Loads alarm records by RID, date range, and alarm type; shows totals, current filters, sortable results, and exports.

### Checks

- Test populated and blank RID, all four alarm types, valid/empty/reversed/overlong dates.
- Verify total, critical, maintenance, and normal counts.
- Verify date, time, machine, train, and location columns.
- Sort every sortable column both directions.
- Verify truncation banner and export behavior.
- Test CSV, Excel, and PDF.

### Boundary

- Date range maximum is 31 days.
- UI currently sends `feedbackStatus: null`; do not test a feedback filter unless a requirement is added.
- Location links route to Alerts and map focus.

## 13. Alert Graph

### Purpose

Plots Peak or RMS G-force over time for one RID on Bogie, Axle Left, or Axle Right, with alert counts and zoom controls.

### Checks

- Empty RID must not call the API.
- Test three-digit numeric RID and `TR_` RID forms.
- Test Peak/RMS, each axis, valid dates, no data, and large data.
- Verify metadata, X/Y/Z charts, titles, threshold counts, and zoom in/out/reset.
- Verify a later no-data search removes the previous charts.

### Boundary

- Date range maximum is 31 days.
- Request contains RID, dates, and metric. Axis selection is used by chart presentation; confirm server-side axis filtering separately if required.
- No-data behavior hides charts and shows an informational error.

## 14. Gateway/API Integration

These tests are outside the browser flow but are required for gateway/data defects:

- `POST /api/v1/handshake`: registration, update, certificate, and SSH-key validation.
- `POST /api/v1/handshake/hello` and `/verify`: public-key hex, unknown session, authentication order, and HMAC validation.
- `POST /api/v1/authenticate`: valid/invalid API key and gateway/train ownership.
- `POST /api/v1/heartbeat`: bearer/body token, API key, serial mismatch, command results, and command delivery.
- `PUT /api/v1/archive`: key ownership, SHA-256, ZIP parsing, duplicate hash, and retention metadata.
- `POST /api/v1/alert`: API key/session, encrypted payload headers, JSON/schema validation, ownership, and thresholds.
- `GET /api/v1/map/alerts` and `/map/rms`: train scope, gateway filter, GPS validity, limits, and session selection.

### Authentication expectations

| Situation | Expected |
|---|---:|
| Missing operator session | `401` |
| Missing gateway API key | `401` |
| Invalid gateway API key | `403` |
| Key/session does not match gateway | `403` |
| Unknown resource | Usually `404` |
| Invalid body or hash | `400` or `422` |

## 15. Defect Report Minimum Information

Record environment URL, browser, OS, build/version, database seed, user role/permissions, page/tab, train, gateway, RID, date values, exact steps, expected/actual result, HTTP status/endpoint/request/response, console error, archive filename/SHA-256 or command ID, and whether refresh/logout/login reproduces the issue.

## 16. Explicit Out of Scope

Unless separately requested, do not treat hardware sensor accuracy, physical wheel measurement, train movement, third-party map/CDN availability, production retention deletion, unsupported gateway IDs/firmware/archive layouts, load testing beyond documented limits, or penetration testing beyond the listed authentication/authorization/input-boundary checks as dashboard functional defects.
