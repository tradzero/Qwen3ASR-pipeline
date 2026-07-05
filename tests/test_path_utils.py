import unittest

from web_app.path_utils import normalize_input_path


class PathUtilsTests(unittest.TestCase):
    def test_file_uri_with_server_share_becomes_unc_path(self):
        self.assertEqual(
            normalize_input_path("file://server/share/a.mp4"),
            r"\\server\share\a.mp4",
        )

    def test_file_uri_with_drive_becomes_windows_path(self):
        self.assertEqual(
            normalize_input_path("file:///C:/media/a file.srt"),
            r"C:\media\a file.srt",
        )

    def test_quoted_path_is_unwrapped(self):
        self.assertEqual(
            normalize_input_path(r'"D:\media\a.mp4"'),
            r"D:\media\a.mp4",
        )


if __name__ == "__main__":
    unittest.main()
