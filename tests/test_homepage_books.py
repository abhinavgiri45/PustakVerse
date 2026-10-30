import unittest
from app import app, get_canonical_starter_books

class TestHomepageBooks(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_canonical_starter_books_non_empty(self):
        starter_books = get_canonical_starter_books()
        self.assertIsInstance(starter_books, list)
        self.assertGreaterEqual(len(starter_books), 7)
        for b in starter_books:
            self.assertIn('id', b)
            self.assertIn('title', b)
            self.assertIn('catalog', b)
            self.assertIn('cover_image', b)

    def test_index_renders_book_cards_always(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode('utf-8')
        self.assertIn('book-card', html)
        self.assertIn('Ganita Manjari', html)
        self.assertIn('Exploration', html)
        self.assertIn('The Constitution of India', html)

    def test_category_and_archives_render_books(self):
        resp_edu = self.client.get('/category/Educational')
        self.assertEqual(resp_edu.status_code, 200)
        html_edu = resp_edu.data.decode('utf-8')
        self.assertIn('Ganita Manjari', html_edu)

        resp_arch = self.client.get('/archives')
        self.assertEqual(resp_arch.status_code, 200)
        html_arch = resp_arch.data.decode('utf-8')
        self.assertIn('The Constitution of India', html_arch)

if __name__ == '__main__':
    unittest.main()
