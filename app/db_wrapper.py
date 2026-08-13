import json
import logging
from datetime import datetime, UTC
import asyncpg


logger = logging.getLogger("db_wrapper")

FIELD_MAP = {
    "gatewayId": "gateway_id",
    "trainId": "train_id",
    "lastHeartbeat": "last_heartbeat",
    "lastHandshake": "last_handshake",
    "apiKey": "secret_key",
    "createdAt": "created_at",
    "rawZipData": "raw_zip_data",
    "adxlState": "adxl_state",
    "adxlUptime": "adxl_uptime",
    "adxlFaults": "adxl_faults",
    "adxlFwVersion": "adxl_fw_version",
    "adxlCalVersion": "adxl_cal_version",
    "encoderState": "encoder_state",
    "encoderUptime": "encoder_uptime",
    "encoderFaults": "encoder_faults",
    "encoderFwVersion": "encoder_fw_version",
    "encoderCalVersion": "encoder_cal_version",
    "updatedAt": "updated_at",
    "scaleX": "scale_x",
    "scaleY": "scale_y",
    "scaleZ": "scale_z",
    "offsetX": "offset_x",
    "offsetY": "offset_y",
    "offsetZ": "offset_z",
    "trainNo": "train_no",
    "trainName": "train_name",
    "alertType": "alert_type",
    "positionMm": "position_mm",
    "receivedAt": "received_at",
    "sessionName": "session_name",
    "archiveSha256": "archive_sha256",
    "gpsValid": "gps_valid",
    "windowStartMm": "window_start_mm",
    "timestampMs": "timestamp_ms",
    "faultCode": "fault_code",
    "description": "description",
    "errorMessage": "error_message",
    "ipAddress": "ip_address",
    "sessionId": "session_id",
    "totalSize": "total_size",
    "fileId": "file_id",
    "chunkIndex": "chunk_index",
    "chunkData": "chunk_data",
    "peakAxis": "peak_axis",
    "peakValueG": "peak_value_g",
    "speedKmph": "speed_kmph",
    "sessionStatus": "session_status",
    "archivedAt": "archived_at",
    "gatewaySerial": "gateway_serial",
    "firmwareVersion": "firmware_version",
    "lastSeen": "last_seen",
    "sizeBytes": "size_bytes",
    "certFingerprint": "cert_fingerprint",
    "serverPrivateKeyHex": "server_private_key_hex",
    "clientPublicKeyHex": "client_public_key_hex",
    "pendingReset": "pending_reset",
    "commandId": "command_id",
    "payloadUrl": "payload_url",
    "deliveredAt": "delivered_at",
    "completedAt": "completed_at",
    "lastDeliveredAt": "last_delivered_at",
    "deliveryCount": "delivery_count",
    "closedAt": "closed_at",
    "sessionKeyHex": "session_key_hex",
    "verifiedAt": "verified_at",
    "authenticated": "authenticated",
    "lastAuthenticated": "last_authenticated",
    "sshPublicKey": "ssh_public_key",
    "uploadEnabled": "upload_enabled",
    "uploadBasePath": "upload_base_path",
    "revokedAt": "revoked_at",
    "uploadId": "upload_id",
    "zipFileName": "zip_file_name",
    "remoteTempPath": "remote_temp_path",
    "remoteFinalPath": "remote_final_path",
    "expiresUtc": "expires_utc",
}

REV_MAP = {v: k for k, v in FIELD_MAP.items()}

TABLE_COLUMNS = {
    "gateways": ["gateway_id", "train_id", "gateway_serial", "firmware_version", "status", "provision_status", "last_seen", "last_heartbeat", "updated_at", "created_at"],
    "gateway_auth": ["gateway_id", "train_id", "secret_key", "cert_fingerprint", "last_authenticated", "created_at", "ssh_public_key", "upload_enabled", "upload_base_path", "revoked_at"],
    "upload_leases": [
        "upload_id", "gateway_id", "train_id", "session_name", "zip_file_name",
        "sha256", "size_bytes", "remote_temp_path", "remote_final_path", "status",
        "expires_utc", "created_at"
    ],
    "gateway_status": [
        "gateway_id", "adxl_state", "adxl_uptime", "adxl_faults", "adxl_fw_version", "adxl_cal_version",
        "encoder_state", "encoder_uptime", "encoder_faults", "encoder_fw_version", "encoder_cal_version", "updated_at",
        "train_id", "online", "last_heartbeat", "last_handshake"
    ],
    "calibrations": [
        "train_id", "gateway_id", "version",
        "adxl_left_offset_x", "adxl_left_offset_y", "adxl_left_offset_z",
        "adxl_right_offset_x", "adxl_right_offset_y", "adxl_right_offset_z",
        "iis_offset_x", "iis_offset_y", "iis_offset_z",
        "imu_accel_offset_x", "imu_accel_offset_y", "imu_accel_offset_z",
        "imu_gyro_offset_x", "imu_gyro_offset_y", "imu_gyro_offset_z",
        "wheel_diameter_m", "encoder_ppr", "spatial_interval_mm", "trigger_start_speed_kmph",
        "adxl_left", "adxl_right", "bogie", "encoder", "updated_at"
    ],
    "calibration_versions": [
        "train_id", "gateway_id", "version",
        "adxl_left_offset_x", "adxl_left_offset_y", "adxl_left_offset_z",
        "adxl_right_offset_x", "adxl_right_offset_y", "adxl_right_offset_z",
        "iis_offset_x", "iis_offset_y", "iis_offset_z",
        "imu_accel_offset_x", "imu_accel_offset_y", "imu_accel_offset_z",
        "imu_gyro_offset_x", "imu_gyro_offset_y", "imu_gyro_offset_z",
        "wheel_diameter_m", "encoder_ppr", "spatial_interval_mm", "trigger_start_speed_kmph",
        "adxl_left", "adxl_right", "bogie", "encoder", "created_at"
    ],
    "alert_events": [
        "train_no", "gateway_id", "alert_type", "latitude", "longitude", "position_mm", "created_at",
        "session_name", "archive_sha256", "source", "peak_axis", "peak_value_g", "speed_kmph", "alert", "session_status", "archived_at"
    ],
    "archives": [
        "gateway_id", "sha256", "received_at", "train_id", "session_name",
        "session_status", "size_bytes", "status", "parse_warnings"
    ],
    "heartbeat_logs": ["gateway_id", "train_id", "received_at", "adxl_state", "encoder_state"],
    "rms_records": [
        "train_id", "gateway_id", "session_name", "archive_sha256", "latitude", "longitude", "gps_valid",
        "bearing", "speed", "position_mm", "axes", "created_at",
        "al_x_g", "al_y_g", "al_z_g",
        "ar_x_g", "ar_y_g", "ar_z_g",
        "bg_x_g", "bg_y_g", "bg_z_g",
    ],
    "peak_records": [
        "train_id", "gateway_id", "archive_sha256", "window_start_mm", "axes", "created_at",
        "position_mm", "speed_kmph", "latitude", "longitude"
    ],
    "fault_records": ["train_id", "gateway_id", "archive_sha256", "timestamp_ms", "fault_code", "description", "created_at"],
    "sessions": ["train_no", "session_name", "status", "created_at", "closed_at"],
    "reset_events": ["train_no", "reason", "created_at"],
    "activity_logs": ["username", "page", "action", "error_message", "ip_address", "latitude", "longitude", "created_at"],
    "handshake_sessions": [
        "session_id", "gateway_id", "train_id", "server_private_key_hex", "client_public_key_hex",
        "nonce", "verified", "authenticated", "session_key_hex", "verified_at", "created_at"
    ],
    "time_domain_files": [
        "file_id", "gateway_id", "train_id", "session_name", "archive_sha256",
        "filename", "path", "size_bytes", "sha256", "chunk_count", "total_size",
        "created_at", "expires_at"
    ],
    "time_domain_chunks": ["file_id", "gateway_id", "train_id", "archive_sha256", "chunk_index", "chunk_data", "created_at", "expires_at"],
    "trains": ["train_no", "train_name", "created_at"],
    "gateway_commands": [
        "command_id", "gateway_id", "type", "status",
        "version", "payload_url", "sha256", "payload", "result",
        "created_at", "delivered_at", "last_delivered_at", "delivery_count", "completed_at"
    ],
}

JSON_COLUMNS = {
    "adxl_left", "adxl_right", "bogie", "encoder", "axes", "payload", "result",
}

PRIMARY_KEYS = {
    "gateways": "gateway_id",
    "gateway_auth": "gateway_id, train_id",
    "gateway_status": "gateway_id",
    "calibrations": "gateway_id",
    "handshake_sessions": "session_id",
    "trains": "train_no",
    "upload_leases": "upload_id",
    "gateway_commands": "command_id",
}


def translate_filter(table_name, mongo_filter, start_param_idx=1):
    if not mongo_filter:
        return "TRUE", {}

    where_clauses = []
    params = {}
    param_idx = start_param_idx

    for key, value in mongo_filter.items():
        if key == "$or" and isinstance(value, list):
            or_clauses = []
            for sub_filter in value:
                sub_where, sub_params = translate_filter(table_name, sub_filter, start_param_idx=param_idx)
                or_clauses.append(f"({sub_where})")
                params.update(sub_params)
                param_idx += len(sub_params)
            if or_clauses:
                where_clauses.append(f"({' OR '.join(or_clauses)})")
            continue

        pg_col = FIELD_MAP.get(key, key)
        if key == "_id":
            pg_col = "id"
            if hasattr(value, "__str__"):
                try:
                    value = int(str(value))
                except ValueError:
                    pass

        if isinstance(value, dict):
            for op, val in value.items():
                param_name = f"p_{param_idx}"
                param_idx += 1

                if op == "$gte":
                    where_clauses.append(f"{pg_col} >= ${param_name}")
                    params[param_name] = val
                elif op == "$lte":
                    where_clauses.append(f"{pg_col} <= ${param_name}")
                    params[param_name] = val
                elif op == "$gt":
                    where_clauses.append(f"{pg_col} > ${param_name}")
                    params[param_name] = val
                elif op == "$lt":
                    where_clauses.append(f"{pg_col} < ${param_name}")
                    params[param_name] = val
                elif op == "$ne":
                    if val is None:
                        where_clauses.append(f"{pg_col} IS NOT NULL")
                    else:
                        where_clauses.append(f"({pg_col} IS NULL OR {pg_col} <> ${param_name})")
                        params[param_name] = val
                elif op == "$in":
                    if not val:
                        where_clauses.append("FALSE")
                        continue
                    placeholders = []
                    for item in val:
                        item_param = f"p_{param_idx}"
                        param_idx += 1
                        placeholders.append(f"${item_param}")
                        params[item_param] = item
                    where_clauses.append(f"{pg_col} IN ({', '.join(placeholders)})")
                elif op == "$nin":
                    non_null_values = [item for item in val if item is not None]
                    clauses = []
                    if non_null_values:
                        placeholders = []
                        for item in non_null_values:
                            item_param = f"p_{param_idx}"
                            param_idx += 1
                            placeholders.append(f"${item_param}")
                            params[item_param] = item
                        clauses.append(f"{pg_col} NOT IN ({', '.join(placeholders)})")
                    if None in val:
                        clauses.append(f"{pg_col} IS NOT NULL")
                    where_clauses.append(f"({' AND '.join(clauses)})" if clauses else "TRUE")
                elif op == "$exists":
                    where_clauses.append(f"{pg_col} IS {'NOT ' if val else ''}NULL")
                else:
                    raise ValueError(f"Unsupported filter operator {op!r} for {table_name}.{key}")
        else:
            if value is None:
                where_clauses.append(f"{pg_col} IS NULL")
            else:
                param_name = f"p_{param_idx}"
                param_idx += 1
                where_clauses.append(f"{pg_col} = ${param_name}")
                params[param_name] = value

    where_str = " AND ".join(where_clauses) if where_clauses else "TRUE"
    return where_str, params


import re
def replace_named_params(sql_str, params):
    sorted_keys = sorted(params.keys(), key=lambda k: int(k.split("_")[1]))
    arg_list = []
    final_sql = sql_str
    for idx, key in enumerate(sorted_keys):
        # Only replace exact placeholder, not substrings like $p_10 when looking for $p_1
        final_sql = re.sub(r'\$' + key + r'(?!\d)', f"${idx + 1}", final_sql)
        arg_list.append(params[key])
    return final_sql, arg_list


class InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class DeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count



class CursorWrapper:
    def __init__(self, collection, filter_dict, projection=None, sort_list=None):
        self.collection = collection
        self.filter_dict = filter_dict
        self.projection = projection
        self.sort_list = sort_list
        self.limit_val = None

    def limit(self, limit_val):
        self.limit_val = limit_val
        return self

    def sort(self, sort_list_or_key, direction=None):
        if isinstance(sort_list_or_key, str):
            self.sort_list = [(sort_list_or_key, direction or 1)]
        else:
            self.sort_list = sort_list_or_key
        return self

    async def to_list(self, length=None):
        return await self.collection.execute_find(self.filter_dict, self.sort_list, self.limit_val or length)


class CollectionWrapper:
    def __init__(self, table_name, pg_pool):
        self.table_name = table_name
        self.pg_pool = pg_pool

    def _map_row(self, row):
        if row is None:
            return None
        row_dict = dict(row)
        mapped = {}
        for col, val in row_dict.items():
            if col in ("id", "session_id", "gateway_id", "train_no") and "_id" not in mapped:
                mapped["_id"] = str(val)
            if col == "id":
                continue
            if col in JSON_COLUMNS:
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except Exception:
                        pass
                if col == "axes" and isinstance(val, dict):
                    for k, v in val.items():
                        mapped[k] = v
            mapped_key = REV_MAP.get(col, col)
            mapped[mapped_key] = val
        return mapped

    async def find_one(self, filter_dict, projection=None, sort=None):
        where_str, params = translate_filter(self.table_name, filter_dict)
        order_by = ""
        if sort:
            parts = []
            for key, direction in sort:
                pg_col = FIELD_MAP.get(key, key)
                if key == "_id":
                    pg_col = "id"
                dir_str = "DESC" if direction == -1 else "ASC"
                parts.append(f"{pg_col} {dir_str}")
            order_by = f"ORDER BY {', '.join(parts)}"

        sql_where, arg_list = replace_named_params(where_str, params)
        sql = f"SELECT * FROM {self.table_name} WHERE {sql_where} {order_by} LIMIT 1"

        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow(sql, *arg_list)

        return self._map_row(row)

    def find(self, filter_dict, projection=None, sort=None):
        return CursorWrapper(self, filter_dict, projection, sort)

    async def execute_find(self, filter_dict, sort_list, limit_val):
        where_str, params = translate_filter(self.table_name, filter_dict)
        order_by = ""
        if sort_list:
            parts = []
            for key, direction in sort_list:
                pg_col = FIELD_MAP.get(key, key)
                if key == "_id":
                    pg_col = "id"
                dir_str = "DESC" if direction == -1 else "ASC"
                parts.append(f"{pg_col} {dir_str}")
            order_by = f"ORDER BY {', '.join(parts)}"

        limit_str = f"LIMIT {limit_val}" if limit_val is not None else ""
        sql_where, arg_list = replace_named_params(where_str, params)
        sql = f"SELECT * FROM {self.table_name} WHERE {sql_where} {order_by} {limit_str}"

        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(sql, *arg_list)

        return [self._map_row(row) for row in rows]

    async def insert_one(self, document):
        doc = dict(document)
        insert_data = {}
        axes_data = {}
        for k, v in doc.items():
            if k == "_id":
                continue
            pg_col = FIELD_MAP.get(k, k)
            if pg_col in TABLE_COLUMNS.get(self.table_name, []):
                if isinstance(v, (dict, list)):
                    v = json.dumps(v)
                insert_data[pg_col] = v
            else:
                axes_data[k] = v

        if axes_data and "axes" in TABLE_COLUMNS.get(self.table_name, []):
            insert_data["axes"] = json.dumps(axes_data)

        pk_col = PRIMARY_KEYS.get(self.table_name, "id")
        if self.table_name in ["gateways", "gateway_auth", "gateway_status", "calibrations"]:
            if "gatewayId" in doc:
                insert_data["gateway_id"] = doc["gatewayId"]
        elif self.table_name == "handshake_sessions":
            pk_col = "session_id"
            if "sessionId" in doc:
                insert_data["session_id"] = doc["sessionId"]
        elif self.table_name == "trains":
            pk_col = "train_no"
            if "trainNo" in doc:
                insert_data["train_no"] = doc["trainNo"]
        elif self.table_name == "upload_leases":
            if "uploadId" in doc:
                insert_data["upload_id"] = doc["uploadId"]
        elif self.table_name == "gateway_commands" and "commandId" in doc:
            insert_data["command_id"] = doc["commandId"]

        cols = list(insert_data.keys())
        placeholders = []
        for i, c in enumerate(cols):
            if c in JSON_COLUMNS and isinstance(insert_data[c], str):
                placeholders.append(f"${i+1}::jsonb")
            else:
                placeholders.append(f"${i+1}")
        sql = f"INSERT INTO {self.table_name} ({', '.join(cols)}) VALUES ({', '.join(placeholders)}) RETURNING {pk_col}"

        values = [insert_data[c] for c in cols]
        async with self.pg_pool.acquire() as conn:
            res_val = await conn.fetchval(sql, *values)

        return InsertOneResult(inserted_id=str(res_val))

    async def insert_many(self, documents):
        if not documents:
            return InsertManyResult([])

        inserted_ids = []
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                for doc in documents:
                    d = dict(doc)
                    insert_data = {}
                    axes_data = {}
                    for k, v in d.items():
                        if k == "_id":
                            continue
                        pg_col = FIELD_MAP.get(k, k)
                        if pg_col in TABLE_COLUMNS.get(self.table_name, []):
                            if isinstance(v, (dict, list)):
                                v = json.dumps(v)
                            insert_data[pg_col] = v
                        else:
                            axes_data[k] = v

                    if axes_data and "axes" in TABLE_COLUMNS.get(self.table_name, []):
                        insert_data["axes"] = json.dumps(axes_data)

                    pk_col = PRIMARY_KEYS.get(self.table_name, "id")
                    if self.table_name in ["gateways", "gateway_auth", "gateway_status", "calibrations"]:
                        if "gatewayId" in d:
                            insert_data["gateway_id"] = d["gatewayId"]
                    elif self.table_name == "handshake_sessions":
                        pk_col = "session_id"
                        if "sessionId" in d:
                            insert_data["session_id"] = d["sessionId"]
                    elif self.table_name == "trains":
                        pk_col = "train_no"
                        if "trainNo" in d:
                            insert_data["train_no"] = d["trainNo"]
                    elif self.table_name == "upload_leases":
                        if "uploadId" in d:
                            insert_data["upload_id"] = d["uploadId"]
                    elif self.table_name == "gateway_commands" and "commandId" in d:
                        insert_data["command_id"] = d["commandId"]

                    cols = list(insert_data.keys())
                    placeholders = []
                    for i, c in enumerate(cols):
                        if c in JSON_COLUMNS and isinstance(insert_data[c], str):
                            placeholders.append(f"${i+1}::jsonb")
                        else:
                            placeholders.append(f"${i+1}")
                    sql = f"INSERT INTO {self.table_name} ({', '.join(cols)}) VALUES ({', '.join(placeholders)}) RETURNING {pk_col}"

                    values = [insert_data[c] for c in cols]
                    res_val = await conn.fetchval(sql, *values)
                    inserted_ids.append(str(res_val))

        return InsertManyResult(inserted_ids)

    async def update_one(self, filter_dict, update_dict, upsert=False):
        set_data = update_dict.get("$set", {})
        if not set_data:
            return

        set_fields = {}
        axes_data = {}
        for k, v in set_data.items():
            pg_col = FIELD_MAP.get(k, k)
            if pg_col in TABLE_COLUMNS.get(self.table_name, []):
                if isinstance(v, (dict, list)):
                    v = json.dumps(v)
                set_fields[pg_col] = v
            else:
                axes_data[k] = v

        if axes_data and "axes" in TABLE_COLUMNS.get(self.table_name, []):
            set_fields["axes"] = json.dumps(axes_data)

        if not set_fields:
            return

        if upsert:
            pk_col = PRIMARY_KEYS.get(self.table_name, "id")

            insert_data = {}
            for k, v in filter_dict.items():
                col = FIELD_MAP.get(k, k)
                if k == "_id":
                    col = "id"
                if col in TABLE_COLUMNS.get(self.table_name, []):
                    insert_data[col] = v
            for k, v in set_fields.items():
                insert_data[k] = v

            on_insert_data = update_dict.get("$setOnInsert", {})
            for k, v in on_insert_data.items():
                pg_col = FIELD_MAP.get(k, k)
                if pg_col in TABLE_COLUMNS.get(self.table_name, []):
                    if isinstance(v, (dict, list)):
                        v = json.dumps(v)
                    insert_data[pg_col] = v

            cols = list(insert_data.keys())
            placeholders = []
            for i, c in enumerate(cols):
                if c in JSON_COLUMNS and isinstance(insert_data[c], str):
                    placeholders.append(f"${i+1}::jsonb")
                else:
                    placeholders.append(f"${i+1}")

            update_clauses = []
            for col in set_fields.keys():
                if col != pk_col:
                    update_clauses.append(f"{col} = EXCLUDED.{col}")
            if update_clauses:
                sql = f"""
                    INSERT INTO {self.table_name} ({', '.join(cols)})
                    VALUES ({', '.join(placeholders)})
                    ON CONFLICT ({pk_col})
                    DO UPDATE SET {', '.join(update_clauses)}
                """
            else:
                sql = f"""
                    INSERT INTO {self.table_name} ({', '.join(cols)})
                    VALUES ({', '.join(placeholders)})
                    ON CONFLICT ({pk_col})
                    DO NOTHING
                """
            values = [insert_data[c] for c in cols]
            async with self.pg_pool.acquire() as conn:
                await conn.execute(sql, *values)
        else:
            set_clauses = []
            param_idx = 1
            sql_params = {}

            for col, val in set_fields.items():
                param_name = f"u_{param_idx}"
                param_idx += 1
                if col in JSON_COLUMNS and isinstance(val, str):
                    set_clauses.append(f"{col} = ${param_name}::jsonb")
                else:
                    set_clauses.append(f"{col} = ${param_name}")
                sql_params[param_name] = val

            where_clauses = []
            for key, value in filter_dict.items():
                pg_col = FIELD_MAP.get(key, key)
                if key == "_id":
                    pg_col = "id"
                    if hasattr(value, "__str__"):
                        try:
                            value = int(str(value))
                        except ValueError:
                            pass
                param_name = f"f_{param_idx}"
                param_idx += 1
                where_clauses.append(f"{pg_col} = ${param_name}")
                sql_params[param_name] = value

            where_str = " AND ".join(where_clauses) if where_clauses else "TRUE"
            combined_sql = f"UPDATE {self.table_name} SET {', '.join(set_clauses)} WHERE {where_str}"
            final_sql, final_args = replace_named_params(combined_sql, sql_params)
            print(f"DEBUG SQL: final_sql={repr(final_sql)}, final_args={repr(final_args)}", flush=True)
            async with self.pg_pool.acquire() as conn:
                await conn.execute(final_sql, *final_args)

    async def update_many(self, filter_dict, update_dict):
        set_data = update_dict.get("$set", {})
        if not set_data:
            return

        set_fields = {}
        for key, value in set_data.items():
            pg_col = FIELD_MAP.get(key, key)
            if pg_col not in TABLE_COLUMNS.get(self.table_name, []):
                continue
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            set_fields[pg_col] = value
        if not set_fields:
            return

        offset = len(set_fields) + 1
        where_str, where_params = translate_filter(self.table_name, filter_dict, start_param_idx=offset)
        params = {}
        set_clauses = []
        for index, (column, value) in enumerate(set_fields.items(), start=1):
            name = f"p_{index}"
            params[name] = value
            if column in JSON_COLUMNS and isinstance(value, str):
                set_clauses.append(f"{column} = ${name}::jsonb")
            else:
                set_clauses.append(f"{column} = ${name}")

        params.update(where_params)
        sql, args = replace_named_params(
            f"UPDATE {self.table_name} SET {', '.join(set_clauses)} WHERE {where_str}", params
        )
        async with self.pg_pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def replace_one(self, filter_dict, replacement, upsert=False):
        return await self.update_one(filter_dict, {"$set": replacement}, upsert=upsert)

    async def count_documents(self, filter_dict):
        where_str, params = translate_filter(self.table_name, filter_dict)
        sql_where, args = replace_named_params(where_str, params)
        sql = f"SELECT COUNT(*) FROM {self.table_name} WHERE {sql_where}"
        async with self.pg_pool.acquire() as conn:
            return int(await conn.fetchval(sql, *args))

    async def delete_many(self, filter_dict):
        where_str, params = translate_filter(self.table_name, filter_dict)
        sql_where, arg_list = replace_named_params(where_str, params)
        sql = f"DELETE FROM {self.table_name} WHERE {sql_where}"
        async with self.pg_pool.acquire() as conn:
            res_str = await conn.execute(sql, *arg_list)

        deleted_count = 0
        if res_str and res_str.startswith("DELETE "):
            try:
                deleted_count = int(res_str.split(" ")[1])
            except Exception:
                pass
        return DeleteResult(deleted_count)

    async def delete_one(self, filter_dict):
        where_str, params = translate_filter(self.table_name, filter_dict)
        sql_where, arg_list = replace_named_params(where_str, params)

        pk_col = PRIMARY_KEYS.get(self.table_name, "id").split(",", 1)[0].strip()

        sql = f"DELETE FROM {self.table_name} WHERE {pk_col} IN (SELECT {pk_col} FROM {self.table_name} WHERE {sql_where} LIMIT 1)"
        async with self.pg_pool.acquire() as conn:
            await conn.execute(sql, *arg_list)

    async def create_index(self, *args, **kwargs):
        pass

    def aggregate(self, pipeline):
        match_stage = None
        for stage in pipeline:
            if "$match" in stage:
                match_stage = stage["$match"]

        from_dt = None
        to_dt = None
        if match_stage and "createdAt" in match_stage:
            created_at_filter = match_stage["createdAt"]
            if isinstance(created_at_filter, dict):
                from_dt = created_at_filter.get("$gte")
                to_dt = created_at_filter.get("$lte")

        return AggregateCursorWrapper(self, from_dt, to_dt)


class AggregateCursorWrapper:
    def __init__(self, collection, from_dt, to_dt):
        self.collection = collection
        self.from_dt = from_dt
        self.to_dt = to_dt

    async def to_list(self, length=None):
        if self.from_dt is None or self.to_dt is None:
            return []

        sql = """
            WITH latest_alerts AS (
                SELECT
                    train_no,
                    latitude,
                    longitude,
                    ROW_NUMBER() OVER (PARTITION BY train_no ORDER BY created_at DESC) as rn
                FROM alert_events
                WHERE created_at >= $1 AND created_at <= $2
            ),
            counts AS (
                SELECT
                    train_no,
                    COUNT(*) as count
                FROM alert_events
                WHERE created_at >= $1 AND created_at <= $2
                GROUP BY train_no
            )
            SELECT
                c.train_no AS "_id",
                c.count,
                l.latitude,
                l.longitude
            FROM counts c
            JOIN latest_alerts l ON c.train_no = l.train_no AND l.rn = 1
            ORDER BY c.count DESC
        """
        async with self.collection.pg_pool.acquire() as conn:
            rows = await conn.fetch(sql, self.from_dt, self.to_dt)
        return [dict(row) for row in rows]


class DatabaseWrapper:
    def __init__(self, pg_pool=None):
        self.pg_pool = pg_pool

    def __getattr__(self, name):
        return CollectionWrapper(name, self.pg_pool)
