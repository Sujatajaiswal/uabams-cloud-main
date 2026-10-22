# Exhaustive Production Transition Plan — Line-by-Line Code Analysis

> This document is the result of a complete, line-by-line read of every source file in the `app/` directory. Each issue is referenced by its **exact file and line number**. Root-level one-off `fix_*.py` / `force_*.py` scripts are excluded (they will be removed).

---

## User Review Required

> [!IMPORTANT]
> **File Storage Architecture (Critical Decision):** The system has already been partially migrated. `telemetry.py` (lines 104–147) writes raw `.bin` files to the **filesystem** (`TIME_DOMAIN_DIR = /app/time_domain`) via `shutil.copyfileobj`. The `time_domain_chunks` table still exists in the DB schema (main.py L771–781) with a `chunk_data BYTEA` column but is currently unused. We will migrate to PostgreSQL BYTEA/Large Objects as requested.
> **Startup Schema:** Every worker runs ~625 lines of `CREATE TABLE … ALTER TABLE …` SQL on boot (main.py L461–1086). With multiple Gunicorn workers, all attempt these DDL statements concurrently. We will replace this with Alembic migrations.
> **Root-Level Scripts:** 73 one-off `fix_*.py`, `force_*.py`, `add_*.py`, `export_*.py`, `check_*.py`, `convert_*.py`, `revert_*.py`, `shorten_*.py`, and `obfuscate_*.py` scripts can be deleted outright. Confirm before removal.

---

## Open Questions

> [!WARNING]
> **`main.py` vs `telemetry.py` divergence:** `main.py` still contains a dead copy of `store_time_domain_files` (lines 158–240) that calls the old MongoDB-style `db.time_domain_chunks.delete_many(…)` and references `len(payload)` where `payload` is undefined (line 222). This dead code crashes with `NameError` if ever executed. The *live* implementation is in `telemetry.py` (lines 84–147). The dead copy in `main.py` must be deleted.
> **`telemetry.py` file encoding:** The entire file uses Windows CRLF line endings (`\r\n`), unlike `main.py` and `utils.py` which use LF. This will cause `git diff` noise and may affect certain deployment toolchains.
> **`normalize_gateway_id` hard-coding:** `middleware/auth.py` (lines 19–23) hard-codes only two gateway IDs (`GW_UABAMS_BOGIE_01`, `GW_UABAMS_BOGIE_02`). Any new gateway deployment will bypass normalization and fail alias lookups.

---

## File-by-File Analysis

---

### Component 1: `app/database.py` — Settings & Pool Holder

**File:** `database.py` — 36 lines

| Line(s) | Issue | Severity |
|---------|-------|----------|
| 7–27 | `get_settings()` is decorated with `@lru_cache` and returns a plain `dict`. Because dicts are mutable, any code that does `settings["key"] = value` would mutate the cached object globally. | Medium |
| 13 | `"jwt_secret"` default is `"change-this-secret"` — a predictable plaintext secret. In production this allows forging JWT tokens if the env var is never set. | **Critical** |
| 17 | `"admin_password"` default is `"admin123"` — stored as plaintext in settings before hashing. | **Critical** |
| 21–26 | SSH host key is hardcoded as a default string. If env var is not overridden, the gateway firmware will accept a potentially spoofed SSH server. | High |
| 31–35 | `class Database` holds only `pg_pool = None`. No connection pool configuration (min/max connections, timeout, command_timeout) is set here; all defaults are left to asyncpg. At 100+ concurrent gateways, the pool will exhaust PostgreSQL's `max_connections`. | High |

---

### Component 2: `app/db_wrapper.py` — Deprecated Stub

**File:** `db_wrapper.py` — 3 lines

| Line(s) | Issue | Severity |
|---------|-------|----------|
| 1–3 | File is 100% a comment block. Has no code. Was not deleted after the asyncpg refactor. It is still importable but does nothing. **Delete it.** | Low |

---

### Component 3: `app/models.py` — Pydantic Request Models

**File:** `models.py` — 178 lines

| Line(s) | Issue | Severity |
|---------|-------|----------|
| 25–27 | `UploadLeaseRequest.sizeBytes` is typed `int` (line 27). A 500 MB archive is valid, but the field has no `gt=0` validator — a gateway could send `sizeBytes: -1` and bypass size-mismatch checks in `telemetry.py` line 518. | Medium |
| 26 | `sha256: str` has no length or hex-format validation. A gateway can send a garbage string that will be stored as the archive's SHA-256 fingerprint and never fail verification. | Medium |
| 36 | Same `sha256` problem on `UploadCompleteRequest`. | Medium |
| 89–96 | `AlertRequest.peakValueG: float` has no min/max validator. A gateway can send `peakValueG: 9999999` which would be stored as a `RED` alert and pollute the map. | Low |
| 103–111 | `TargetedResetRequest.radiusMeters` defaults to `100.0` with no upper-bound. A malicious operator could pass `radiusMeters: 999999999` which maps to an enormous bounding box in `telemetry.py` (lines 946–962), effectively deleting all records globally. | Medium |
| 162–178 | `UserCreateRequest` and `UserUpdateRequest` have no length limits on `username` or `password`. The `validate_username` and `validate_password` functions are defined in `auth.py` (lines 11–25) and applied manually, but not as Pydantic validators. | Medium |

---

### Component 4: `app/middleware/auth.py` — Gateway Auth Middleware

**File:** `middleware/auth.py` — 116 lines

| Line(s) | Issue | Severity |
|---------|-------|----------|
| 9–12 | `PROTECTED_GATEWAY_PATHS = ("/api/v1/archive", "/api/v1/alert")`. The `/api/v1/heartbeat` endpoint is **not** in this list, so it falls through the middleware without authentication. | High |
| 19–23 | `normalize_gateway_id()` hard-codes only two specific gateway strings. Any gateway whose ID does not match these exact aliases is returned as-is without normalization. | High |
| 26–30 | `gateway_keys()` reads two env vars (`GATEWAY_API_KEY_GW01`, `GATEWAY_API_KEY_GW02`). This is a static, hard-coded allow-list for only 2 gateways. | Medium |
| 33–57 | `async_gateway_id_for_key()`: The env-var check (lines 38–45) always runs before the DB check. If `GATEWAY_API_KEY_DEFAULT` (line 43) is set in the environment, **any** request with that key is authenticated as `GW_UABAMS_BOGIE_01`. This is a backdoor. | **Critical** |
| 54–55 | The DB exception is swallowed with only a `print()`. If the DB is momentarily unavailable, the middleware silently denies all gateway requests without raising an alarm or returning a 503. | Medium |
| 66–116 | `GatewayAuthMiddleware.dispatch()`: On every request that hits `/api/v1/archive` or `/api/v1/alert`, a full DB query (`fetchrow`) is executed against `gateway_auth` or `handshake_sessions`. There is no in-process cache (e.g., `cachetools.TTLCache`). | High |
| 112–114 | Logic: if the `X-Gateway-Id` header doesn't match the DB-resolved `gateway_id`, and the supplied ID contains `"BOGIE"` or `"_"`, it is silently re-classified as `logical_gateway_id`. This pattern-matching heuristic is fragile. | Medium |

---

### Component 5: `app/utils.py` — Shared Utilities

**File:** `utils.py` — 325 lines

| Line(s) | Issue | Severity |
|---------|-------|----------|
| 17–19 | `OPERATOR_SESSION_HOURS = 12` and `SPATIAL_RETENTION_DAYS = 30` are defined here, but `main.py` also defines `SPATIAL_RETENTION_DAYS = 90` and `OPERATOR_SESSION_HOURS = 168`. These constants are **divergent**. | High |
| 22–34 | `utc_now()` and `serialize()` are defined here. They are also re-defined identically in `main.py` (lines 85–99). | High |
| 36–40 | `client_ip()` is defined here. Also defined identically in `main.py` (lines 78–82). | High |
| 90–197 | `render_login_page()` renders a large HTML string by Python f-string substitution. Structurally unsafe against XSS if input was dynamic. | Medium |
| 199–205 | `create_gateway_token()` in `utils.py` creates a JWT with only `sub`, `iat`, `exp`. The version in `main.py` adds `trainId`. Downstream code that expects `trainId` in the gateway JWT will get `None`. | High |
| 207 | `from fastapi import HTTPException` is imported in the **middle** of the file. | Low |
| 227–239 | `resolve_train_id()`: If no candidate train ID is provided, it falls back to two sequential DB queries. This generates unnecessary DB round-trips per upload. | Medium |
| 241–246 | `location_box()` returns a MongoDB-style dict (`{"$gte": …}`). This function is dead code. | Low |

---

### Component 6: `app/main.py` — Core Application Bootstrap

**File:** `main.py` — 1,306 lines

#### Duplicated Utility Functions & Dead Code (Lines 78–294)

| Line(s) | Issue | Severity |
|---------|-------|----------|
| 78–155 | `client_ip`, `utc_now`, `serialize`, `_positive_factor`, `apply_wheel_compensation` — **exact duplicates** of `utils.py`. | High |
| 158–240 | **Dead `store_time_domain_files`**. References undefined variable `payload` (line 222). Fatal `NameError` if ever executed. Delete it. | **Critical** |
| 243–294 | Session and token validation functions duplicated from `utils.py`. | High |
| 323–403 | Dead `render_login_page` duplicate. | High |

#### `startup()` Function — The Schema Monster (Lines 430–1215)

| Line(s) | Issue | Severity |
|---------|-------|----------|
| 430 | `@app.on_event("startup")` is **deprecated** in FastAPI. Replace with `lifespan` context managers. | Medium |
| 440 | `asyncpg.create_pool(settings["database_url"])` uses no `min_size`, `max_size`. The pool grows unbounded. | **Critical** |
| 458–1086 | **The entire schema is executed in a single `conn.execute("""…""")` string**. Every `ALTER TABLE … ADD COLUMN IF NOT EXISTS` is re-run on every startup. | Medium |
| 493 | `ALTER TABLE gateway_auth DROP CONSTRAINT IF EXISTS gateway_auth_pkey CASCADE` is run on **every startup**. Dropping a primary key constraint drops its backing index, forcing PostgreSQL to rebuild it every restart. | **Critical** |
| 805–812 | `ALTER TABLE heartbeat_logs ADD COLUMN IF NOT EXISTS logical_gateway_id …` is executed **before** `CREATE TABLE IF NOT EXISTS heartbeat_logs` (lines 814–823). This fails and swallows the error on fresh install. | **Critical** |
| 1186–1211 | **Default users are re-seeded on every startup**, wiping custom admin permissions on every restart. | High |

---

### Component 7: `app/parsers/archive.py` — Binary Protocol Parser

**File:** `parsers/archive.py` — 448 lines

| Line(s) | Issue | Severity |
|---------|-------|----------|
| 60–96 | `parse_archive_zip(body)`: A 100 MB archive occupies memory multiple times simultaneously due to `BytesIO` and `.read()` loads. | High |
| 99–162 | `parse_rms_bytes()`: Iterates synchronously. For a 50 MB binary at 70 bytes/record, that is ~750,000 iterations calling `struct.unpack`. Because it is called directly from the FastAPI event loop, **this blocks the entire event loop** for the duration of parsing. | **Critical** |
| 432–437 | Hardcoded alert thresholds `> 80` for RED and `> 50` for YELLOW. Duplicated in `telemetry.py`. | Medium |

---

### Component 8: `app/routers/telemetry.py` — The Largest Router

**File:** `telemetry.py` — 1,359 lines

| Line(s) | Issue | Severity |
|---------|-------|----------|
| 1–29 | Standalone PDF generation utility placed at the top of a router file. Violates single responsibility. | Medium |
| 68–82 | Two separate `db.pg_pool.execute()` calls update `gateways` and `gateway_status`. These are **not in a transaction**. | Medium |
| 104–105 | Writes archives to the **local filesystem of the container**. Must be migrated to DB BYTEA chunks to support horizontal scaling. | High |
| 158 | `parsed = parse_archive_zip(source)` — synchronous CPU-blocking call blocks the async event loop. | **Critical** |
| 172–175 | Four sequential `DELETE FROM` statements without a transaction in archive ingestion. | High |
| 388 | `PUT /api/v1/archive` endpoint loads the **entire request body into RAM** before the handler executes. For a 500 MB archive, 500 MB of RAM usage per concurrent upload. | **Critical** |
| 909–910 | Plaintext admin password check in `TargetedResetRequest`. | Medium |
| 964–969 | Data reset endpoint does multi-table DELETEs without a transaction. | High |
| 1272 | `UPDATE alert_events SET enroute_diagnosis = …` uses columns that **do not exist** in the `alert_events` schema. Will fail with `PostgreSQL column does not exist`. | **Critical** |

---

### Component 9: `app/routers/auth.py` & Component 10: `app/routers/gateways.py`

| File | Line(s) | Issue | Severity |
|------|---------|-------|----------|
| `auth.py` | 110–111 | API key generation reuses existing keys instead of rotating them. | High |
| `auth.py` | 333–390 | `stored_key != data.apiKey` compared with `!=` instead of `hmac.compare_digest()`. **Timing side-channel**. | High |
| `gateways.py` | 454–522 | 8 sequential DB queries on the same connection. Could be parallelized with `asyncio.gather()`. | High |
| `gateways.py` | 524–528 | `fallback_peak_g, fallback_alert = (latest_peak.get("axes"))` — `axes` is a JSONB dict, not a 2-tuple. This unpacking will raise `ValueError` at runtime. | **Critical** |

---

### Component 11: `app/routers/ui.py` & Component 12: `app/routers/logs.py`

| File | Line(s) | Issue | Severity |
|------|---------|-------|----------|
| `ui.py` | 113–130 | Dual-storage of auth token in `httponly` cookie AND JSON response (stored in `sessionStorage`). | Medium |
| `ui.py` | 150–159 | Session token exposed in the URL query string (`?session_token=...`). | Medium |
| `logs.py` | 58 | Hardcoded filter hides `javascript_error` from operators. | Low |

---

### Component 13: Infrastructure (`Dockerfile`, `docker-compose.yml`, `requirements.txt`)

| File | Line(s) | Issue | Severity |
|------|---------|-------|----------|
| `Dockerfile` | 14 | Single Uvicorn worker. No multi-worker support (`gunicorn`). | **Critical** |
| `docker-compose` | 36 | Default `JWT_SECRET: local-docker-change-me` is predictable. | **Critical** |
| `requirements` | 1 | `fastapi` and most other dependencies are not pinned to specific versions. | High |

---

## Priority Bug Summary (Must Fix Before Production)

| # | File | Line(s) | Bug Description |
|---|------|---------|-----------------|
| 1 | `main.py` | 222, 234 | `NameError: payload` in dead `store_time_domain_files` copy |
| 2 | `main.py` | 805 | `ALTER TABLE heartbeat_logs` before `CREATE TABLE heartbeat_logs` |
| 3 | `main.py` | 493 | Drops and rebuilds `gateway_auth` PK on every startup |
| 4 | `main.py` | 440 | No pool size limits — will exhaust PostgreSQL `max_connections` |
| 5 | `telemetry.py` | 388 | Full archive body loaded into RAM (no streaming) |
| 6 | `telemetry.py` | 158 | Synchronous CPU-blocking parse in async event loop |
| 7 | `telemetry.py` | 1272 | UPDATE on non-existent `enroute_diagnosis` column |
| 8 | `gateways.py` | 524–528 | `ValueError` unpacking JSONB dict as a tuple |
| 9 | `auth.py` | 355 | Timing side-channel: `!=` instead of `compare_digest` |
| 10 | `middleware/auth.py` | 43–45 | `GATEWAY_API_KEY_DEFAULT` backdoor |
| 11 | `Dockerfile` | 14 | Single Uvicorn worker (no multi-worker support) |
| 12 | `docker-compose.yml` | 36 | Default JWT_SECRET is a predictable string |

---

## Execution Plan: Production-Grade Conversion

### Phase 1: Immediate Bug Fixes & Dead Code Removal (Layer 0)
1. **Remove Dead/Redundant Code**
   - Delete `app/db_wrapper.py`.
   - Remove the dead copy of `store_time_domain_files` (lines 158-240) in `app/main.py` which contains the fatal `NameError`.
   - Delete duplicate utility functions in `app/main.py` and enforce imports from `app/utils.py`.
   - Remove redundant `render_login_page`, `create_gateway_token`, and `verify_gateway_token` from `app/main.py`.
2. **Fix Critical API & Auth Bugs**
   - Address the plaintext password/API key comparison timing attacks in `app/routers/auth.py` and `app/routers/telemetry.py` by using `hmac.compare_digest`.
   - Patch `app/routers/gateways.py` tuple unpacking bug on JSONB object.
   - Remove the backdoor `GATEWAY_API_KEY_DEFAULT` logic from `app/middleware/auth.py`.
   - Fix missing `enroute_diagnosis` columns for the feedback endpoint.

### Phase 2: Unblock Event Loop & Optimize Resources (Layer 1)
1. **Offload Heavy Parsing to Process Pool**
   - In `app/routers/telemetry.py`, wrap `parse_archive_zip` using `asyncio.get_running_loop().run_in_executor(ProcessPoolExecutor(), ...)` so CPU-intensive binary parsing doesn't block the FastAPI event loop.
2. **Optimize Memory Usage**
   - Switch the `/api/v1/archive` endpoint from reading the entire `Body()` into memory to streaming the upload directly to a temporary file via `Request.stream()`.
3. **Database Concurrency Limits**
   - Explicitly configure `asyncpg.create_pool` with `min_size` and `max_size` to cap DB connections.
   - Wrap destructive mass DELETE routes (e.g., reset endpoints) inside `async with conn.transaction():`.

### Phase 3: PostgreSQL Schema Migration (Layer 2)
1. **Extract Schema to Alembic**
   - Initialize `alembic` in the project.
   - Move the 600+ lines of raw SQL inside `app/main.py`'s `@app.on_event("startup")` into an initial Alembic migration file.
   - Fix schema bugs: ensure `heartbeat_logs` is created before altering it, and stop dropping and recreating `gateway_auth_pkey` on every boot.

### Phase 4: Storage Migration & Infrastructure (Layer 3)
1. **Migrate Archive Storage**
   - Rewrite `store_time_domain_files` to insert raw file chunks into PostgreSQL `time_domain_chunks` via `BYTEA`, rather than writing to local disk, making workers stateless and horizontally scalable.
2. **Delete Legacy Root Scripts**
   - Purge the 70+ old `fix_*.py` one-off operational scripts from the root directory.
3. **Infrastructure Upgrades**
   - Update `Dockerfile` to launch via Gunicorn with Uvicorn workers (`gunicorn -k uvicorn.workers.UvicornWorker -w 4 ...`) instead of a single Uvicorn process.
   - Fix the default `JWT_SECRET` predictability in `docker-compose.yml`.
   - Pin requirements in `requirements.txt`.
