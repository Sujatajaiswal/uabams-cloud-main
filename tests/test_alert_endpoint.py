import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app.database import db
from app.routers.telemetry import create_alert


class TestAlertEndpoint(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "gatewayId": "GW1_PIL_BOGIE_01",
            "logicalGatewayId": "GW1_22151_BOGIE_01",
            "trainNo": "22151",
            "sessionName": "20260911_111245_22151_UP",
            "timestampUtcMs": 1789139875123,
            "startKm": 17.94366,
            "endKm": 17.99366,
            "speedKmph": 83.55,
            "minSpeedKmph": 83.18,
            "maxSpeedKmph": 83.88,
            "alertsCount": 3,
            "alerts": [
                {
                    "sensor": "AXLE_LEFT",
                    "axis": "X",
                    "channel": "AL_X",
                    "peakValueG": 9.0959,
                    "thresholdG": 8.0,
                    "speedKmph": 83.58,
                    "locationKm": 17.97748,
                    "latitude": 26.734633,
                    "longitude": 80.786575,
                },
                {
                    "sensor": "AXLE_LEFT",
                    "axis": "Y",
                    "channel": "AL_Y",
                    "peakValueG": 8.6275,
                    "thresholdG": 8.0,
                    "speedKmph": 83.50,
                    "locationKm": 17.98173,
                    "latitude": 26.734609,
                    "longitude": 80.786541,
                },
                {
                    "sensor": "AXLE_LEFT",
                    "axis": "Z",
                    "channel": "AL_Z",
                    "peakValueG": 43.5307,
                    "thresholdG": 10.0,
                    "speedKmph": 83.62,
                    "locationKm": 17.96548,
                    "latitude": 26.734700,
                    "longitude": 80.786669,
                },
            ],
        }

    async def _run_create_alert(self, body_dict, gateway_id="GW1_PIL_BOGIE_01"):
        mock_scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/alert",
            "headers": [(b"content-type", b"application/json")],
        }
        mock_request = Request(mock_scope)
        mock_request.state.gateway_id = gateway_id
        mock_request.state.train_id = "22151"

        body_bytes = json.dumps(body_dict).encode("utf-8")
        mock_request._body = body_bytes

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"id": 42})
        mock_pool.execute = AsyncMock()

        with patch.object(db, "pg_pool", mock_pool), \
             patch("app.routers.telemetry.mark_gateway_online", AsyncMock()):
            res = await create_alert(mock_request)
            return res, mock_pool

    def test_create_alert_success(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res, pool = loop.run_until_complete(self._run_create_alert(self.payload))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["windowAlertId"], 42)
            self.assertIn(res["alert"], ("RED", "YELLOW"))

            # Verify window_alerts INSERT was executed
            fetchrow_args = pool.fetchrow.call_args[0]
            self.assertIn("INSERT INTO window_alerts", fetchrow_args[0])
            self.assertEqual(fetchrow_args[1], "GW1_PIL_BOGIE_01")  # gateway_id
            self.assertEqual(fetchrow_args[2], "GW1_22151_BOGIE_01")  # logical_gateway_id
            self.assertEqual(fetchrow_args[3], "22151")  # train_no
            self.assertEqual(fetchrow_args[4], "20260911_111245_22151_UP")  # session_name
            self.assertEqual(fetchrow_args[5], 1789139875123)  # timestamp_utc_ms
            self.assertEqual(fetchrow_args[6], 17.94366)  # start_km
            self.assertEqual(fetchrow_args[7], 17.99366)  # end_km
            self.assertEqual(fetchrow_args[8], 83.55)  # speed_kmph
            self.assertEqual(fetchrow_args[9], 83.18)  # min_speed_kmph
            self.assertEqual(fetchrow_args[10], 83.88)  # max_speed_kmph
            self.assertEqual(fetchrow_args[11], 3)  # alerts_count

            # Verify 3 alert_events INSERTs were executed (one for each axis)
            alert_event_calls = [
                call for call in pool.execute.call_args_list
                if "INSERT INTO alert_events" in call[0][0]
            ]
            self.assertEqual(len(alert_event_calls), 3)

            # Check channels in the alert_events calls ($8 is channel)
            inserted_channels = [c[0][8] for c in alert_event_calls]
            self.assertEqual(inserted_channels, ["AL_X", "AL_Y", "AL_Z"])

            # Check centimeter chainage in position_mm ($11) and location_km ($12)
            self.assertEqual(alert_event_calls[0][0][11], 17977480)  # position_mm
            self.assertEqual(alert_event_calls[0][0][12], 17.97748)   # location_km

            # Check speeds and threshold ($17 peakValueG, $18 thresholdG, $19 speedKmph)
            self.assertEqual(alert_event_calls[0][0][17], 9.0959)    # peak_value_g
            self.assertEqual(alert_event_calls[0][0][18], 8.0)       # threshold_g
            self.assertEqual(alert_event_calls[0][0][19], 83.58)     # speed_kmph (per-axis peak)
            self.assertEqual(alert_event_calls[0][0][20], 83.55)     # window_speed_kmph
            self.assertEqual(alert_event_calls[0][0][21], 83.18)     # min_speed_kmph
            self.assertEqual(alert_event_calls[0][0][22], 83.88)     # max_speed_kmph
            self.assertEqual(alert_event_calls[0][0][23], 3)         # alerts_count
            self.assertEqual(alert_event_calls[0][0][24], 42)        # window_alert_id

        finally:
            loop.close()

    def test_create_alert_invalid_schema(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with self.assertRaises(HTTPException) as ctx:
                loop.run_until_complete(self._run_create_alert({"invalid": "data"}))
            self.assertEqual(ctx.exception.status_code, 422)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
