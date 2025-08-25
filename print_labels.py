#!/usr/bin/env python3
"""
Generate printable labels (PDF) for Totescan inventory CSV exports.

- Reads one or more CSV files from a path or a folder (default: ./data/*.csv)
- Groups rows by TOTE ID
- Creates one label per tote including:
  - Tote ID (big)
  - Tote Title
  - Optional location
  - Optional QR code (if QRDATA present)
  - Bulleted list of items with quantity and short description
- Outputs a multi-page PDF (default: ./output/labels.pdf)

Usage:
  python print_labels.py -i data -o output/labels.pdf
  python print_labels.py -i data/your-file.csv

CSV assumptions:
- Primary table header columns contain: TOTE ID, TOTE TITLE, ITEM TITLE, ITEM DESCRIPTION, ITEM QUANTITY, QRDATA, TOTE LOCATION
- Some exports append an "Empty ToteScan labels without items" section with a reduced header set. This script understands both.

Dependencies: reportlab, qrcode (optional for QR code), pillow (used by qrcode)
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import hashlib
import requests

# Try to import QR code libs; run without QR if not available
try:
    import qrcode
    from PIL import Image
except Exception:  # pragma: no cover - optional
    qrcode = None  # type: ignore
    Image = None  # type: ignore

from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


PRIMARY_HEADER = [
    "PROFILE",
    "TOTE ID",
    "PARENT TOTE ID",
    "QRDATA",
    "TOTE LOCATION",
    "TOTE TITLE",
    "ITEM TITLE",
    "ITEM DESCRIPTION",
    "UPC",
    "ITEM QUANTITY",
    "IMAGES",
    "CREATED",
    "UPDATED",
    "ITEM URL",
]

EMPTY_HEADER = [
    "PROFILE",
    "TOTE ID",
    "TOTE LOCATION",
    "TOTE TITLE",
]


@dataclass
class ToteItem:
    title: str
    description: str = ""
    quantity: int = 1
    image_url: Optional[str] = None


@dataclass
class Tote:
    tote_id: str
    title: str = ""
    location: str = ""
    qrdata: Optional[str] = None
    items: List[ToteItem] = field(default_factory=list)
    parent_id: Optional[str] = None
    children: List[str] = field(default_factory=list)

    def add_item(self, title: str, description: str = "", quantity: Optional[int] = None, image_url: Optional[str] = None):
        if not title and not description:
            return
        try:
            qty = int(quantity) if quantity not in (None, "", " ") else 1
        except Exception:
            qty = 1
        self.items.append(ToteItem(title=title.strip(), description=(description or "").strip(), quantity=qty, image_url=(image_url or None)))


def sniff_headers(line: List[str]) -> str:
    # Normalize by stripping quotes/spaces
    norm = [c.strip().strip('\ufeff') for c in line]  # handle BOM if present
    if len(norm) >= len(PRIMARY_HEADER) and all(h in norm for h in ["TOTE ID", "QRDATA", "ITEM TITLE", "ITEM QUANTITY"]):
        return "PRIMARY"
    if len(norm) == len(EMPTY_HEADER) and all(h in norm for h in EMPTY_HEADER):
        return "EMPTY"
    return "UNKNOWN"


def read_csvs(paths: List[str]) -> Dict[str, Tote]:
    totes: Dict[str, Tote] = {}

    def get_or_create(tote_id: str) -> Tote:
        if tote_id not in totes:
            totes[tote_id] = Tote(tote_id=tote_id)
        return totes[tote_id]

    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            current_mode = "UNKNOWN"
            header_index: Dict[str, int] = {}
            for row in reader:
                # skip completely empty lines
                if not any(cell.strip() for cell in row):
                    continue

                # detect section switchers like the literal line: "Empty ToteScan labels without items"
                if len(row) == 1 and "Empty ToteScan labels" in row[0]:
                    current_mode = "EMPTY_PENDING_HEADER"
                    header_index = {}
                    continue

                # Detect header rows
                mode = sniff_headers(row)
                if mode == "PRIMARY":
                    current_mode = "PRIMARY"
                    header_index = {name: row.index(name) for name in row}
                    continue
                elif mode == "EMPTY":
                    current_mode = "EMPTY"
                    header_index = {name: row.index(name) for name in row}
                    continue
                elif current_mode == "EMPTY_PENDING_HEADER":
                    # If we were expecting an EMPTY header but sniff didn't catch, try manual match
                    if all(h in row for h in EMPTY_HEADER):
                        current_mode = "EMPTY"
                        header_index = {name: row.index(name) for name in row}
                        continue

                # Process data rows
                if current_mode == "PRIMARY":
                    try:
                        tote_id = row[header_index.get("TOTE ID")]
                    except Exception:
                        continue
                    tote = get_or_create(tote_id)
                    tote.title = tote.title or row[header_index.get("TOTE TITLE", 0)].strip()
                    tote.location = tote.location or row[header_index.get("TOTE LOCATION", 0)].strip()
                    tote.qrdata = tote.qrdata or row[header_index.get("QRDATA", 0)].strip() or None
                    # Parent tote id, if present
                    try:
                        p_id = row[header_index.get("PARENT TOTE ID", -1)].strip() if header_index.get("PARENT TOTE ID") is not None else ""
                    except Exception:
                        p_id = ""
                    if p_id:
                        tote.parent_id = p_id
                    item_title = row[header_index.get("ITEM TITLE", 0)].strip()
                    item_desc = row[header_index.get("ITEM DESCRIPTION", 0)].strip()
                    qty_raw = row[header_index.get("ITEM QUANTITY", 0)].strip()
                    images_field = row[header_index.get("IMAGES", 0)].strip()
                    first_image = None
                    if images_field:
                        # IMAGES may contain multiple URLs separated by whitespace
                        parts = [p for p in images_field.split() if p.startswith("http")]
                        if parts:
                            first_image = parts[0]
                    tote.add_item(item_title, item_desc, qty_raw, first_image)
                elif current_mode == "EMPTY":
                    # Omit empty totes from output list: only update existing totes (do not create new ones)
                    try:
                        tote_id = row[header_index.get("TOTE ID")]
                    except Exception:
                        continue
                    tote = totes.get(tote_id)
                    if not tote:
                        # Skip creating a tote based solely on the EMPTY section
                        continue
                    tote.title = tote.title or row[header_index.get("TOTE TITLE", 0)].strip()
                    tote.location = tote.location or row[header_index.get("TOTE LOCATION", 0)].strip()
                    # No items to add in this section
                else:
                    # Unknown content, ignore
                    continue

    # Build parent->children links
    for t in list(totes.values()):
        if t.parent_id:
            parent = totes.get(t.parent_id)
            if not parent:
                # Do not create stub parents; if a parent isn't in PRIMARY data, omit it
                continue
            if t.tote_id not in parent.children:
                parent.children.append(t.tote_id)

    return totes


# Register a nicer font if available (optional). We'll fall back silently if not.
def try_register_fonts():  # pragma: no cover - rendering nicety
    # Try some common macOS fonts
    candidates: List[Tuple[str, str]] = [
        ("SFNS.ttf", "/System/Library/Fonts/SFNS.ttf"),
        ("HelveticaNeueDeskInterface.ttf", "/System/Library/Fonts/HelveticaNeueDeskInterface.ttc"),
    ]
    for name, path in candidates:
        try:
            if os.path.exists(path):
                pdfmetrics.registerFont(TTFont("Body", path))
                return "Body"
        except Exception:
            pass
    return "Helvetica"


def make_qr_image(data: str, box_size: int = 6, border: int = 2) -> Optional[Image.Image]:  # type: ignore[name-defined]
    if not qrcode or not Image:
        return None
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    if hasattr(img, "convert"):
        return img.convert("RGB")
    return None


# Image thumbnail caching and loading
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache", "images")


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def fetch_thumbnail(url: str, thumb_px: int = 128) -> Optional[Image.Image]:  # type: ignore[name-defined]
    if not Image:
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _hash(url)
    thumb_path = os.path.join(CACHE_DIR, f"{key}_{thumb_px}.png")
    # Return cached thumbnail if present
    if os.path.exists(thumb_path):
        try:
            return Image.open(thumb_path).convert("RGB")
        except Exception:
            try:
                os.remove(thumb_path)
            except Exception:
                pass
    # Download
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        # Create thumbnail while preserving aspect ratio
        img_copy = img.copy()
        img_copy.thumbnail((thumb_px, thumb_px))
        # Save to cache
        try:
            img_copy.save(thumb_path, format="PNG")
        except Exception:
            pass
        return img_copy
    except Exception:
        return None


def wrap_text(text: str, max_width: float, font_name: str, font_size: int) -> List[str]:
    # simple word-wrap using ReportLab metrics
    words = text.split()
    lines: List[str] = []
    cur: List[str] = []
    for w in words:
        candidate = (" ".join(cur + [w])).strip()
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    if not lines:
        lines = [""]
    return lines


def draw_label(c: canvas.Canvas, tote: Tote, page_size, margin=0.5 * inch, totes_map: Optional[Dict[str, Tote]] = None, render_mode: str = "thumbs"):
    width, height = page_size
    content_width = width - 2 * margin

    font_body = try_register_fonts()
    font_bold = "Helvetica-Bold"

    y = height - margin
    top_y0 = y

    # QR code (optional) at top-right
    qr_side = 1.5 * inch
    qr_drawn = False
    if tote.qrdata:
        img = make_qr_image(tote.qrdata)
        if img is not None:
            bio = io.BytesIO()
            img.save(bio, format="PNG")
            bio.seek(0)
            c.drawImage(ImageReader(bio), width - margin - qr_side, y - qr_side, qr_side, qr_side)
            qr_drawn = True

    # Compute available width to avoid overlapping the QR
    reserved_right = (qr_side + 0.25 * inch) if qr_drawn else 0
    avail_width = content_width - reserved_right

    # Title first (large), wrapped if needed
    c.setFont(font_bold, 24)
    title = tote.title or "(Untitled tote)"
    title_lines = wrap_text(title, avail_width, font_bold, 24)
    for line in title_lines:
        c.drawString(margin, y - 10, line)
        y -= 26

    # Then Tote ID (smaller)
    c.setFont(font_bold, 16)
    tote_id_text = f"Tote: {tote.tote_id}"
    id_lines = wrap_text(tote_id_text, avail_width, font_bold, 16)
    for line in id_lines:
        c.drawString(margin, y, line)
        y -= 18

    # Location
    if tote.location:
        c.setFont(font_body, 10)
        c.setFillColor(colors.grey)
        loc_text = f"Location: {tote.location}"
        loc_lines = wrap_text(loc_text, avail_width, font_body, 10)
        for line in loc_lines:
            c.drawString(margin, y, line)
            y -= 14
        c.setFillColor(colors.black)
        y -= 2
    else:
        y -= 8

    # Spacing before items (separator line removed per request)
    y -= 12

    # Sub totes (children)
    if tote.children:
        c.setFont(font_bold, 12)
        c.drawString(margin, y, "Sub totes:")
        y -= 16
        c.setFont(font_body, 10)
    for child_id in tote.children:
            child_title = ""
            if totes_map and child_id in totes_map:
                child_title = totes_map[child_id].title or ""
            line = f"• {child_id}" + (f" — {child_title}" if child_title else "")
            for wrapped in wrap_text(line, avail_width, font_body, 10):
                c.drawString(margin + 12, y, wrapped)
                y -= 12
    # Extra spacing before Items section to avoid crowding
    y -= 14

    # Items rendering (as reusable renderer), then child's contents
    c.setFont(font_bold, 12)
    c.drawString(margin, y, "Items:")
    y -= 16

    c.setFont(font_body, 10)
    # Shared layout values and helpers
    gutter = 0.15 * inch
    pad = 4
    max_y = margin + 24
    qr_bottom_y = (top_y0 - qr_side) if qr_drawn else None

    def ellipsize(text: str, max_w: float, font: str, size: int) -> str:
        if pdfmetrics.stringWidth(text, font, size) <= max_w:
            return text
        ell = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi) // 2
            if pdfmetrics.stringWidth(text[:mid] + ell, font, size) <= max_w:
                lo = mid + 1
            else:
                hi = mid
        return text[: max(lo - 1, 0)] + ell

    def render_items_grid_thumbs(items: List[ToteItem]):
        nonlocal y
        cols = 5
        if not items:
            c.drawString(margin + 12, y, "(No items recorded)")
            y -= 14
            return
        i = 0
        while i < len(items):
            row_items = items[i : i + cols]
            row_available_width = content_width - (qr_side + 0.25 * inch) if (qr_bottom_y is not None and y > qr_bottom_y) else content_width
            col_width = (row_available_width - gutter * (cols - 1)) / cols

            def measure_cell(item, col_w: float) -> float:
                thumb_img = fetch_thumbnail(item.image_url) if item.image_url else None
                thumb_h = 0.7 * inch if thumb_img is not None else 0.0
                title_text = f"{item.quantity} × {item.title}" if item.title else f"{item.quantity} × (untitled)"
                desc_text = (item.description or "").strip()
                title_lines = wrap_text(title_text, col_w - 2 * pad, font_body, 10)
                desc_lines = wrap_text(desc_text, col_w - 2 * pad, font_body, 9) if desc_text else []
                text_h = len(title_lines) * 11 + (len(desc_lines) * 10 if desc_lines else 0)
                img_space = (thumb_h + 4) if thumb_h else 0
                return pad + img_space + text_h + pad

            def draw_cell(item, x_left: float, y_top: float, col_w: float) -> float:
                thumb_img = fetch_thumbnail(item.image_url) if item.image_url else None
                thumb_h = 0.7 * inch if thumb_img is not None else 0.0
                thumb_w = thumb_h if thumb_h else 0.0
                title_text = f"{item.quantity} × {item.title}" if item.title else f"{item.quantity} × (untitled)"
                desc_text = (item.description or "").strip()
                title_lines = wrap_text(title_text, col_w - 2 * pad, font_body, 10)
                desc_lines = wrap_text(desc_text, col_w - 2 * pad, font_body, 9) if desc_text else []
                text_h = len(title_lines) * 11 + (len(desc_lines) * 10 if desc_lines else 0)
                img_space = (thumb_h + 4) if thumb_h else 0
                cell_h = pad + img_space + text_h + pad
                y_cursor = y_top - pad
                if thumb_img is not None:
                    bio = io.BytesIO()
                    thumb_img.save(bio, format="PNG")
                    bio.seek(0)
                    c.drawImage(ImageReader(bio), x_left + pad, y_cursor - thumb_h, thumb_w, thumb_h, preserveAspectRatio=True, mask=None)
                    y_cursor -= (thumb_h + 4)
                c.setFillColor(colors.black)
                c.setFont(font_body, 10)
                for line in title_lines:
                    c.drawString(x_left + pad, y_cursor - 10, line)
                    y_cursor -= 11
                if desc_lines:
                    c.setFillColor(colors.grey)
                    c.setFont(font_body, 9)
                    for line in desc_lines:
                        c.drawString(x_left + pad, y_cursor - 9, line)
                        y_cursor -= 10
                    c.setFillColor(colors.black)
                return cell_h

            row_heights = [measure_cell(it, col_width) for it in row_items]
            row_h = max(row_heights) if row_heights else 0
            if y - row_h < max_y:
                c.showPage()
                y = height - margin
                c.setFont(font_bold, 16)
                c.drawString(margin, y, f"Tote {tote.tote_id} (cont.)")
                y -= 22
                c.setFont(font_body, 10)
            for col_idx, it in enumerate(row_items):
                x_left = margin + col_idx * (col_width + gutter)
                draw_cell(it, x_left, y, col_width)
            y -= (row_h + 6)
            i += cols

    def render_items_grid_text(items: List[ToteItem]):
        nonlocal y
        cols = 4
        if not items:
            c.drawString(margin + 12, y, "(No items recorded)")
            y -= 14
            return
        i = 0
        title_size = 10
        desc_size = 9
        while i < len(items):
            row_items = items[i : i + cols]
            row_available_width = content_width - (qr_side + 0.25 * inch) if (qr_bottom_y is not None and y > qr_bottom_y) else content_width
            col_width = (row_available_width - gutter * (cols - 1)) / cols

            def measure_cell_text(item, col_w: float) -> float:
                text_w = col_w - 2 * pad
                title_text = f"{item.quantity} × {item.title}" if item.title else f"{item.quantity} × (untitled)"
                title_line = ellipsize(title_text, text_w, font_body, title_size)
                desc_text = (item.description or "").strip()
                desc_line = ellipsize(desc_text, text_w, font_body, desc_size) if desc_text else ""
                lines_h = 11 + (10 if desc_line else 0)
                return pad + lines_h + pad

            def draw_cell_text(item, x_left: float, y_top: float, col_w: float) -> float:
                text_w = col_w - 2 * pad
                title_text = f"{item.quantity} × {item.title}" if item.title else f"{item.quantity} × (untitled)"
                title_line = ellipsize(title_text, text_w, font_body, title_size)
                desc_text = (item.description or "").strip()
                desc_line = ellipsize(desc_text, text_w, font_body, desc_size) if desc_text else ""
                cell_h = pad + (11 + (10 if desc_line else 0)) + pad
                y_cursor = y_top - pad
                c.setFillColor(colors.black)
                c.setFont(font_body, title_size)
                c.drawString(x_left + pad, y_cursor - 10, title_line)
                y_cursor -= 11
                if desc_line:
                    c.setFillColor(colors.grey)
                    c.setFont(font_body, desc_size)
                    c.drawString(x_left + pad, y_cursor - 9, desc_line)
                    y_cursor -= 10
                    c.setFillColor(colors.black)
                return cell_h

            row_heights = [measure_cell_text(it, col_width) for it in row_items]
            row_h = max(row_heights) if row_heights else 0
            if y - row_h < max_y:
                c.showPage()
                y = height - margin
                c.setFont(font_bold, 16)
                c.drawString(margin, y, f"Tote {tote.tote_id} (cont.)")
                y -= 22
                c.setFont(font_body, 10)
            for col_idx, it in enumerate(row_items):
                x_left = margin + col_idx * (col_width + gutter)
                draw_cell_text(it, x_left, y, col_width)
            y -= (row_h + 6)
            i += cols

    # Render this tote's items based on selected mode
    if render_mode == "text":
        render_items_grid_text(tote.items)
    else:
        render_items_grid_thumbs(tote.items)

    # Now render each child's full content (thumbnails and titles)
    if tote.children:
        for child_id in tote.children:
            child = totes_map.get(child_id) if totes_map else None
            # Section header per child
            c.setFont(font_bold, 12)
            header = f"Sub tote: {child_id}"
            if child and child.title:
                header += f" — {child.title}"
            # Ensure spacing before the child section; account for page breaks
            section_gap = 16
            if y - (section_gap + 16) < max_y:
                c.showPage()
                y = height - margin
                c.setFont(font_bold, 16)
                c.drawString(margin, y, f"Tote {tote.tote_id} (cont.)")
                y -= 22
            else:
                y -= section_gap
            c.setFont(font_bold, 12)
            c.drawString(margin, y, header)
            y -= 16
            c.setFont(font_body, 10)
            # Render grid of child items per mode (or a placeholder if none)
            if render_mode == "text":
                render_items_grid_text(child.items if child else [])
            else:
                render_items_grid_thumbs(child.items if child else [])

    # Finish page
    c.showPage()


def generate_pdf(totes: Dict[str, Tote], output_path: str, page: str = "letter", render_mode: str = "thumbs"):
    page_size = letter if page.lower() == "letter" else A4
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    c = canvas.Canvas(output_path, pagesize=page_size)

    # Stable order by tote id
    for tote_id in sorted(totes.keys()):
        draw_label(c, totes[tote_id], page_size, totes_map=totes, render_mode=render_mode)

    c.save()


def collect_input_paths(input_path: str) -> List[str]:
    if os.path.isdir(input_path):
        # all CSVs in folder
        paths = sorted(glob.glob(os.path.join(input_path, "*.csv")))
    else:
        # allow globbing as well
        paths = sorted(glob.glob(input_path))
        if not paths and os.path.isfile(input_path):
            paths = [input_path]
    if not paths:
        raise FileNotFoundError(f"No CSV files found for: {input_path}")
    return paths


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Totescan tote labels PDF from CSV export(s)")
    p.add_argument("-i", "--input", default="data", help="Input CSV file, glob, or folder (default: data)")
    p.add_argument("-o", "--output", default=os.path.join("output", "labels.pdf"), help="Output PDF path or base (default: output/labels.pdf). With --mode both, writes *_thumbs.pdf and *_text.pdf")
    p.add_argument("--page", choices=["letter", "A4"], default="letter", help="Page size (default: letter)")
    p.add_argument("--mode", choices=["thumbs", "text", "both"], default="both", help="Render thumbnails, text-only (2 rows), or both (default: both)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        paths = collect_input_paths(args.input)
    except Exception as e:
        print(f"Error: {e}")
        return 2

    totes = read_csvs(paths)
    if not totes:
        print("No totes found in input.")
        return 1

    try:
        if args.mode == "both":
            base, ext = os.path.splitext(args.output)
            ext = ext or ".pdf"
            thumbs_path = f"{base}_thumbs{ext}"
            text_path = f"{base}_text{ext}"
            generate_pdf(totes, thumbs_path, page=args.page, render_mode="thumbs")
            generate_pdf(totes, text_path, page=args.page, render_mode="text")
            print(f"Wrote labels (thumbs) for {len(totes)} totes to {thumbs_path}")
            print(f"Wrote labels (text) for {len(totes)} totes to {text_path}")
        elif args.mode == "thumbs":
            generate_pdf(totes, args.output, page=args.page, render_mode="thumbs")
            print(f"Wrote labels (thumbs) for {len(totes)} totes to {args.output}")
        else:
            generate_pdf(totes, args.output, page=args.page, render_mode="text")
            print(f"Wrote labels (text) for {len(totes)} totes to {args.output}")
    except Exception as e:
        print(f"Failed to generate PDF: {e}")
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
