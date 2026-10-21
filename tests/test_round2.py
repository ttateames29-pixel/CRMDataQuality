import csv
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from crm_quality import FIELDS, analyze, read_records, write_reports
from test_quality import record

ROOT = Path(__file__).resolve().parents[1]

def encoded(rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()

class RoundTwoTests(unittest.TestCase):
    def test_cross_state_identity(self):
        issues = analyze([record(), record('P2', state='CA')], [record('R1', state='CA', owner='OTHER')])
        self.assertFalse(any('duplicate' in x['category'] for x in issues))
        changes = [x for x in issues if x['category'] == 'reference_difference']
        self.assertEqual([(x['record_id'], x['field']) for x in changes], [('P2', 'owner')])

    def test_invalid_or_missing_state_disables_identity(self):
        for state in ('', 'ZZ', 'Texas', '1A'):
            with self.subTest(state=state):
                issues = analyze([record(state=state), record('P2', state=state)], [record('R1', state=state, owner='OTHER')])
                self.assertFalse(any('duplicate' in x['category'] or x['category'] == 'reference_difference' for x in issues))
                self.assertTrue(any(x['field'] == 'state' and x['category'] == 'validation' for x in issues))

    def test_invalid_postal_not_used_for_address_or_proposal(self):
        issues = analyze([record(parcel_id='', postal_code='bad'), record('P2', parcel_id='', postal_code='bad')], [])
        self.assertFalse(any('duplicate' in x['category'] for x in issues))
        issues = analyze([record()], [record('R', postal_code='bad')])
        self.assertFalse(any(x['category'] == 'reference_difference' and x['field'] == 'postal_code' for x in issues))

    def test_cli_rejects_malformed_inputs_without_output(self):
        header = ','.join(FIELDS) + '\n'
        valid = ','.join(record().values())
        cases = {'unterminated': header+'"broken', 'trailing_quote':header+'"P1"x,'+','.join(list(record().values())[1:]),
                 'bare_quote':header+valid.replace('P001','P"001'), 'oversize':header+valid.replace('P001','X'*200000),
                 'short':header+'P1,CEDAR', 'long':header+valid+',extra', 'blank_id':header+valid.replace('P001',''),
                 'header_only':header, 'duplicate_id':header+valid+'\n'+valid}
        for name, text in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp); src=base/'bad.csv'; src.write_text(text,encoding='utf-8'); out=base/'output'
                run=subprocess.run([sys.executable,str(ROOT/'crm_quality.py'),str(src),str(ROOT/'sample_reference.csv'),str(out)],capture_output=True,text=True)
                self.assertEqual(run.returncode,2)
                self.assertIn('CSV line',run.stderr)
                self.assertNotIn('Traceback',run.stderr)
                self.assertFalse(out.exists())

    def test_bom_crlf_multiline_and_blank_optional_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'input.csv'
            row=record(owner='',building_sqft='',address='100 "EXAMPLE"\nPLAZA')
            path.write_bytes(b'\xef\xbb\xbf'+encoded([row]).encode())
            self.assertEqual(read_records(path),[row])

    def test_write_failures_leave_no_output_and_allow_retry(self):
        original=Path.open
        for fail_name in ('summary.csv','review.html'):
            with self.subTest(file=fail_name), tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)/'report'
                def fail(path,*args,**kwargs):
                    if path.name == fail_name: raise OSError('simulated write failure')
                    return original(path,*args,**kwargs)
                with patch.object(Path,'open',fail), self.assertRaises(OSError):
                    write_reports(out,[record()],[],[])
                self.assertFalse(out.exists())
                self.assertEqual(list(Path(tmp).iterdir()),[])
                write_reports(out,[record()],[],[])
                self.assertEqual(len(list(out.iterdir())),3)

    def test_publish_move_failure_rolls_back(self):
        original=Path.rename
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'report'
            def fail(path,target):
                if path.name=='summary.csv': raise OSError('simulated move failure')
                return original(path,target)
            with patch.object(Path,'rename',fail), self.assertRaises(OSError):
                write_reports(out,[record()],[],[])
            self.assertFalse(out.exists())

    def test_all_formula_prefixes_in_csv_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows=[record(str(i),owner=value) for i,value in enumerate(('=1+1','+SUM(A1)','-1','@A1',' \t=1'))]
            out=Path(tmp)/'report'; write_reports(out,rows,[record('R')],analyze(rows,[record('R')]))
            with (out/'review.csv').open(newline='',encoding='utf-8') as f:
                values=[x['current'] for x in csv.DictReader(f) if x['field']=='owner']
            for row in rows: self.assertIn("'"+row['owner'],values)

    def test_clean_cli_reproduction_and_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'report'
            args=[sys.executable,str(ROOT/'crm_quality.py'),str(ROOT/'sample_properties.csv'),str(ROOT/'sample_reference.csv'),str(out)]
            run=subprocess.run(args,capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertIn('53 review items',run.stdout)
            self.assertNotIn(b'\r', (out/'review.html').read_bytes())
            self.assertIn(b'\n', (out/'review.html').read_bytes())
            for name in ('review.csv','summary.csv','review.html'):
                self.assertEqual((out/name).read_bytes(),(ROOT/'example_report'/name).read_bytes())
            before={p.name:p.read_bytes() for p in out.iterdir()}
            again=subprocess.run(args,capture_output=True,text=True)
            self.assertEqual(again.returncode,2)
            self.assertEqual(before,{p.name:p.read_bytes() for p in out.iterdir()})
