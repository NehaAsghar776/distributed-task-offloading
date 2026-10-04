import unittest

from pydantic import ValidationError

from server.api import SettingsModel


class SettingsValidationTests(unittest.TestCase):
    def test_accepts_supported_render_settings(self) -> None:
        settings = SettingsModel(width=1920, height=1080, bitrate="8M", codec="h264_nvenc", preset="p5")
        self.assertEqual(settings.width, 1920)

    def test_rejects_invalid_dimensions_codec_and_bitrate(self) -> None:
        for values in (
            {"width": 0},
            {"codec": "../../unsafe"},
            {"bitrate": "1M; touch badfile"},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                SettingsModel(**values)


if __name__ == "__main__":
    unittest.main()