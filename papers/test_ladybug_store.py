"""Integration smoke test: synthetic data only, isolated temporary database."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ladybug_store import open_store


def rows(conn, query, parameters=None):
    result = conn.execute(query, parameters or {})
    try:
        return result.get_all()
    finally:
        result.close()


class LadybugSmokeTest(unittest.TestCase):
    def test_graph_search_and_reopen(self):
        with TemporaryDirectory(prefix="ladybug-smoke-") as directory:
            path = Path(directory) / "test.lbdb"
            with open_store(path) as conn:
                rows(conn, "CREATE NODE TABLE Item(id STRING PRIMARY KEY, text STRING, embedding FLOAT[3])")
                rows(conn, "CREATE REL TABLE Related(FROM Item TO Item)")
                for item_id, text, vector in [
                    ("a", "Graph database exploration", [1.0, 0.0, 0.0]),
                    ("b", "Vector similarity retrieval", [0.0, 1.0, 0.0]),
                    ("c", "Full text indexing", [0.0, 0.0, 1.0]),
                ]:
                    rows(conn, "CREATE (:Item {id: $id, text: $text, embedding: $vector})",
                         {"id": item_id, "text": text, "vector": vector})
                rows(conn, "MATCH (a:Item {id: 'a'}), (b:Item {id: 'b'}) CREATE (a)-[:Related]->(b)")
                rows(conn, "CALL CREATE_VECTOR_INDEX('Item', 'item_vector', 'embedding', metric := 'cosine')")
                rows(conn, "CALL CREATE_FTS_INDEX('Item', 'item_text', ['text'])")
            # Reopening verifies that graph records AND search indexes persist.
            with open_store(path) as conn:
                self.assertEqual(rows(conn, "MATCH (a:Item)-[:Related]->(b:Item) RETURN a.id, b.id"), [["a", "b"]])
                result = rows(conn, "CALL QUERY_VECTOR_INDEX('Item', 'item_vector', $v, 1) RETURN node.id, distance",
                              {"v": [1.0, 0.0, 0.0]})
                self.assertEqual(result[0][0], "a")
                self.assertAlmostEqual(result[0][1], 0.0, places=5)
                self.assertEqual(rows(conn, "CALL QUERY_FTS_INDEX('Item', 'item_text', 'exploration') RETURN node.id"), [["a"]])


if __name__ == "__main__":
    unittest.main()
