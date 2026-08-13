import io
import unittest
import zipfile

from app.main import (
    SPATIAL_RETENTION_DAYS,
    TIME_DOMAIN_RETENTION_DAYS,
    apply_wheel_compensation,
)
from app.parsers.archive import parse_archive_zip, validate_rms_intervals


class SpatialValidationTests(unittest.TestCase):
    def test_marks_250_mm_intervals_and_flags_gaps(self):
        records = [
            {"positionMm": 0},
            {"positionMm": 250},
            {"positionMm": 500},
            {"positionMm": 900},
        ]
        warnings = []

        summary = validate_rms_intervals(records, warnings)

        self.assertEqual(summary["validIntervals"], 2)
        self.assertEqual(summary["invalidIntervals"], 1)
        self.assertTrue(records[1]["spatialIntervalValid"])
        self.assertFalse(records[3]["spatialIntervalValid"])
        self.assertEqual(records[3]["spatialIntervalMm"], 400)
        self.assertEqual(len(warnings), 1)


class WheelCompensationTests(unittest.TestCase):
    def test_preserves_raw_values_and_applies_average_factor(self):
        rms = [{"positionMm": 1000, "speedKmph": 80.0}]
        peak = [
            {
                "windowStartMm": 0,
                "windowEndMm": 50000,
                "positionMm": 25000,
                "speedKmph": 80.0,
                "axes": {"al_x": {"peakPositionMm": 25000}},
            }
        ]

        summary = apply_wheel_compensation(
            rms,
            peak,
            {"leftWheelFactor": 1.02, "rightWheelFactor": 1.01, "version": 4},
        )

        self.assertEqual(summary["combinedFactor"], 1.015)
        self.assertEqual(rms[0]["rawPositionMm"], 1000)
        self.assertEqual(rms[0]["positionMm"], 1015)
        self.assertEqual(rms[0]["rawSpeedKmph"], 80.0)
        self.assertEqual(rms[0]["speedKmph"], 81.2)
        self.assertEqual(peak[0]["axes"]["al_x"]["peakPositionMm"], 25375)


class TimeDomainRetentionTests(unittest.TestCase):
    def test_reads_raw_files_from_nested_session_directory(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("SESSION_1/raw/adxl_left.bin", b"1234")

        parsed = parse_archive_zip(buffer.getvalue())

        self.assertEqual(len(parsed.raw_files), 1)
        self.assertEqual(parsed.raw_files[0]["path"], "raw/adxl_left.bin")
        self.assertEqual(parsed.raw_files[0]["sizeBytes"], 4)
        self.assertEqual(parsed.raw_files[0]["zip_member"], "SESSION_1/raw/adxl_left.bin")

    def test_retention_periods_match_requirement(self):
        self.assertEqual(SPATIAL_RETENTION_DAYS, 30)
        self.assertEqual(TIME_DOMAIN_RETENTION_DAYS, 7)


if __name__ == "__main__":
    unittest.main()
