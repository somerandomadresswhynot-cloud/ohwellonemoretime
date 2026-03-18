import tempfile
import unittest

from study_app.persistence.database import Database
from study_app.persistence.repositories import SourceRepo


class SourceLearningModeTests(unittest.TestCase):
    def test_default_learning_mode_is_any(self):
        with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
            db = Database(tmp.name)
            self.addCleanup(db.close)
            repo = SourceRepo(db)
            source_id = repo.create('Book', '/tmp/book.pdf', 1, 10)
            source = repo.get(source_id)
            self.assertEqual(source.learning_mode, 'any')

    def test_update_metadata_persists_learning_mode(self):
        with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
            db = Database(tmp.name)
            self.addCleanup(db.close)
            repo = SourceRepo(db)
            source_id = repo.create('Book', '/tmp/book.pdf', 1, 10)
            repo.update_metadata(source_id, 'Book', True, 'strict')
            source = repo.get(source_id)
            self.assertEqual(source.learning_mode, 'strict')


if __name__ == '__main__':
    unittest.main()
