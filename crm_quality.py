"""Independent synthetic portfolio demonstration using fictional property records."""

import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from html import escape
from itertools import combinations
from pathlib import Path
import re
import io
import tempfile

FIELDS = (
    "record_id", "county", "parcel_id", "address", "city", "state",
    "postal_code", "owner", "building_sqft",
)
REVIEW_FIELDS = (
    "record_id", "category", "field", "current", "proposed",
    "reference_id", "detail",
)
NUMBER = re.compile(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?\Z")
STATES = frozenset("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC AS GU MP PR VI".split())
POSTAL = re.compile(r"(?:\d{5}|\d{9})\Z")


def compact(value):
    return " ".join(value.strip().split())


def area(value):
    """Return an exact decimal string; reject malformed or negative areas."""
    value = compact(value)
    if not value:
        return ""
    if not NUMBER.fullmatch(value):
        raise ValueError("Building area must be a nonnegative number with valid grouping.")
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("Building area is invalid.") from exc
    return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else str(number)


def normalized(row):
    result = {field: compact(row[field]) for field in FIELDS}
    for field in ("county", "address", "city", "state", "owner"):
        result[field] = result[field].upper()
    invalid = {}
    try:
        result["building_sqft"] = area(result["building_sqft"])
    except ValueError as exc:
        invalid["building_sqft"] = str(exc)
    if result["postal_code"] and not POSTAL.fullmatch(result["postal_code"]):
        invalid["postal_code"] = "Postal code must contain five or nine digits."
    if result["state"] not in STATES:
        invalid["state"] = "State must be a supported US state, DC, or territory abbreviation; required for identity matching."
    return result, invalid


def validate_quotes(text):
    """Reject bare quotes and trailing characters after closing CSV quotes."""
    mode, line = "start", 1
    for char in text:
        if mode == "quoted":
            if char == '"':
                mode = "closed"
        elif mode == "closed":
            if char == '"':
                mode = "quoted"
            elif char in ",\r\n":
                mode = "start"
            else:
                raise ValueError(f"CSV line {line}: unexpected character after closing quote.")
        elif char == '"':
            if mode != "start":
                raise ValueError(f"CSV line {line}: quote inside an unquoted field.")
            mode = "quoted"
        elif char in ",\r\n":
            mode = "start"
        else:
            mode = "unquoted"
        if char == "\n":
            line += 1
    if mode == "quoted":
        raise ValueError(f"CSV line {line}: unterminated quoted field.")


def read_records(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        text = handle.read()
    validate_quotes(text)
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    try:
        if reader.fieldnames != list(FIELDS):
            raise ValueError("CSV line 1: columns must match the documented schema and order.")
        records, seen = [], set()
        for row in reader:
            line = reader.line_num
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV line {line}: unexpected number of values.")
            record_id = compact(row["record_id"])
            if not record_id or record_id in seen:
                raise ValueError(f"CSV line {line}: blank or repeated record ID.")
            seen.add(record_id)
            records.append(row)
        if not records:
            raise ValueError("CSV line 1: input must include at least one record.")
        return records
    except csv.Error as exc:
        raise ValueError(f"CSV line {reader.line_num or 1}: {exc}") from exc


def parcel_key(row):
    if row["state"] in STATES and row["county"] and row["parcel_id"]:
        return row["state"], row["county"], row["parcel_id"]
    return None


def address_key(row):
    fields = ("county", "address", "city", "state", "postal_code")
    if row["state"] in STATES and POSTAL.fullmatch(row["postal_code"]) and all(row[field] for field in fields):
        return tuple(row[field] for field in fields)
    return None


def analyze(records, references):
    """Propose review items without modifying either input collection."""
    issues = []

    def add(record_id, category, field="", current="", proposed="", reference_id="", detail=""):
        issues.append(dict(zip(REVIEW_FIELDS, (
            record_id, category, field, current, proposed, reference_id, detail,
        ))))

    clean, clean_reference, reference_invalid = [], [], {}
    for source, destination, is_reference in (
        (records, clean, False), (references, clean_reference, True),
    ):
        for raw in source:
            row, invalid = normalized(raw)
            destination.append(row)
            if is_reference:
                reference_invalid[row["record_id"]] = set(invalid)
            for field, detail in invalid.items():
                add(row["record_id"], "reference_validation" if is_reference else "validation",
                    field, raw[field], detail=detail)
            if is_reference:
                continue
            for field in FIELDS[1:]:
                if field not in invalid and raw[field] != row[field]:
                    add(row["record_id"], "normalization", field, raw[field], row[field],
                        detail="Formatting proposal only. Original input remains unchanged.")
            for field in ("county", "parcel_id", "address", "owner", "building_sqft"):
                if not row[field]:
                    add(row["record_id"], "missing_value", field,
                        detail="A reviewer should verify this missing field.")

    parcels, addresses = defaultdict(list), defaultdict(list)
    for row in clean:
        if parcel_key(row):
            parcels[parcel_key(row)].append(row)
        if address_key(row):
            addresses[address_key(row)].append(row)
    paired = set()
    for group in parcels.values():
        for left, right in combinations(group, 2):
            paired.add(frozenset((left["record_id"], right["record_id"])))
            exact = all(left[field] == right[field] for field in FIELDS[1:])
            add(left["record_id"] + " | " + right["record_id"],
                "exact_duplicate_candidate" if exact else "ambiguous_duplicate_candidate",
                "state + county + parcel_id", detail=(
                    "All normalized fields agree. Review before any merge."
                    if exact else "Parcel identity agrees but other fields differ. Review required."
                ))
    for group in addresses.values():
        for left, right in combinations(group, 2):
            pair = frozenset((left["record_id"], right["record_id"]))
            if pair not in paired:
                paired.add(pair)
                add(left["record_id"] + " | " + right["record_id"],
                    "ambiguous_duplicate_candidate", "address",
                    detail="Address agrees but parcel identity is missing or different. Do not merge automatically.")

    lookup = defaultdict(list)
    for row in clean_reference:
        if parcel_key(row):
            lookup[parcel_key(row)].append(row)
    for row in clean:
        matches = lookup.get(parcel_key(row), [])
        if len(matches) > 1:
            add(row["record_id"], "ambiguous_reference", "state + county + parcel_id",
                detail="Multiple reference records match. No reference changes proposed.")
        elif not matches:
            add(row["record_id"], "no_reference", "state + county + parcel_id",
                detail="No unique parcel reference was found. Address alone is insufficient.")
        else:
            reference = matches[0]
            for field in ("address", "city", "state", "postal_code", "owner", "building_sqft"):
                value = reference[field]
                if value and field not in reference_invalid[reference["record_id"]] and value != row[field]:
                    add(row["record_id"], "reference_difference", field, row[field], value,
                        reference["record_id"], "Fictional reference differs. A reviewer must verify authority and freshness.")
    return issues


def summary(records, references, issues):
    counts = Counter(issue["category"] for issue in issues)
    return {
        "property_records": len(records), "reference_records": len(references),
        "review_items": len(issues),
        "exact_duplicate_pairs": counts["exact_duplicate_candidate"],
        "ambiguous_duplicate_pairs": counts["ambiguous_duplicate_candidate"],
        "reference_differences": counts["reference_difference"],
        "normalization_proposals": counts["normalization"],
        "validation_issues": counts["validation"] + counts["reference_validation"],
    }


def render_html(stats, issues):
    priority = {
        "validation": 0, "reference_validation": 0,
        "exact_duplicate_candidate": 1, "ambiguous_duplicate_candidate": 1,
        "ambiguous_reference": 2, "reference_difference": 3,
        "missing_value": 4, "no_reference": 4, "normalization": 5,
    }
    field_labels = {
        "state + county + parcel_id": "State + county + parcel ID", "parcel_id": "Parcel ID",
        "postal_code": "Postal code", "building_sqft": "Building area",
    }

    def display_value(issue, field):
        value = issue[field]
        if field == "category":
            value = value.replace("_", " ").capitalize()
        elif field == "field":
            value = field_labels.get(value, value.replace("_", " ").capitalize())
        return escape(value)

    cards = "".join(
        f"<div class='card'><strong>{stats[key]}</strong><span>{label}</span></div>"
        for key, label in (
            ("property_records", "Property records"), ("review_items", "Review items"),
            ("exact_duplicate_pairs", "Exact candidate pairs"),
            ("ambiguous_duplicate_pairs", "Ambiguous pairs"),
            ("reference_differences", "Reference differences"),
        )
    )
    rows = "".join(
        "<tr>" + "".join(f"<td>{display_value(issue, field)}</td>" for field in REVIEW_FIELDS) + "</tr>"
        for issue in sorted(issues, key=lambda item: priority.get(item["category"], 6))
    )
    headers = "".join(f"<th>{escape(field.replace('_', ' ').title())}</th>" for field in REVIEW_FIELDS)
    return """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CRM Data Quality | Fictional Portfolio Demonstration</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:#152037;font:15px/1.55 system-ui,sans-serif}
header{background:#101c31;color:#fff;padding:48px max(24px,calc((100vw - 1240px)/2)) 46px}
.eyebrow{font-size:12px;letter-spacing:.18em;color:#7ae0c2;font-weight:700}h1{font-size:40px;line-height:1.12;letter-spacing:-.035em;margin:14px 0}
header p{max-width:700px;color:#c2ccdd;margin:0}main{max-width:1288px;margin:0 auto;padding:28px 24px}
.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:14px}.card{background:#fff;border:1px solid #e0e6ef;border-radius:12px;padding:21px}
.card strong{display:block;font-size:32px;line-height:1.1}.card span{display:block;color:#61718a;font-size:12px;margin-top:10px}
.notice{border-left:4px solid #24a88a;padding:16px 20px;background:#eaf7f3;margin:24px 0;border-radius:4px}
h2{font-size:22px;margin:30px 0 12px}.tablebox{overflow:auto;background:#fff;border:1px solid #e0e6ef;border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:12px}th{text-align:left;padding:14px;background:#edf1f7;color:#43516a;white-space:nowrap}
td{padding:14px;border-top:1px solid #e8edf4;vertical-align:top;min-width:100px}td:last-child{min-width:250px}td:nth-child(2){color:#276e63;font-weight:600}
footer{color:#61718a;font-size:12px;padding:26px 0 0}code{font-family:ui-monospace,monospace}
@media(max-width:800px){.cards{grid-template-columns:repeat(2,1fr)}h1{font-size:32px}header{padding:32px 24px}}
</style><header><div class="eyebrow">TATE AMES / APPLIED BUSINESS SYSTEMS</div>
<h1>Turn uncertain records into<br>clear review decisions.</h1>
<p>CRM data quality demonstration. Identify duplicate candidates, propose formatting changes, and compare records with reference data while preserving every original input.</p>
</header><main><section class="cards">""" + cards + """</section>
<div class="notice"><strong>Fictional data. Human review required.</strong><br>
This independent synthetic demonstration was prepared October 21, 2026 with AI assistance. All supplied records and reference values are synthetic; place and organization names are illustrative and do not describe real properties. The report is not an operational result from Pinnacle Real Estate Advisors.</div>
<h2>Review queue</h2><p>Every row is a proposed review item. A difference is evidence to investigate, not proof that the reference is correct. No source records were changed or merged.</p>
<div class="tablebox"><table><thead><tr>""" + headers + "</tr></thead><tbody>" + rows + """</tbody></table></div>
<footer>Original implementation for a synthetic portfolio demonstration. Runs locally using the Python standard library. No network access, credentials, employer code, or employer records.</footer>
</main></html>"""


def spreadsheet_text(value):
    """Keep spreadsheet software from treating exported text as a formula."""
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _write_reports(destination, records, references, issues):
    destination = Path(destination)
    with (destination / "review.csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows({key: spreadsheet_text(value) for key, value in issue.items()} for issue in issues)
    stats = summary(records, references, issues)
    with (destination / "summary.csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("metric", "count"))
        writer.writerows(stats.items())
    (destination / "review.html").write_text(render_html(stats, issues), encoding="utf-8", newline="\n")
    return stats


def write_reports(destination, records, references, issues):
    """Stage all reports before claiming a new output directory; roll back failures."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Output already exists: {destination}")
    # Require an existing parent rather than leaving newly created ancestors behind.
    with tempfile.TemporaryDirectory(prefix=".crm-stage-", dir=destination.parent) as temp:
        stage = Path(temp)
        stats = _write_reports(stage, records, references, issues)
        destination.mkdir(exist_ok=False)
        moved = []
        try:
            for name in ("review.csv", "summary.csv", "review.html"):
                (stage / name).rename(destination / name)
                moved.append(destination / name)
        except BaseException:
            for path in moved:
                path.unlink()
            destination.rmdir()
            raise
    return stats


def main():
    parser = argparse.ArgumentParser(description="Review fictional CRM data without changing source files.")
    parser.add_argument("properties", type=Path)
    parser.add_argument("references", type=Path)
    parser.add_argument("destination", type=Path, help="A new directory for review reports.")
    args = parser.parse_args()
    try:
        records, references = read_records(args.properties), read_records(args.references)
        issues = analyze(records, references)
        stats = write_reports(args.destination, records, references, issues)
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f"Cannot create report: {exc}\n")
    print("Fictional portfolio demonstration. No input records were changed.")
    print(f"Reviewed {stats['property_records']} records and generated {stats['review_items']} review items.")
    print(f"Open {args.destination / 'review.html'} to inspect the result.")


if __name__ == "__main__":
    main()
