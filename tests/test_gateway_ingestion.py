import struct
import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.models import AlertRequest, AxisAlertItem
from app.parsers.archive import (
    AXES,
    AXIS_RECORD_FORMAT,
    PEAK_RECORD_SIZE,
    WINDOW_HEADER_FORMAT,
    parse_channel_info,
    parse_peak_bytes,
    parse_record,
    peak_records_to_alert_events,
)


class TestGatewayIngestion(unittest.TestCase):
    def setUp(self):
        self.sample_payload = {
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

    def test_alert_request_schema_validation_success(self):
        req = AlertRequest(**self.sample_payload)
        self.assertEqual(req.gatewayId, "GW1_PIL_BOGIE_01")
        self.assertEqual(req.logicalGatewayId, "GW1_22151_BOGIE_01")
        self.assertEqual(req.trainNo, "22151")
        self.assertEqual(req.sessionName, "20260911_111245_22151_UP")
        self.assertEqual(req.timestampUtcMs, 1789139875123)
        self.assertAlmostEqual(req.startKm, 17.94366, places=5)
        self.assertAlmostEqual(req.endKm, 17.99366, places=5)
        self.assertAlmostEqual(req.speedKmph, 83.55, places=2)
        self.assertAlmostEqual(req.minSpeedKmph, 83.18, places=2)
        self.assertAlmostEqual(req.maxSpeedKmph, 83.88, places=2)
        self.assertEqual(req.alertsCount, 3)
        self.assertEqual(len(req.alerts), 3)

        # First alert item
        a0 = req.alerts[0]
        self.assertEqual(a0.sensor, "AXLE_LEFT")
        self.assertEqual(a0.axis, "X")
        self.assertEqual(a0.channel, "AL_X")
        self.assertAlmostEqual(a0.peakValueG, 9.0959, places=4)
        self.assertAlmostEqual(a0.thresholdG, 8.0, places=4)
        self.assertAlmostEqual(a0.speedKmph, 83.58, places=2)
        self.assertAlmostEqual(a0.locationKm, 17.97748, places=5)
        self.assertAlmostEqual(a0.latitude, 26.734633, places=6)
        self.assertAlmostEqual(a0.longitude, 80.786575, places=6)

    def test_alert_request_schema_validation_failure(self):
        # Missing required fields
        invalid_payload = {"gatewayId": "GW1"}
        with self.assertRaises(ValidationError):
            AlertRequest(**invalid_payload)

    def test_parse_channel_info(self):
        self.assertEqual(parse_channel_info("AL_X"), ("AXLE_LEFT", "X", "AL_X"))
        self.assertEqual(parse_channel_info("AR_Y"), ("AXLE_RIGHT", "Y", "AR_Y"))
        self.assertEqual(parse_channel_info("BG_Z"), ("BOGIE", "Z", "BG_Z"))
        self.assertEqual(parse_channel_info("al_z"), ("AXLE_LEFT", "Z", "AL_Z"))

    def _build_348_byte_record(
        self,
        start_mm=17943660,
        end_mm=17993660,
        avg_speed=83.55,
        min_speed=83.18,
        max_speed=83.88,
        alerts_count=3,
        alert_gen=1,
    ):
        header = struct.pack(
            WINDOW_HEADER_FORMAT,
            start_mm,
            end_mm,
            avg_speed,
            min_speed,
            max_speed,
            0x07,  # validMask
            alert_gen,
            alerts_count,
            0,  # reserved
        )
        self.assertEqual(len(header), 24)

        # 9 axis records: AL_X, AL_Y, AL_Z, AR_X, AR_Y, AR_Z, BG_X, BG_Y, BG_Z
        # Configure specific peaks for AL_X, AL_Y, AL_Z to exceed thresholds
        axes_bytes = bytearray()
        axis_values = {
            "AL_X": (9.0959, 17977480, 83.58, 10001, 26.734633, 80.786575),
            "AL_Y": (8.6275, 17981730, 83.50, 10002, 26.734609, 80.786541),
            "AL_Z": (43.5307, 17965480, 83.62, 10003, 26.734700, 80.786669),
            "AR_X": (1.2000, 17950000, 83.55, 10004, 26.734600, 80.786500),
            "AR_Y": (1.1500, 17951000, 83.55, 10005, 26.734610, 80.786510),
            "AR_Z": (2.5000, 17952000, 83.55, 10006, 26.734620, 80.786520),
            "BG_X": (0.8000, 17953000, 83.55, 10007, 26.734630, 80.786530),
            "BG_Y": (0.7500, 17954000, 83.55, 10008, 26.734640, 80.786540),
            "BG_Z": (1.0500, 17955000, 83.55, 10009, 26.734650, 80.786550),
        }

        for ax in AXES:
            peak_g, pos_mm, spd, m_cnt, lat, lon = axis_values[ax]
            ax_chunk = struct.pack(AXIS_RECORD_FORMAT, peak_g, pos_mm, spd, m_cnt, lat, lon)
            self.assertEqual(len(ax_chunk), 36)
            axes_bytes.extend(ax_chunk)

        total_bytes = header + bytes(axes_bytes)
        self.assertEqual(len(total_bytes), PEAK_RECORD_SIZE)
        return total_bytes

    def test_parse_record_348_bytes(self):
        chunk = self._build_348_byte_record()
        parsed = parse_record(chunk)

        # Centimeter accuracy: start_km and end_km rounded to 5 decimals
        self.assertEqual(parsed["startKm"], 17.94366)
        self.assertEqual(parsed["endKm"], 17.99366)
        self.assertEqual(parsed["windowStartMm"], 17943660)
        self.assertEqual(parsed["windowEndMm"], 17993660)

        # Speed metrics
        self.assertAlmostEqual(parsed["avgSpeedKmph"], 83.55, places=2)
        self.assertAlmostEqual(parsed["minSpeedKmph"], 83.18, places=2)
        self.assertAlmostEqual(parsed["maxSpeedKmph"], 83.88, places=2)
        self.assertAlmostEqual(parsed["speedKmph"], 83.55, places=2)

        # Alert flags
        self.assertTrue(parsed["alertGenerated"])
        self.assertEqual(parsed["alertsCount"], 3)
        self.assertEqual(parsed["validMask"], 0x07)

        # 9 axes data check
        axes = parsed["axes"]
        self.assertIn("AL_X", axes)
        self.assertIn("al_x", axes)
        al_x = axes["AL_X"]
        self.assertAlmostEqual(al_x["peakValueG"], 9.0959, places=4)
        self.assertEqual(al_x["positionMm"], 17977480)
        self.assertEqual(al_x["locationKm"], 17.97748)
        self.assertAlmostEqual(al_x["peakSpeedKmph"], 83.58, places=2)
        self.assertAlmostEqual(al_x["latitude"], 26.734633, places=6)
        self.assertAlmostEqual(al_x["longitude"], 80.786575, places=6)

        # Max peak axis
        self.assertEqual(parsed["maxPeakAxis"], "AL_Z")
        self.assertAlmostEqual(parsed["maxPeakG"], 43.5307, places=4)

    def test_parse_peak_bytes_multiple_records(self):
        chunk1 = self._build_348_byte_record(start_mm=0, end_mm=50000, alerts_count=1)
        chunk2 = self._build_348_byte_record(start_mm=50000, end_mm=100000, alerts_count=0, alert_gen=0)
        raw = chunk1 + chunk2

        warnings = []
        records = parse_peak_bytes(raw, warnings)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(warnings), 0)
        self.assertEqual(records[0]["recordIndex"], 0)
        self.assertEqual(records[0]["alertsCount"], 1)
        self.assertEqual(records[1]["recordIndex"], 1)
        self.assertEqual(records[1]["alertsCount"], 0)

    def test_multi_axis_alert_reporting(self):
        """
        When alertsCount = 3 in a 50m window, peak_records_to_alert_events
        must trigger an alert event for every axis exceeding its threshold,
        rather than discarding all except the single worst axis.
        """
        chunk = self._build_348_byte_record(alerts_count=3)
        records = parse_peak_bytes(chunk)
        self.assertEqual(len(records), 1)

        now = datetime.now(UTC)
        events = peak_records_to_alert_events(
            records,
            gateway_id="GW1_PIL_BOGIE_01",
            train_id="22151",
            session_name="20260911_111245_22151_UP",
            archive_sha256="testsha256",
            created_at=now,
        )

        # MUST generate 3 alert events (one for each of the 3 exceeded axes)
        self.assertEqual(len(events), 3)

        # Top 3 peaks sorted by peakValueG: AL_Z (43.5307), AL_X (9.0959), AL_Y (8.6275)
        channels = [e["channel"] for e in events]
        self.assertEqual(channels, ["AL_Z", "AL_X", "AL_Y"])

        # Check fields of the generated events
        e_z = events[0]
        self.assertEqual(e_z["channel"], "AL_Z")
        self.assertEqual(e_z["sensor"], "AXLE_LEFT")
        self.assertEqual(e_z["axis"], "Z")
        self.assertAlmostEqual(e_z["peakValueG"], 43.5307, places=4)
        self.assertAlmostEqual(e_z["speedKmph"], 83.62, places=2)
        self.assertEqual(e_z["locationKm"], 17.96548)
        self.assertEqual(e_z["startKm"], 17.94366)
        self.assertEqual(e_z["endKm"], 17.99366)
        self.assertAlmostEqual(e_z["windowAvgSpeedKmph"], 83.55, places=2)
        self.assertAlmostEqual(e_z["minSpeedKmph"], 83.18, places=2)
        self.assertAlmostEqual(e_z["maxSpeedKmph"], 83.88, places=2)
        self.assertEqual(e_z["alertsCount"], 3)
        self.assertEqual(e_z["source"], "peak_50m.bin")

        e_x = events[1]
        self.assertEqual(e_x["channel"], "AL_X")
        self.assertEqual(e_x["sensor"], "AXLE_LEFT")
        self.assertEqual(e_x["axis"], "X")
        self.assertAlmostEqual(e_x["peakValueG"], 9.0959, places=4)
        self.assertAlmostEqual(e_x["speedKmph"], 83.58, places=2)
        self.assertEqual(e_x["locationKm"], 17.97748)

        e_y = events[2]
        self.assertEqual(e_y["channel"], "AL_Y")
        self.assertEqual(e_y["sensor"], "AXLE_LEFT")
        self.assertEqual(e_y["axis"], "Y")
        self.assertAlmostEqual(e_y["peakValueG"], 8.6275, places=4)
        self.assertAlmostEqual(e_y["speedKmph"], 83.50, places=2)
        self.assertEqual(e_y["locationKm"], 17.98173)

    def test_single_axis_alert_reporting_when_alerts_count_zero(self):
        """
        When alertsCount = 0, peak_records_to_alert_events outputs
        the single max peak axis of the window.
        """
        chunk = self._build_348_byte_record(alerts_count=0, alert_gen=0)
        records = parse_peak_bytes(chunk)
        events = peak_records_to_alert_events(
            records,
            gateway_id="GW1_PIL_BOGIE_01",
            train_id="22151",
            session_name="20260911_111245_22151_UP",
            archive_sha256="testsha256",
            created_at=datetime.now(UTC),
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["channel"], "AL_Z")


if __name__ == "__main__":
    unittest.main()
