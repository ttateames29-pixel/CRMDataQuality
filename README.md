# CRM data quality

My internship work involved reviewing inconsistent CRM information. I wanted to make potential duplicates and differences easier to spot, while leaving the decision to change a record with a person.

A separate demonstration was prepared with AI assistance in October 2026. It uses synthetic data and is not the employer's implementation. It flags records for review; it does not decide which source is true, merge records, or connect to a live system.

The demonstration uses synthetic records and reference values. Its place and organization names are illustrative.

This repository includes the separate demonstration code, synthetic inputs, tests, and example reports. Its outputs are not results achieved at Pinnacle. No employer source code, records, credentials, or live integrations are included.


## Run the demo

Use Python 3.10 or newer. No packages, credentials, accounts, or network access are needed.

```bash
python crm_quality.py sample_properties.csv sample_reference.csv my_review
```

Open `my_review/review.html` locally in your browser. You can also download and open [the example report](example_report/review.html). GitHub displays HTML source rather than running it.

The example compares 10 synthetic records with 8 synthetic references and produces 53 review items. These are proposals to investigate, not 53 confirmed errors or measured business results. Inputs remain unchanged.

## Test it

```bash
python -m unittest discover -s tests -v
```

The 32 tests cover identity matching, input preservation, malformed CSV, precise decimals, safe report handling, CLI failures, and generated output. Validation was run on Windows Python 3.13.13; this is not a claim of production readiness or cross-platform testing.

## Inputs and limits

Both CSV files use this column order:

```text
record_id,county,parcel_id,address,city,state,postal_code,owner,building_sqft
```

Record IDs must be nonempty and unique within each file. Parcel matching requires a recognized US state, DC, or supported territory abbreviation, county, and parcel ID. Missing or invalid state disables identity matching. Address matching uses exact normalized text and requires a valid postal code; it does not use fuzzy matching or verify legal addresses.

The reference dataset is evidence to review, not an automatic source of truth. Invalid reference fields do not supply change proposals. Duplicate results are candidate pairs: three matching records generate three pairs. Large datasets and duplicate groups would need a different review design.

Use a new output directory with an existing parent. Existing output paths are rejected. Reports are staged and failures are cleaned up; this is not a crash-proof transaction or protection against concurrent tampering.

CSV identifiers keep leading zeros in the file. Spreadsheet software may still auto-convert them; import parcel IDs and postal codes as text. HTML content is escaped and formula prefixes in exported CSV values are neutralized, but this is not a universal spreadsheet security guarantee.
