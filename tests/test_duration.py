import unittest

from takt.domain.duration import Duration


class DurationTests(unittest.TestCase):
    def test_formats_stopwatch_and_added_time(self) -> None:
        duration = Duration(83_459)
        self.assertEqual(duration.format_stopwatch(), "01:23.45")
        self.assertEqual(duration.format_added(), "+01:23.45")

    def test_formats_more_than_an_hour_without_wrapping_minutes(self) -> None:
        self.assertEqual(Duration(3_661_000).format_stopwatch(), "61:01.00")

    def test_rejects_negative_values(self) -> None:
        with self.assertRaises(ValueError):
            Duration(-1)

    def test_addition_preserves_milliseconds(self) -> None:
        self.assertEqual(Duration(5_001) + Duration(10_002), Duration(15_003))


if __name__ == "__main__":
    unittest.main()

