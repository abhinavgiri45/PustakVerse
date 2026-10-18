import unittest
import os
import re
from app import app

class TestPaymentsAndInvoices(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_csp_allows_razorpay_and_pdf_tools(self):
        """Verify CSP headers include Razorpay and PDF CDN sources."""
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)
        csp = response.headers.get('Content-Security-Policy', '')
        self.assertIn('https://checkout.razorpay.com', csp)
        self.assertIn('https://*.razorpay.com', csp)
        self.assertIn('https://cdnjs.cloudflare.com', csp)

    def test_receipt_download_redirects_to_pdf_invoice(self):
        """Receipt download endpoint should redirect to /invoice/<id>?download=pdf."""
        from unittest.mock import MagicMock, patch
        with self.app.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'testuser'
            sess['role'] = 'reader'

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_db.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {
            'id': 1, 'user_id': 1, 'book_id': 10, 'razorpay_order_id': 'pv-test-order-123',
            'amount_paise': 10000, 'donation_paise': 0, 'fee_paise': 236, 'author_earning_paise': 9764,
            'status': 'paid', 'paid_at': '2026-10-18T10:00:00'
        }

        with patch('app.get_db_connection', return_value=mock_db):
            response = self.app.get('/api/receipt/download/pv-test-order-123')
            self.assertEqual(response.status_code, 302)
            self.assertIn('download=pdf', response.location)
            self.assertIn('/invoice/pv-test-order-123', response.location)

    def test_invoice_template_has_pdf_converter_and_button(self):
        """Invoice template must contain html2pdf library and PDF download button."""
        tmpl_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'invoice.html')
        with open(tmpl_path, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('html2pdf.bundle.min.js', content)
        self.assertIn('id="taxInvoiceCard"', content)
        self.assertIn('downloadInvoicePdf()', content)
        self.assertIn('Download Tax Invoice (.pdf)', content)
        self.assertNotIn('Download Receipt (.html)', content)

    def test_checkout_template_has_no_fake_bypasses(self):
        """Checkout template must not have fake sandbox instant unlock bypasses."""
        tmpl_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'checkout.html')
        with open(tmpl_path, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertNotIn('pay_instant_', content)
        self.assertNotIn('sig_sandbox_test_verified', content)
        self.assertNotIn('openGatewayModal', content)
        self.assertNotIn('proceedInstantUnlock', content)
        self.assertIn('loadRazorpaySdk', content)

    def test_worker_has_no_fake_bypasses_in_checkout(self):
        """_worker.js must not have fake sandbox instant unlock bypasses in checkout."""
        worker_path = os.path.join(os.path.dirname(__file__), '..', '_worker.js')
        with open(worker_path, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertNotIn('openGatewayModal', content)
        self.assertNotIn('proceedInstantUnlock', content)
        self.assertIn('loadRazorpaySdk', content)
        self.assertIn('downloadInvoicePdf()', content)
        self.assertIn('Download Tax Invoice (.pdf)', content)

if __name__ == '__main__':
    unittest.main()
