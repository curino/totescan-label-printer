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
 - Shows sub-totes on their parent labels:
   - A brief bullet list of child tote IDs/titles near the top
   - A full section per child with its own items (in both render modes)

## Setup

Create a virtual environment (optional) and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

- Default: generate BOTH variants to `output/labels_thumbs.pdf` and `output/labels_text.pdf` (letter):

```bash
python print_labels.py -i data -o output/labels.pdf --mode both
```

- A single CSV file or glob, A4 paper:

```bash
python print_labels.py -i data/your.csv --page A4
```

### Render modes

Use `--mode` to control the label variant(s):

- `--mode thumbs` (images):
  - Renders items in a 5-column grid with thumbnails (if available), quantity × title, and optional description.
  - Output path is the `-o/--output` you specify (e.g., `output/labels.pdf`).

- `--mode text` (compact):
  - Renders items as two-row text entries (no thumbnails), with 4 columns per row.
  - Row 1: quantity × title; Row 2: optional grey description. Long lines are ellipsized to fit.
  - Output path is the `-o/--output` you specify.

- `--mode both` (default):
  - Writes two PDFs using the provided `--output` as a base name: `*_thumbs.pdf` and `*_text.pdf`.
  - Example: `-o output/labels.pdf` produces `output/labels_thumbs.pdf` and `output/labels_text.pdf`.

Sub-totes are included in both modes:
- A short “Sub totes” bullet list of child tote IDs/titles near the top of the parent label
- Full child sections with their items rendered in the same mode as the parent (thumbs or text)

## Notes
- If the `qrcode` dependency is missing, the script still works but omits QR codes.
- Very long item lists will continue on subsequent pages marked with “(cont.)”.
- CSV parsing understands the standard Totescan table and the “Empty ToteScan labels without items” section; totes appearing only in the “Empty” section are omitted from output.
