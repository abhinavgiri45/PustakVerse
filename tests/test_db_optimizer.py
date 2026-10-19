import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app, prune_database_storage


class TestDatabaseOptimizer(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key-db-optimizer'

    def test_optimize_database_requires_developer(self):
        # Unauthenticated request
        res = self.client.post('/api/developer/optimize_database')
        self.assertEqual(res.status_code, 403)
        data = res.get_json()
        self.assertFalse(data.get('success'))
        self.assertIn('developer', data.get('message', '').lower())

    def test_optimize_database_reader_role_forbidden(self):
        # Authenticated as normal reader
        with self.client.session_transaction() as sess:
            sess['user_id'] = 100
            sess['username'] = 'normal_reader'
            sess['role'] = 'reader'

        res = self.client.post('/api/developer/optimize_database')
        self.assertEqual(res.status_code, 403)
        data = res.get_json()
        self.assertFalse(data.get('success'))

    @patch('app.prune_database_storage')
    @patch('app.log_official_activity')
    def test_optimize_database_developer_success(self, mock_log, mock_prune):
        mock_prune.return_value = {
            'logs_pruned': 15,
            'requests_pruned': 4,
            'orphans_removed': 2,
            'chat_pruned': 10,
            'lockouts_reset': 1,
            'vacuum_executed': True
        }

        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'master_dev'
            sess['role'] = 'developer'

        res = self.client.post('/api/developer/optimize_database')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertEqual(data['details']['logs_pruned'], 15)
        self.assertEqual(data['details']['requests_pruned'], 4)
        self.assertEqual(data['details']['orphans_removed'], 2)
        self.assertEqual(data['details']['chat_pruned'], 10)
        self.assertEqual(data['details']['lockouts_reset'], 1)
        self.assertTrue(data['details']['vacuum_executed'])

    @patch('app.get_db_connection')
    def test_prune_database_storage_execution(self, mock_get_db):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_db
        mock_db.cursor.return_value = mock_cursor
        mock_cursor.rowcount = 3

        stats = prune_database_storage()
        self.assertIsInstance(stats, dict)
        self.assertIn('logs_pruned', stats)
        self.assertIn('requests_pruned', stats)
        self.assertIn('orphans_removed', stats)
        self.assertIn('chat_pruned', stats)
        self.assertIn('lockouts_reset', stats)
        self.assertTrue(stats.get('vacuum_executed'))
        self.assertTrue(mock_db.commit.called)


if __name__ == '__main__':
    unittest.main()
