import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import build_ai_learning_response


class TestAILearning(unittest.TestCase):

    def test_build_ai_learning_response_has_explanation_and_questions(self):
        result = build_ai_learning_response(
            book_title='Python Crash Course',
            book_description='A practical guide to learning Python programming with examples and exercises.',
            concept_query='loops',
            book_text='Loops help repeat actions, reduce duplication, and process collections of data.'
        )

        self.assertTrue(any(term in result['concept'].lower() for term in ['loop', 'loops']))
        self.assertIn('explanation', result)
        self.assertIn('practice_questions', result)
        self.assertIsInstance(result['practice_questions'], list)


if __name__ == '__main__':
    unittest.main()
