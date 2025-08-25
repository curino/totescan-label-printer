# Totescan Label Printer

Generate printable labels (PDF) for Totescan inventory CSV exports.

## What it does
- Reads one or more CSV file(s) from `data/`.
- Groups rows by `TOTE ID` and builds labels with:
  - Tote ID (big)
  - Tote Title and Location
  - Optional QR code (uses `QRDATA` if present)
  - List of items with quantities and descriptions
- Produces a multi-page PDF with one label per tote.

## Setup

Create a virtual environment (optional) and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

- All CSVs in `data/` to `output/labels.pdf` (default letter size):

```bash
python print_labels.py -i data -o output/labels.pdf
```

- A single CSV file or glob, A4 paper:

```bash
python print_labels.py -i data/your.csv --page A4
```

The script recognizes the standard Totescan export table and the "Empty ToteScan labels without items" section.

## Notes
- If the `qrcode` dependency is missing, the script still works but omits QR codes.
- Very long item lists will continue on subsequent pages marked with “(cont.)”.
