import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app


class TestAuthorLikesAndSearch(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key-author-likes'

    def test_author_search_empty_query(self):
        res = self.client.get('/api/authors/search?q=')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('authors'), [])

    def test_author_like_requires_login(self):
        # When user is not logged in, must return 401 with authenticated: False
        res = self.client.post('/api/author/999/like')
        self.assertEqual(res.status_code, 401)
        data = res.get_json()
        self.assertFalse(data.get('success'))
        self.assertFalse(data.get('authenticated'))
        self.assertIn('log in', data.get('message', '').lower())

    @patch('app.get_db_connection')
    def test_author_like_when_logged_in_toggles_like(self, mock_get_db):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_db
        mock_db.cursor.return_value = mock_cursor

        current_query = ['']
        def mock_execute(sql, *args, **kwargs):
            current_query[0] = str(sql)

        mock_cursor.execute.side_effect = mock_execute

        def mock_fetchone():
            q = current_query[0].lower()
            if 'front_page_settings' in q:
                return None
            if 'count(*)' in q:
                return {'cnt': 1}
            if 'author_likes' in q:
                return None
            return None

        mock_cursor.fetchone.side_effect = mock_fetchone

        with self.client.session_transaction() as sess:
            sess['user_id'] = 42
            sess['username'] = 'reader_john'
            sess['role'] = 'reader'

        res = self.client.post('/api/author/10/like')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertTrue(data.get('authenticated'))
        self.assertTrue(data.get('liked'))
        self.assertEqual(data.get('like_count'), 1)

    @patch('app.get_db_connection')
    def test_author_like_when_already_liked_unlikes(self, mock_get_db):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_db
        mock_db.cursor.return_value = mock_cursor

        current_query = ['']
        def mock_execute(sql, *args, **kwargs):
            current_query[0] = str(sql)

        mock_cursor.execute.side_effect = mock_execute

        def mock_fetchone():
            q = current_query[0].lower()
            if 'front_page_settings' in q:
                return None
            if 'count(*)' in q:
                return {'cnt': 0}
            if 'author_likes' in q:
                return {'id': 5}
            return None

        mock_cursor.fetchone.side_effect = mock_fetchone

        with self.client.session_transaction() as sess:
            sess['user_id'] = 42
            sess['username'] = 'reader_john'
            sess['role'] = 'reader'

        res = self.client.post('/api/author/10/like')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertTrue(data.get('authenticated'))
        self.assertFalse(data.get('liked'))
        self.assertEqual(data.get('like_count'), 0)


if __name__ == '__main__':
    unittest.main()
