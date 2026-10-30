import unittest
from app import app
from unittest.mock import patch

class TestMaintenanceLockdown(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    @patch('app.get_front_page_settings')
    def test_regular_user_blocked_during_maintenance(self, mock_settings):
        # Simulate active maintenance mode
        mock_settings.return_value = {
            'maintenance_mode': 1,
            'maintenance_start': 'Immediate',
            'maintenance_end': 'Shortly',
            'maintenance_reason': 'Infrastructure upgrades',
            'maintenance_notified': 0
        }

        # Regular unauthenticated visitor visiting homepage
        resp = self.app.get('/')
        self.assertEqual(resp.status_code, 503)
        self.assertIn(b'Scheduled Maintenance', resp.data)

        # Regular user API request
        api_resp = self.app.get('/api/search_books?q=test')
        self.assertEqual(api_resp.status_code, 503)

    @patch('app.get_front_page_settings')
    def test_technical_leadership_bypasses_maintenance(self, mock_settings):
        mock_settings.return_value = {
            'maintenance_mode': 1,
            'maintenance_start': 'Immediate',
            'maintenance_end': 'Shortly',
            'maintenance_reason': 'Infrastructure upgrades',
            'maintenance_notified': 0
        }

        # Simulate developer logged in session
        with self.app.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'abhinavgiri45'
            sess['role'] = 'developer'

        resp = self.app.get('/')
        # Developer is NOT blocked by maintenance (status should be 200, not 503)
        self.assertNotEqual(resp.status_code, 503)

if __name__ == '__main__':
    unittest.main()
