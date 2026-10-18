import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import (
    normalize_book_storage_url,
    normalize_screenshot_urls,
    verify_book_integrity
)


class TestAntiCorruptionAndStorage(unittest.TestCase):

    def test_normalize_google_drive_url(self):
        url = "https://drive.google.com/file/d/1A2B3C4D5E6F7G8H9I/view?usp=sharing"
        normalized = normalize_book_storage_url(url)
        self.assertEqual(normalized, "https://drive.google.com/file/d/1A2B3C4D5E6F7G8H9I/preview")

    def test_normalize_dropbox_url(self):
        url = "https://www.dropbox.com/s/sample123/my_book.pdf?dl=0"
        normalized = normalize_book_storage_url(url)
        self.assertIn("raw=1", normalized)
        self.assertNotIn("dl=0", normalized)

    def test_normalize_onedrive_url(self):
        url = "https://1drv.ms/b/s!Amx123xyz"
        normalized = normalize_book_storage_url(url)
        self.assertIn("download=1", normalized)

    def test_normalize_r2_and_s3_direct_url(self):
        r2_url = "https://pub-r2.example.com/books/quantum_physics.pdf"
        self.assertEqual(normalize_book_storage_url(r2_url), r2_url)
        s3_url = "https://my-bucket.s3.amazonaws.com/books/quantum_physics.epub"
        self.assertEqual(normalize_book_storage_url(s3_url), s3_url)

    def test_normalize_screenshot_urls_drive(self):
        raw_input = (
            "https://drive.google.com/file/d/1PhotoIdOne/view\n"
            "https://www.dropbox.com/s/photo2/page2.png?dl=0\n"
            "https://pub-r2.example.com/screenshots/page3.jpg"
        )
        screenshots_json = normalize_screenshot_urls(raw_input)
        screenshots = json.loads(screenshots_json)
        self.assertEqual(len(screenshots), 3)
        self.assertEqual(screenshots[0], "https://lh3.googleusercontent.com/d/1PhotoIdOne")
        self.assertIn("raw=1", screenshots[1])
        self.assertEqual(screenshots[2], "https://pub-r2.example.com/screenshots/page3.jpg")

    def test_verify_book_integrity_valid_pdf(self):
        valid_pdf_stream = b"%PDF-1.7\n%\xb5\xed\xae\xfb\n3 0 obj\n<< /Type /Catalog ... >>"
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.getcode.return_value = 200
        mock_response.headers = {"Content-Type": "application/pdf", "Content-Length": "204800"}
        mock_response.read.return_value = valid_pdf_stream[:2048]

        with patch("urllib.request.urlopen", return_value=mock_response):
            res = verify_book_integrity("https://pub-r2.example.com/books/sample.pdf", "pdf")
            self.assertTrue(res["valid"])
            self.assertEqual(res["detected_format"], "pdf")
            self.assertIn("Valid PDF", res["message"])

    def test_verify_book_integrity_restricted_google_signin(self):
        login_html = (
            b"<!DOCTYPE html><html lang=\"en\"><head><title>Sign in - Google Accounts</title></head>"
            b"<body><form action=\"https://accounts.google.com/signin\">Enter password</form></body></html>"
        )
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.getcode.return_value = 200
        mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_response.read.return_value = login_html

        with patch("urllib.request.urlopen", return_value=mock_response):
            res = verify_book_integrity("https://pub-r2.example.com/drive_export.pdf", "pdf")
            self.assertFalse(res["valid"])
            self.assertEqual(res["error_code"], "RESTRICTED_GOOGLE_SIGNIN_PAGE")
            self.assertIn("sign-in", res["message"].lower())

    def test_verify_book_integrity_corrupted_zero_byte(self):
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.getcode.return_value = 200
        mock_response.headers = {"Content-Length": "0"}
        mock_response.read.return_value = b""

        with patch("urllib.request.urlopen", return_value=mock_response):
            res = verify_book_integrity("https://example.com/empty.pdf", "pdf")
            self.assertFalse(res["valid"])
            self.assertEqual(res["error_code"], "EMPTY_FILE")

    def test_verify_book_integrity_http_404(self):
        http_error = urllib.error.HTTPError(
            url="https://example.com/missing.pdf",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None
        )
        with patch("urllib.request.urlopen", side_effect=http_error):
            res = verify_book_integrity("https://example.com/missing.pdf", "pdf")
            self.assertFalse(res["valid"])
            self.assertEqual(res["error_code"], "HTTP_NOT_FOUND")


if __name__ == '__main__':
    unittest.main()
