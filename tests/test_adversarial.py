import copy
import csv
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from crm_quality import area, read_records, analyze, write_reports, summary, FIELDS
from test_quality import record

def cli(source, output):
    return subprocess.run([sys.executable, str(ROOT/'crm_quality.py'), str(source), str(ROOT/'sample_reference.csv'), str(output)], capture_output=True, text=True)

def csv_text(rows):
    out=io.StringIO(newline='')
    writer=csv.DictWriter(out,fieldnames=FIELDS)
    writer.writeheader();writer.writerows(rows)
    return out.getvalue()

class Adversarial(unittest.TestCase):
    def test_01_decimal_precision(self):
        for value,expected in [('00000.0000','0'),('0,000.000','0'),('9999999999999999999999999999.123450000','9999999999999999999999999999.12345'),('  1,000.0100  ','1000.01')]:
            with self.subTest(value=value):self.assertEqual(area(value),expected)

    def test_02_reject_numeric_grammar(self):
        for value in ('1,00,000','1,000,','.5','1.','+1','-0','1 000','1_000','1e3'):
            with self.subTest(value=value), self.assertRaises(ValueError):area(value)

    def test_03_multiline_quotes_preserved(self):
        row=record(owner='OWNER, "A"',address='FIRST\r\nSECOND',building_sqft='1,000.00')
        for terminal in (True,False):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'input.csv'; data=csv_text([row]);data=data if terminal else data.removesuffix('\r\n');p.write_bytes(data.encode())
                rows=read_records(p);self.assertEqual(rows,[row]);before=copy.deepcopy(rows);analyze(rows,[]);self.assertEqual(rows,before)

    def test_04_trimmed_duplicate_ids_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'input.csv';p.write_bytes(csv_text([record(' P001 '),record('P001')]).encode());out=Path(tmp)/'out';result=cli(p,out)
            self.assertEqual(result.returncode,2);self.assertNotIn('Traceback',result.stderr);self.assertIn('repeated',result.stderr);self.assertFalse(out.exists())

    def test_05_state_normalization(self):
        issues=analyze([record(state=' tx ')],[record('R',state='TX',owner='REFERENCE')])
        changes=[x for x in issues if x['category']=='reference_difference'];self.assertEqual([(x['field'],x['proposed']) for x in changes],[('owner','REFERENCE')])
        self.assertEqual(changes[0]['reference_id'],'R')
        self.assertEqual([x['proposed'] for x in issues if x['field']=='state' and x['category']=='normalization'],['TX'])
        self.assertFalse(any(x['field']=='state' and 'validation' in x['category'] for x in issues))

    def test_06_invalid_reference_fields_suppressed(self):
        issues=analyze([record()],[record('R',postal_code='bad',building_sqft='NaN',owner='REFERENCE')])
        self.assertEqual({x['field'] for x in issues if x['category']=='reference_validation'},{'postal_code','building_sqft'})
        self.assertEqual([(x['field'],x['proposed']) for x in issues if x['category']=='reference_difference'],[('owner','REFERENCE')])

    def test_07_three_unique_pairs(self):
        issues=analyze([record('P1'),record('P2'),record('P3')],[])
        pairs=[frozenset(x['record_id'].split(' | ')) for x in issues if x['category']=='exact_duplicate_candidate']
        self.assertEqual(len(pairs),3)
        self.assertEqual(set(pairs),{frozenset(('P1','P2')),frozenset(('P1','P3')),frozenset(('P2','P3'))})
        self.assertEqual(summary([record('P1'),record('P2'),record('P3')],[],issues)['exact_duplicate_pairs'],3)
        self.assertFalse(any(x['category']=='ambiguous_duplicate_candidate' for x in issues))

    def test_08_missing_county(self):
        issues=analyze([record('P1',county=''),record('P2',county='')],[record('R',county='',owner='OTHER')])
        self.assertFalse(any('duplicate' in x['category'] or x['category']=='reference_difference' for x in issues))
        self.assertEqual({x['record_id'] for x in issues if x['category']=='missing_value' and x['field']=='county'},{'P1','P2'})
        self.assertEqual({x['record_id'] for x in issues if x['category']=='no_reference'},{'P1','P2'})

    def test_09a_existing_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'out';out.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):write_reports(out,[record()],[],[])
            self.assertEqual(out.read_bytes(),b'keep')

    def test_09b_dangling_directory_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'out';target=Path(tmp)/'missing'
            try:out.symlink_to(target,target_is_directory=True)
            except OSError as exc:self.skipTest('Directory symlink creation unsupported or not permitted: '+str(exc.winerror if hasattr(exc,'winerror') else exc.errno))
            original_target=out.readlink()
            with self.assertRaises(FileExistsError):write_reports(out,[record()],[],[])
            self.assertTrue(out.is_symlink());self.assertEqual(out.readlink(),original_target);self.assertFalse(target.exists())

    def test_10_third_move_failure_retry(self):
        original=Path.rename
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);sentinel=base/'sentinel';sentinel.write_bytes(b'keep');out=base/'out';calls=[]
            def fail(path,target):
                calls.append(path.name)
                if path.name=='review.html':raise OSError('injected third move failure')
                return original(path,target)
            with patch.object(Path,'rename',fail),self.assertRaises(OSError):write_reports(out,[record()],[],[])
            self.assertEqual(calls,['review.csv','summary.csv','review.html']);self.assertFalse(out.exists());self.assertEqual(sentinel.read_bytes(),b'keep');self.assertEqual(list(base.iterdir()),[sentinel])
            write_reports(out,[record()],[],[]);expected=base/'expected';write_reports(expected,[record()],[],[])
            self.assertEqual({p.name:p.read_bytes() for p in out.iterdir()},{p.name:p.read_bytes() for p in expected.iterdir()});self.assertEqual(len(list(out.iterdir())),3)

    def test_11_absent_parent_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)/'missing';out=parent/'out';result=cli(ROOT/'sample_properties.csv',out)
            self.assertEqual(result.returncode,2);self.assertNotIn('Traceback',result.stderr);self.assertFalse(parent.exists())

    def test_12_invalid_utf8_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'input.csv';p.write_bytes(b'\xff');out=Path(tmp)/'out';result=cli(p,out)
            self.assertEqual(result.returncode,2);self.assertNotIn('Traceback',result.stderr);self.assertFalse(out.exists())

if __name__=='__main__':unittest.main(verbosity=2)
