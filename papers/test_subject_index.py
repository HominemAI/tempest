import json
import sqlite3
import unittest
import tempfile
from argparse import Namespace
from unittest.mock import patch
from pathlib import Path
from subject_index import build, extract, paragraphs, patterns, safe_source
from annotations import AnnotationStore

TERM = dict(id='dopamine',name='Dopamine',category='Neurotransmitter',origin='endogenous',aliases=['dopamine','DA'],related_terms=['dopaminergic'])

class ExtractionTests(unittest.TestCase):
    def scan(self,text,terms=None,radius=3):
        return list(extract({'id':'test','text':text},patterns(terms or [TERM]),radius))

    def test_lines_hyphenation_and_context(self):
        text='# Title\n\n## Page 1\n\nBefore.\n\nDopa-\nmine matters.\n\nAfter.\n'
        row=self.scan(text)[0]
        self.assertEqual((row['line'],row['end_line'],row['page']),(7,8,1))
        self.assertEqual(row['before'][0]['text'],'Before.')
        self.assertEqual(row['after'][0]['text'],'After.')

    def test_boundaries_acronyms_related(self):
        rows=self.scan('da DA dopaminergic pseudodopamine dopamine.')
        self.assertEqual([r['matched_text'] for r in rows],['DA','dopaminergic','dopamine'])
        self.assertEqual(rows[1]['match_kind'],'related term')

    def test_no_duplicate_name_alias(self):
        self.assertEqual(len(self.scan('Dopamine dopamine')),2)

    def test_longest_overlapping(self):
        a=dict(id='endorphin',name='endorphin',category='peptide')
        b=dict(id='beta',name='beta-endorphin',category='peptide')
        self.assertEqual(self.scan('beta-endorphin',[a,b])[0]['term_id'],'beta')

    def test_context_radius(self):
        rows=self.scan('\n\n'.join(['a','b','c','d','dopamine','e','f','g','h']))
        self.assertEqual([p['text'] for p in rows[0]['before']],['b','c','d'])
        self.assertEqual([p['text'] for p in rows[0]['after']],['e','f','g'])

    def test_path_traversal(self):
        with self.assertRaises(ValueError):safe_source(Path('/tmp/corpus'),'../secret.md')

    def test_generic_plain_markdown(self):
        row=self.scan('# Test\n\nDopamine is mentioned.')[0]
        self.assertEqual(row['line'],3)
        self.assertIsNone(row['page'])

    def test_curated_labels_cover_catalog(self):
        root=Path(__file__).parent
        docs=json.loads((root/'catalog.json').read_text())['papers']
        labels=json.loads((root/'document-labels.json').read_text())['documents']
        self.assertEqual({p['markdown'] for p in docs},set(labels))
        self.assertTrue(all(len(value['labels'])>=2 for value in labels.values()))

    def test_reuse_for_nonchemical_subject(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'paper.md').write_text('# A paper\n\nQuantum entanglement is a topic.\n')
            (root/'catalog.json').write_text(json.dumps({'papers':[dict(markdown='paper.md',title='Quantum paper',area='physics',type='Review; Journal note')]}))
            (root/'terms.json').write_text(json.dumps({'terms':[dict(id='entanglement',name='entanglement',category='Quantum concept')]}))
            (root/'labels.json').write_text(json.dumps({'documents':{'paper.md':{'labels':['physics','quantum-information']}}}))
            args=Namespace(corpus=root,output=root/'output',catalog=root/'catalog.json',vocabulary=root/'terms.json',labels=root/'labels.json',context=3,cache=root/'cache',title='Physics')
            with patch('subject_index.embedder',return_value=object()),patch('subject_index.vectorize',return_value=iter([bytes(384*4)])):
                build(args)
            with sqlite3.connect(root/'output/subject-index.sqlite') as db:
                self.assertEqual(db.execute('SELECT term,category,type,path FROM reference_index').fetchone(),('entanglement','Quantum concept','Review','paper.md'))
                self.assertEqual(db.execute('SELECT count(*) FROM document_labels').fetchone()[0],2)
            html=(root/'output/explorer.html').read_text()
            self.assertIn('../paper.md',html)
            self.assertIn('quantum-information',html)

class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        with sqlite3.connect(self.root/'subject-index.sqlite') as db:
            db.execute('CREATE TABLE documents(id TEXT,path TEXT,sha256 TEXT,text TEXT)')
            db.execute('INSERT INTO documents VALUES (?,?,?,?)',('doc','paper.md','hash','First\nHello world.'))
        self.store=AnnotationStore(self.root)
        self.note=dict(id='note-1',document_id='doc',markdown_path='paper.md',source_sha256='hash',text='Hello',comment='A note',segments=[dict(passage_start_line=2,rendered_start=0,rendered_end=5,source_start_offset=6,source_end_offset=11,source_start=dict(line=2,column=0),source_end=dict(line=2,column=5),source_text='Hello')])

    def tearDown(self):self.temp.cleanup()

    def test_save_update_restart(self):
        self.store.save(self.note);self.note['comment']='Updated';self.store.save(self.note)
        records=AnnotationStore(self.root).list()
        self.assertEqual(len(records),1);self.assertEqual(records[0]['comment'],'Updated')

    def test_reject_changed_document(self):
        self.note['source_sha256']='changed'
        with self.assertRaises(ValueError):self.store.save(self.note)

    def test_reject_incorrect_source_range(self):
        self.note['segments'][0]['source_text']='Goodbye'
        with self.assertRaises(ValueError):self.store.save(self.note)

    def test_index_rebuild_does_not_remove_notes(self):
        self.store.save(self.note)
        with sqlite3.connect(self.root/'subject-index.sqlite') as db:db.execute('DELETE FROM documents')
        self.assertEqual(len(self.store.list()),1)

if __name__=='__main__':unittest.main()
