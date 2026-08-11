import unittest
from work_metadata import analyze_work_metadata

class WorkMetadataTests(unittest.TestCase):
    def test_russia_eligibility(self):
        for text in ("Russia", "Russian Federation", "Worldwide", "Anywhere"):
            self.assertEqual(analyze_work_metadata(text).russia_eligibility, "allowed")
        for text in ("US only", "Canada only", "must reside in Germany"):
            self.assertEqual(analyze_work_metadata(text).russia_eligibility, "blocked")
        self.assertEqual(analyze_work_metadata("", ["USA", "Canada", "UK"]).russia_eligibility, "blocked")
        self.assertEqual(analyze_work_metadata("", ["Germany", "Russia", "Poland"]).russia_eligibility, "allowed")
        for text in ("remote only", "portfolio only", "contract only", "English only", "company headquartered in USA", "US company"):
            self.assertEqual(analyze_work_metadata(text).russia_eligibility, "unknown")
        self.assertEqual(analyze_work_metadata("Our company is headquartered in USA").russia_eligibility, "unknown")

    def test_payment_methods(self):
        self.assertEqual(analyze_work_metadata("Payment in USDT").payment_method, "crypto_explicit")
        self.assertEqual(analyze_work_metadata("USDC or bank transfer").payment_method, "mixed")
        self.assertEqual(analyze_work_metadata("$100/hour").payment_method, "unknown")
        self.assertEqual(analyze_work_metadata("EUR salary").payment_method, "unknown")
