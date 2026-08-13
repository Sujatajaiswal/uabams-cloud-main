import unittest
from datetime import timedelta
from unittest.mock import patch

from starlette.requests import Request

from app import main
from app.db_wrapper import CollectionWrapper, translate_filter
from app.models import HeartbeatRequest


class FakeCursor:
    def __init__(self, commands):
        self.commands = commands
        self.limit_value = None

    def limit(self, value):
        self.limit_value = value
        return self

    async def to_list(self, length=None):
        eligible = [
            dict(command)
            for command in self.commands.values()
            if command.get("status") in ("pending", "delivered")
        ]
        eligible.sort(key=lambda item: item["createdAt"])
        return eligible[: self.limit_value or length]


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = documents or {}

    async def find_one(self, query, sort=None):
        if "commandId" in query:
            document = self.documents.get(query["commandId"])
            if document and all(document.get(key) == value for key, value in query.items()):
                return dict(document)
            return None
        if "gatewayId" in query:
            document = self.documents.get(query["gatewayId"])
            return dict(document) if document else None
        return None

    def find(self, query, projection=None, sort=None):
        return FakeCursor(self.documents)

    async def update_one(self, query, update, upsert=False):
        key = query.get("commandId") or query.get("gatewayId") or query.get("trainNo")
        document = self.documents.setdefault(key, {})
        document.update(update.get("$set", {}))

    async def insert_one(self, document):
        key = document.get("commandId") or document.get("gatewayId") or str(len(self.documents))
        self.documents[key] = dict(document)


class FakeDatabase:
    def __init__(self, gateway_id, command):
        self.gateways = FakeCollection({gateway_id: {
            "gatewayId": gateway_id,
            "gatewaySerial": "UABAMS_PIL_01",
            "trainId": "21304",
        }})
        self.gateway_commands = FakeCollection({command["commandId"]: command})
        self.heartbeat_logs = FakeCollection()
        self.gateway_status = FakeCollection()
        self.trains = FakeCollection()


def request_for(path="/api/v1/heartbeat"):
    return Request({
        "type": "http",
        "method": "POST",
        "scheme": "https",
        "server": ("cloud.example.com", 443),
        "path": path,
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
    })


class GatewayCommandFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivers_calibration_then_records_result_idempotently(self):
        gateway_id = "GW1_21304_BOGIE_02"
        now = main.utc_now()
        command_id = "cmd-1003"
        fake_db = FakeDatabase(gateway_id, {
            "commandId": command_id,
            "gatewayId": gateway_id,
            "type": "calibration_update",
            "status": "pending",
            "version": 25,
            "payloadUrl": f"/api/v1/calibration/{gateway_id}/payload/{command_id}",
            "sha256": "expected_sha256_hash",
            "deliveryCount": 0,
            "createdAt": now - timedelta(minutes=1),
        })
        token = main.create_gateway_token(gateway_id, "21304")
        heartbeat = HeartbeatRequest(
            gatewayId=gateway_id,
            gatewaySerial="UABAMS_PIL_01",
            timestamp=now,
            token=token,
            commandResults=[],
        )

        old_base_url = main.settings["cloud_public_base_url"]
        main.settings["cloud_public_base_url"] = "https://cloud.example.com"
        try:
            with patch.object(main, "db", fake_db):
                response = await main.heartbeat(heartbeat, request_for())
                self.assertEqual(response["commands"][0]["commandId"], command_id)
                self.assertEqual(
                    response["commands"][0]["payloadUrl"],
                    f"https://cloud.example.com/api/v1/calibration/{gateway_id}/payload/{command_id}",
                )
                self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "delivered")

                completed = HeartbeatRequest(
                    gatewayId=gateway_id,
                    gatewaySerial="UABAMS_PIL_01",
                    token=token,
                    commandResults=[{
                        "commandId": command_id,
                        "type": "calibration_update",
                        "status": "success",
                        "details": {"version": 25, "sha256Verified": True},
                    }],
                )
                response = await main.heartbeat(completed, request_for())
                self.assertEqual(response["commands"], [])
                self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "success")

                duplicate = completed.model_copy(deep=True)
                duplicate.commandResults[0].status = "failed"
                await main.heartbeat(duplicate, request_for())
                self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "success")
        finally:
            main.settings["cloud_public_base_url"] = old_base_url

    def test_rejects_invalid_command_result_status(self):
        with self.assertRaises(ValueError):
            HeartbeatRequest(
                gatewayId="GW1_21304_BOGIE_02",
                commandResults=[{
                    "commandId": "cmd-1",
                    "type": "reset",
                    "status": "running",
                }],
            )

    def test_canonical_payload_hash_is_stable(self):
        first = {"version": 25, "gatewayId": "GW1", "calibration": {"encoder": {"encoderPPR": 100}}}
        second = {"calibration": {"encoder": {"encoderPPR": 100}}, "gatewayId": "GW1", "version": 25}
        self.assertEqual(main.canonical_json_bytes(first), main.canonical_json_bytes(second))


class RecordingConnection:
    def __init__(self):
        self.sql = None
        self.args = None

    async def fetchval(self, sql, *args):
        self.sql = sql
        self.args = args
        return "cmd-1003"


class RecordingAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class RecordingPool:
    def __init__(self):
        self.connection = RecordingConnection()

    def acquire(self):
        return RecordingAcquire(self.connection)


class PostgreSQLCommandPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_command_insert_returns_command_id(self):
        pool = RecordingPool()
        collection = CollectionWrapper("gateway_commands", pool)
        result = await collection.insert_one({
            "commandId": "cmd-1003",
            "gatewayId": "GW1_21304_BOGIE_02",
            "type": "reset",
            "status": "pending",
            "deliveryCount": 0,
        })
        self.assertEqual(result.inserted_id, "cmd-1003")
        self.assertIn("RETURNING command_id", pool.connection.sql)
        self.assertNotIn("RETURNING id", pool.connection.sql)


class FilterTranslationTests(unittest.TestCase):
    def test_exists_false_does_not_become_true(self):
        where, params = translate_filter("archives", {"_id": {"$exists": False}})
        self.assertEqual(where, "id IS NULL")
        self.assertEqual(params, {})

    def test_nin_filters_null_and_zero(self):
        where, params = translate_filter("rms_records", {"latitude": {"$nin": [None, 0]}})
        self.assertIn("latitude NOT IN", where)
        self.assertIn("latitude IS NOT NULL", where)
        self.assertEqual(list(params.values()), [0])

    def test_unknown_operator_is_rejected(self):
        with self.assertRaises(ValueError):
            translate_filter("archives", {"createdAt": {"$regex": "2026"}})


if __name__ == "__main__":
    unittest.main()
