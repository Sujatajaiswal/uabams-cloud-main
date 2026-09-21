import unittest
from datetime import timedelta
from unittest.mock import patch

from starlette.requests import Request

from app.routers import telemetry
from app import utils
from app.database import db, settings
from app.models import HeartbeatRequest


class FakePgPool:
    def __init__(self, gateway_id, command):
        self.gateways = {gateway_id: {
            "gatewaySerial": "UABAMS_PIL_01",
            "trainId": "21304",
        }}
        self.gateway_commands = {command["commandId"]: command}

    async def fetchrow(self, query, *args):
        if "FROM gateways" in query:
            gateway_id = args[0]
            return self.gateways.get(gateway_id)
        if "FROM gateway_commands" in query:
            command_id = args[0]
            return self.gateway_commands.get(command_id)
        return None

    async def fetch(self, query, *args):
        if "FROM gateway_commands" in query:
            gateway_id = args[0]
            eligible = [
                cmd for cmd in self.gateway_commands.values()
                if cmd.get("gatewayId") == gateway_id and cmd.get("status") in ("pending", "delivered")
            ]
            eligible.sort(key=lambda item: item["createdAt"], reverse=True)
            return eligible[:50]
        return []

    async def execute(self, query, *args):
        if "UPDATE gateway_commands" in query:
            if "status = $1, result = $2" in query:
                status, result_json, completed_at, command_id = args[0], args[1], args[2], args[3]
                if command_id in self.gateway_commands:
                    self.gateway_commands[command_id]["status"] = status
                    self.gateway_commands[command_id]["result"] = result_json
            elif "status = 'superseded'" in query:
                now, res_json, command_id = args[0], args[1], args[2]
                if command_id in self.gateway_commands:
                    self.gateway_commands[command_id]["status"] = "superseded"
            elif "status = 'delivered'" in query:
                now, delivery_count, command_id = args[0], args[1], args[2]
                if command_id in self.gateway_commands:
                    self.gateway_commands[command_id]["status"] = "delivered"
        pass


class FakeDatabase:
    def __init__(self, gateway_id, command):
        self.pg_pool = FakePgPool(gateway_id, command)
        
    @property
    def gateway_commands(self):
        # Compatibility property for tests checking fake_db.gateway_commands.documents
        class _Docs:
            def __init__(self, docs):
                self.documents = docs
        return _Docs(self.pg_pool.gateway_commands)


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
        now = utils.utc_now()
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
        token = utils.create_gateway_token(gateway_id)
        heartbeat = HeartbeatRequest(
            gatewayId=gateway_id,
            gatewaySerial="UABAMS_PIL_01",
            timestamp=now,
            token=token,
            commandResults=[],
        )

        old_base_url = settings["cloud_public_base_url"]
        settings["cloud_public_base_url"] = "https://cloud.example.com"
        try:
            with patch.object(telemetry, "db", fake_db):
                response = await telemetry.heartbeat(heartbeat, request_for())
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
                response = await telemetry.heartbeat(completed, request_for())
                self.assertEqual(response["commands"], [])
                self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "success")

                duplicate = completed.model_copy(deep=True)
                duplicate.commandResults[0].status = "failed"
                await telemetry.heartbeat(duplicate, request_for())
                self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "success")
        finally:
            settings["cloud_public_base_url"] = old_base_url

    async def test_calibration_state_progression(self):
        gateway_id = "GW1_21304_BOGIE_02"
        now = utils.utc_now()
        command_id = "cmd-cal-1"
        fake_db = FakeDatabase(gateway_id, {
            "commandId": command_id,
            "gatewayId": gateway_id,
            "type": "calibration_update",
            "status": "pending",
            "version": 14,
            "payloadUrl": f"/api/v1/calibration/{gateway_id}/payload/{command_id}",
            "sha256": "expected_sha256",
            "deliveryCount": 1,
            "createdAt": now - timedelta(minutes=5),
        })
        token = utils.create_gateway_token(gateway_id)
        
        # 1. Staged
        staged_req = HeartbeatRequest(
            gatewayId=gateway_id, token=token,
            commandResults=[{
                "commandId": command_id, "type": "calibration_update",
                "status": "staged", "reason": "TRAIN_IDLE_REQUIRED"
            }]
        )
        with patch.object(telemetry, "db", fake_db):
            await telemetry.heartbeat(staged_req, request_for())
            self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "staged")
            
        # 2. Partial Success
        partial_req = HeartbeatRequest(
            gatewayId=gateway_id, token=token,
            commandResults=[{
                "commandId": command_id, "type": "calibration_update",
                "status": "partial_success",
                "nodes": {
                    "adxlLeft": {"status": "COMMITTED", "committedVersion": 14, "targetVersion": 14, "valuesChanged": False},
                    "bogie": {"status": "FAILED", "committedVersion": 13, "targetVersion": 14, "valuesChanged": True, "error": "NODE_CALIBRATION_SYNC_FAILED"}
                }
            }]
        )
        with patch.object(telemetry, "db", fake_db):
            await telemetry.heartbeat(partial_req, request_for())
            self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "partial_success")
            
        # 3. Success
        success_req = HeartbeatRequest(
            gatewayId=gateway_id, token=token,
            commandResults=[{
                "commandId": command_id, "type": "calibration_update",
                "status": "success",
                "nodes": {
                    "adxlLeft": {"status": "COMMITTED", "committedVersion": 14, "targetVersion": 14, "valuesChanged": False},
                    "adxlRight": {"status": "COMMITTED", "committedVersion": 14, "targetVersion": 14, "valuesChanged": False},
                    "bogie": {"status": "COMMITTED", "committedVersion": 14, "targetVersion": 14, "valuesChanged": True},
                    "encoder": {"status": "COMMITTED", "committedVersion": 14, "targetVersion": 14, "valuesChanged": False}
                }
            }]
        )
        with patch.object(telemetry, "db", fake_db):
            await telemetry.heartbeat(success_req, request_for())
            self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "success")

    async def test_calibration_ignored_state(self):
        gateway_id = "GW1_21304_BOGIE_02"
        command_id = "cmd-cal-2"
        fake_db = FakeDatabase(gateway_id, {
            "commandId": command_id, "gatewayId": gateway_id,
            "type": "calibration_update", "status": "pending",
        })
        token = utils.create_gateway_token(gateway_id)
        
        ignored_req = HeartbeatRequest(
            gatewayId=gateway_id, token=token,
            commandResults=[{
                "commandId": command_id, "type": "calibration_update",
                "status": "ignored", "reason": "ALREADY_STAGED"
            }]
        )
        with patch.object(telemetry, "db", fake_db):
            await telemetry.heartbeat(ignored_req, request_for())
            self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "ignored")
            
            # verify terminal state prevents further updates
            later_req = HeartbeatRequest(
                gatewayId=gateway_id, token=token,
                commandResults=[{
                    "commandId": command_id, "type": "calibration_update",
                    "status": "success"
                }]
            )
            await telemetry.heartbeat(later_req, request_for())
            self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "ignored")

    async def test_calibration_failed_state(self):
        gateway_id = "GW1_21304_BOGIE_02"
        command_id = "cmd-cal-3"
        fake_db = FakeDatabase(gateway_id, {
            "commandId": command_id, "gatewayId": gateway_id,
            "type": "calibration_update", "status": "pending",
        })
        token = utils.create_gateway_token(gateway_id)
        
        failed_req = HeartbeatRequest(
            gatewayId=gateway_id, token=token,
            commandResults=[{
                "commandId": command_id, "type": "calibration_update",
                "status": "failed", "reason": "STALE_VERSION"
            }]
        )
        with patch.object(telemetry, "db", fake_db):
            await telemetry.heartbeat(failed_req, request_for())
            self.assertEqual(fake_db.gateway_commands.documents[command_id]["status"], "failed")

    def test_parses_calibration_result_schema_leniently(self):
        # Missing retryCount, presence of unknown extra field, details, and absence of nodes
        req = HeartbeatRequest(
            gatewayId="GW1",
            commandResults=[{
                "commandId": "cmd-123",
                "type": "calibration_update",
                "status": "success",
                "details": {"message": "done"},
                "unexpectedFutureField": "should be ignored"
            }]
        )
        self.assertEqual(req.commandResults[0].status, "success")
        self.assertEqual(req.commandResults[0].details, {"message": "done"})
        self.assertIsNone(req.commandResults[0].nodes)

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
        self.assertEqual(utils.canonical_json_bytes(first), utils.canonical_json_bytes(second))


class RecordingConnection:
    def __init__(self):
        self.sql = None
        self.args = None

    async def fetchval(self, sql, *args):
        self.sql = sql
        self.args = args
        return "cmd-1003"


if __name__ == "__main__":
    unittest.main()
