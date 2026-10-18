import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import build_ai_learning_response


def test_build_ai_learning_response_has_explanation_and_questions():
    result = build_ai_learning_response(
        book_title='Python Crash Course',
        book_description='A practical guide to learning Python programming with examples and exercises.',
        concept_query='loops',
        book_text='Loops help repeat actions, reduce duplication, and process collections of data.'
    )

    assert any(term in result['concept'].lower() for term in ['loop', 'loops'])
    assert 'explanation' in result
    assert 'practice_questions' in result
    assert isinstance(result['practice_questions'], list)
