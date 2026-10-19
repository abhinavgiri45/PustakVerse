import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app


class TestDeleteAccount(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key-delete-account'
        app.config['WTF_CSRF_ENABLED'] = False

    def test_send_delete_account_otp_unauthenticated(self):
        res = self.client.post('/send_delete_account_otp', follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn('/login', res.headers.get('Location', ''))

    def test_send_delete_account_otp_developer_blocked(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'abhinavgiri45'
            sess['role'] = 'developer'

        res = self.client.post('/send_delete_account_otp', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        content = res.get_data(as_text=True)
        self.assertIn('Developer accounts cannot be deleted', content)

    def test_delete_my_account_developer_blocked(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'abhinavgiri45'
            sess['role'] = 'developer'
            sess['delete_account_otp'] = '123456'

        res = self.client.post('/delete_my_account', data={
            'otp': '123456',
            'password': 'anypassword',
            'security_retype': 'abhinavgiri45',
            'confirm_permanent_loss': 'on'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        content = res.get_data(as_text=True)
        self.assertIn('Developer accounts cannot be deleted', content)

    def test_delete_my_account_missing_checkbox(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 10
            sess['username'] = 'testuser'
            sess['role'] = 'reader'
            sess['delete_account_otp'] = '654321'

        res = self.client.post('/delete_my_account', data={
            'otp': '654321',
            'password': 'password123',
            'security_retype': 'testuser'
            # no confirm_permanent_loss checkbox
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        content = res.get_data(as_text=True)
        self.assertIn('explicitly confirm and accept that your whole data will get deleted', content)

    def test_delete_my_account_invalid_otp(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 10
            sess['username'] = 'testuser'
            sess['role'] = 'reader'
            sess['delete_account_otp'] = '654321'

        res = self.client.post('/delete_my_account', data={
            'otp': '000000',
            'password': 'password123',
            'security_retype': 'testuser',
            'confirm_permanent_loss': 'on'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        content = res.get_data(as_text=True)
        self.assertIn('Invalid or expired 6-digit deletion OTP', content)

    @patch('app.get_db_connection')
    def test_delete_my_account_invalid_password(self, mock_get_db):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_db
        mock_db.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = {
            'id': 10,
            'username': 'testuser',
            'email': 'testuser@example.com',
            'password_hash': generate_password_hash('correct_password'),
            'role': 'reader'
        }

        with self.client.session_transaction() as sess:
            sess['user_id'] = 10
            sess['username'] = 'testuser'
            sess['role'] = 'reader'
            sess['delete_account_otp'] = '654321'

        res = self.client.post('/delete_my_account', data={
            'otp': '654321',
            'password': 'wrong_password',
            'security_retype': 'testuser',
            'confirm_permanent_loss': 'on'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        content = res.get_data(as_text=True)
        self.assertIn('Incorrect account password', content)

    @patch('app.get_db_connection')
    def test_delete_my_account_invalid_security_retype(self, mock_get_db):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_db
        mock_db.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = {
            'id': 10,
            'username': 'testuser',
            'email': 'testuser@example.com',
            'password_hash': generate_password_hash('correct_password'),
            'role': 'reader'
        }

        with self.client.session_transaction() as sess:
            sess['user_id'] = 10
            sess['username'] = 'testuser'
            sess['role'] = 'reader'
            sess['delete_account_otp'] = '654321'

        res = self.client.post('/delete_my_account', data={
            'otp': '654321',
            'password': 'correct_password',
            'security_retype': 'wrong_username',
            'confirm_permanent_loss': 'on'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        content = res.get_data(as_text=True)
        self.assertIn('Security retype verification failed', content)

    @patch('app.log_official_activity')
    @patch('app.get_db_connection')
    def test_delete_my_account_success_all_factors_and_cascades(self, mock_get_db, mock_log_act):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_db
        mock_db.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = {
            'id': 10,
            'username': 'testuser',
            'email': 'testuser@example.com',
            'password_hash': generate_password_hash('correct_password'),
            'role': 'author'
        }
        mock_cursor.fetchall.return_value = [{'id': 101}, {'id': 102}]

        with self.client.session_transaction() as sess:
            sess['user_id'] = 10
            sess['username'] = 'testuser'
            sess['role'] = 'author'
            sess['delete_account_otp'] = '654321'

        res = self.client.post('/delete_my_account', data={
            'otp': '654321',
            'password': 'correct_password',
            'security_retype': 'testuser',
            'confirm_permanent_loss': 'on'
        }, follow_redirects=False)

        # Redirects to home page / index upon deletion
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers.get('Location'), '/')

        # Session cleared
        with self.client.session_transaction() as sess:
            self.assertNotIn('user_id', sess)
            self.assertNotIn('delete_account_otp', sess)

        # Verify DB delete calls occurred
        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        self.assertTrue(any("DELETE FROM users WHERE id =" in c for c in calls))
        self.assertTrue(any("DELETE FROM books WHERE id IN" in c for c in calls))


if __name__ == '__main__':
    unittest.main()
