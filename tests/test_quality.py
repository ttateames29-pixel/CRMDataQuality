import copy
import csv
import hashlib
from pathlib import Path
import tempfile
import unittest

from crm_quality import FIELDS, analyze, area, normalized, read_records, render_html, spreadsheet_text, summary, write_reports


def record(identifier="P001", **changes):
    row = dict(zip(FIELDS, (identifier, "CEDAR COUNTY", "00042", "100 EXAMPLE PLAZA",
                           "SAMPLETON", "TX", "00123", "EXAMPLE OWNER", "10000")))
    row.update(changes)
    return row


class QualityTests(unittest.TestCase):
    def test_inputs_and_source_bytes_are_preserved(self):
        root = Path(__file__).resolve().parents[1]
        paths = [root / "sample_properties.csv", root / "sample_reference.csv"]
        before_bytes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        records, references = [read_records(path) for path in paths]
        before_records = copy.deepcopy((records, references))
        issues = analyze(records, references)
        with tempfile.TemporaryDirectory() as temp:
            write_reports(Path(temp) / "report", records, references, issues)
        self.assertEqual((records, references), before_records)
        self.assertEqual(before_bytes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])

    def test_identifier_leading_zeroes_survive(self):
        normalized_row, invalid = normalized(record(parcel_id="00042", postal_code="00123"))
        self.assertEqual(normalized_row["parcel_id"], "00042")
        self.assertEqual(normalized_row["postal_code"], "00123")
        self.assertFalse(invalid)
        issues = analyze([record(), record("P002", parcel_id="42", address="OTHER ADDRESS")], [])
        self.assertFalse(any("duplicate" in issue["category"] for issue in issues))

    def test_parcel_ids_are_scoped_by_county(self):
        issues = analyze([record(), record("P002", county="LINDEN COUNTY")], [])
        self.assertFalse(any("duplicate" in issue["category"] for issue in issues))

    def test_exact_and_ambiguous_candidates_are_separate(self):
        rows = [record(), record("P002"), record("P003", owner="DIFFERENT OWNER")]
        issues = analyze(rows, [])
        self.assertEqual(sum(item["category"] == "exact_duplicate_candidate" for item in issues), 1)
        self.assertEqual(sum(item["category"] == "ambiguous_duplicate_candidate" for item in issues), 2)
        address_only = analyze([record(), record("P004", parcel_id="")], [])
        self.assertTrue(any(item["category"] == "ambiguous_duplicate_candidate" for item in address_only))

    def test_ambiguous_references_make_no_proposals(self):
        issues = analyze([record()], [record("R001", owner="OWNER A"), record("R002", owner="OWNER B")])
        self.assertTrue(any(item["category"] == "ambiguous_reference" for item in issues))
        self.assertFalse(any(item["category"] == "reference_difference" for item in issues))

    def test_numeric_parsing_is_exact_and_rejects_malformed_values(self):
        self.assertEqual(area("1,250.50"), "1250.5")
        self.assertEqual(area("10000.00"), "10000")
        self.assertEqual(area("0"), "0")
        for value in ("12,34", "NaN", "Infinity", "-4", "1e5"):
            with self.assertRaises(ValueError):
                area(value)

    def test_invalid_reference_cannot_propose_invalid_values(self):
        issues = analyze([record()], [record("R001", building_sqft="NaN")])
        self.assertTrue(any(item["category"] == "reference_validation" for item in issues))
        self.assertFalse(any(item["category"] == "reference_difference" and item["field"] == "building_sqft" for item in issues))

    def test_csv_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "records.csv"
            path.write_text("record_id,county\nP001,CEDAR\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_records(path)
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows([record(), record()])
            with self.assertRaises(ValueError):
                read_records(path)

    def test_exported_values_remain_inert_text(self):
        rows = [record(owner="<script>alert('demo')</script>")]
        issues = analyze(rows, [record("R001")])
        html = render_html(summary(rows, [], issues), issues)
        self.assertNotIn("<script>", html.lower())
        self.assertIn("&lt;script&gt;", html.lower())
        self.assertEqual(spreadsheet_text("=1+1"), "'=1+1")
        self.assertEqual(spreadsheet_text("  @SUM(A1)"), "'  @SUM(A1)")
        self.assertEqual(spreadsheet_text("EXAMPLE OWNER"), "EXAMPLE OWNER")

    def test_existing_output_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            sentinel = path / "review.csv"
            sentinel.write_text("original", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_reports(path, [record()], [], [])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
