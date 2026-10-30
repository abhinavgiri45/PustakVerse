import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app

class TestUsernameChange(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False

    def test_check_username_invalid_chars(self):
        res = self.client.get('/api/check_username?u=invalid space!')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data.get('available'))

    def test_check_username_too_short(self):
        res = self.client.get('/api/check_username?u=ab')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data.get('available'))

    def test_change_username_requires_auth(self):
        res = self.client.post('/change_username', data={'new_username': 'new_reader'})
        self.assertEqual(res.status_code, 302)
        self.assertIn('/login', res.headers.get('Location', ''))

    def test_change_username_requires_password(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 999
            sess['username'] = 'test_reader'
            sess['role'] = 'reader'

        res = self.client.post('/change_username', data={'new_username': 'valid_name'}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        content = res.get_data(as_text=True)
        self.assertIn('password', content.lower())

if __name__ == '__main__':
    unittest.main()
