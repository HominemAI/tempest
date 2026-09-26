import random
from types import SimpleNamespace
import unittest
from passage_transforms import apply_edits, name_mapping, name_edits, verb_edits, plausible_person


class TransformTests(unittest.TestCase):
    def test_reject_non_proper_person_candidates(self):
        class Entity(list):
            label_ = 'PERSON'
        self.assertFalse(plausible_person(Entity([SimpleNamespace(text='Said', pos_='VERB')])))
        self.assertFalse(plausible_person(Entity([SimpleNamespace(text='bush', pos_='NOUN')])))
        self.assertTrue(plausible_person(Entity([SimpleNamespace(text='Alice', pos_='PROPN')])))

    def test_names_possessives_longest_match(self):
        mapping = name_mapping(['Alice Smith', 'Alice', 'Bob'])
        text = "Alice Smith met Bob. Alice's book was Bob’s. Malice stayed."
        edits = name_edits(text, mapping)
        output = apply_edits(text, edits)
        self.assertEqual(output, "person1 met person2. person1's book was person2’s. Malice stayed.")
        for edit in edits:
            self.assertEqual(output[edit['output_start']:edit['output_end']], edit['replacement'])

    def test_ambiguous_surname_and_honorifics(self):
        m = name_mapping(['John Smith', 'Jane Smith', 'Smith', 'Mr. Bennet', 'Mrs. Bennet'])
        self.assertEqual(len(set(m.values())), 5)

    def test_manual_alias(self):
        m = name_mapping(['Sherlock Holmes', 'Holmes'], {'Holmes': 'Sherlock Holmes'})
        self.assertEqual(m['Holmes'], m['Sherlock Holmes'])

    def test_verbs_inflection_seed_and_auxiliary(self):
        text = 'Alice walked and was singing.'
        def token(word, pos, tag, lemma, entity=''):
            return SimpleNamespace(text=word, pos_=pos, tag_=tag, lemma_=lemma,
                                   ent_type_=entity, idx=text.index(word))
        doc = [token('Alice', 'PROPN', 'NNP', 'Alice', 'PERSON'),
               token('walked', 'VERB', 'VBD', 'walk'), token('was', 'AUX', 'VBD', 'be'),
               token('singing', 'VERB', 'VBG', 'sing')]
        inflect = lambda lemma, tag: (lemma + {'VBD': 'ed', 'VBG': 'ing'}[tag],)
        a = verb_edits(doc, ['jump', 'walk'], random.Random(42), inflect)
        b = verb_edits(doc, ['jump', 'walk'], random.Random(42), inflect)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 2)
        self.assertIn('was', apply_edits(text, a))
        self.assertEqual(a[0]['replacement'], 'jumped')

    def test_overlap_rejected(self):
        with self.assertRaises(ValueError):
            apply_edits('abc', [dict(start=0, end=2, original='ab', replacement='x'),
                                dict(start=1, end=3, original='bc', replacement='y')])

    def test_contractions_and_copula_not_spliced(self):
        doc = [SimpleNamespace(text='’s', pos_='VERB', ent_type_='', lemma_='have'),
               SimpleNamespace(text='is', pos_='VERB', ent_type_='', lemma_='be')]
        self.assertEqual(verb_edits(doc, ['jump', 'sing'], random.Random(1), None), [])


if __name__ == '__main__':
    unittest.main()
