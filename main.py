#!/usr/bin/env python3
"""
qr_pdf_gen.py — Generate a label-printer PDF of version-2 QR codes from a CSV of URLs.

Each URL is rendered as a vector SVG QR code on its own page.
Page size matches the QR code exactly (12×12 mm by default), including only
the mandatory 4-module quiet zone required by the QR code specification.

Usage:
    python3 main.py serials.csv serials.pdf [--size MM]

Arguments:
    input.csv       CSV file containing URLs (uppercase recommended)
    output.pdf      Destination PDF file

Options:
    --size MM       Label size in millimetres; page will be SIZE x SIZE mm
                    (default: 12)
    --help / -h     Show this help message
"""

import argparse
import csv
import io
import sys
from pathlib import Path

# ── Third-party ───────────────────────────────────────────────────────────────
try:
    import qrcode
    import qrcode.image.svg as qr_svg
except ImportError:
    sys.exit("Missing dependency: pip install qrcode")

try:
    from svglib.svglib import svg2rlg
except ImportError:
    sys.exit("Missing dependency: pip install svglib")

try:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.graphics import renderPDF
except ImportError:
    sys.exit("Missing dependency: pip install reportlab")


# QR spec: version 2 = 25×25 data modules + 4-module quiet zone on each side
_QR_VERSION       = 2
_QR_QUIET_ZONE    = 4   # minimum per ISO/IEC 18004
_QR_DATA_MODULES  = 25  # version 2
_QR_TOTAL_MODULES = _QR_DATA_MODULES + _QR_QUIET_ZONE * 2  # = 33


# ── Core helpers ──────────────────────────────────────────────────────────────

def build_qr_svg(url: str) -> bytes:
    """
    Return raw SVG bytes for a version-2 QR code encoding *url*.
    border=4 gives exactly the spec-minimum quiet zone.
    SvgPathImage produces a compact <path>-based vector (no raster data).
    """
    qr = qrcode.QRCode(
        version=_QR_VERSION,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,           # 1 unit per module; we scale in ReportLab
        border=_QR_QUIET_ZONE,
    )
    qr.add_data(url)
    try:
        qr.make(fit=False)    # strict version=2, no auto-upgrade
    except qrcode.exceptions.DataOverflowError:
        raise ValueError(
            f"URL is too long for a version-2 QR code "
            f"({len(url)} chars). Version 2 supports ~32 alphanumeric chars."
        )

    img = qr.make_image(image_factory=qr_svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


def svg_to_drawing(svg_bytes: bytes):
    """Convert raw SVG bytes to a ReportLab Drawing via svglib."""
    return svg2rlg(io.BytesIO(svg_bytes))


def read_urls(csv_path: Path) -> list[str]:
    """Read URLs from a headerless single-column CSV. Returns a list of non-empty URL strings."""
    urls = []
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for i, row in enumerate(reader, start=1):
            if not row:
                continue
            url = row[0].strip()
            if url:
                urls.append(url)
            else:
                print(f"[warn] Row {i}: empty — skipped.")
    return urls


# ── PDF builder ───────────────────────────────────────────────────────────────

def generate_pdf(
    urls: list[str],
    output_path: Path,
    label_size_mm: float = 12.0,
) -> None:
    """
    Create a PDF where each page is exactly label_size_mm × label_size_mm and
    holds one vector QR code that fills the page edge-to-edge, with only the
    mandatory 4-module quiet zone as margin.
    """
    page_pt = label_size_mm * mm          # page width and height in points
    page_size = (page_pt, page_pt)

    c = rl_canvas.Canvas(str(output_path), pagesize=page_size)

    errors: list[tuple[int, str, str]] = []

    for idx, url in enumerate(urls, start=1):
        print(f"[{idx}/{len(urls)}] {url}")

        # ── Generate QR SVG ──────────────────────────────────────────────────
        try:
            svg_bytes = build_qr_svg(url)
        except ValueError as exc:
            print(f"  [error] {exc} — page will show error notice.")
            errors.append((idx, url, str(exc)))
            _draw_error_page(c, page_pt, idx, url, str(exc))
            c.showPage()
            continue

        drawing = svg_to_drawing(svg_bytes)

        # ── Scale drawing so it fills the page exactly ───────────────────────
        # svglib honours the SVG viewBox, so drawing.width == drawing.height
        # == _QR_TOTAL_MODULES (in user units from box_size=1).
        # We scale so those units map exactly to page_pt points.
        scale = page_pt / drawing.width
        drawing.width  = page_pt
        drawing.height = page_pt
        drawing.transform = (scale, 0, 0, scale, 0, 0)

        # ── Render at origin (bottom-left of page) ───────────────────────────
        renderPDF.draw(drawing, c, 0, 0)

        c.showPage()

    c.save()

    if errors:
        print(f"\n[warn] {len(errors)} URL(s) could not be encoded:")
        for page_num, bad_url, reason in errors:
            print(f"  Page {page_num}: {bad_url}\n    → {reason}")


def _draw_error_page(
    c: rl_canvas.Canvas,
    page_pt: float,
    idx: int,
    url: str,
    reason: str,
) -> None:
    """Draw a minimal error placeholder for a URL that could not be encoded."""
    c.setFont("Helvetica-Bold", max(3, page_pt * 0.08))
    c.setFillColor(colors.red)
    c.drawCentredString(page_pt / 2, page_pt / 2 + page_pt * 0.05, "ERR")
    c.setFont("Helvetica", max(2, page_pt * 0.05))
    c.setFillColor(colors.black)
    c.drawCentredString(page_pt / 2, page_pt / 2 - page_pt * 0.05,
                        f"#{idx}: too long")


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a label-printer PDF of version-2 QR codes from a CSV of URLs. "
            "Each page is sized to fit the QR code exactly — quiet zone only, no extra margin."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input_csv",  type=Path, help="Input CSV file containing URLs")
    parser.add_argument("output_pdf", type=Path, help="Output PDF file")
    parser.add_argument(
        "--size", type=float, default=12.0, metavar="MM",
        help="Label size in mm; page will be SIZE×SIZE (default: 12)",
    )

    args = parser.parse_args()

    if not args.input_csv.exists():
        sys.exit(f"Error: input file '{args.input_csv}' not found.")
    if args.size <= 0:
        sys.exit("Error: --size must be a positive number.")

    urls = read_urls(args.input_csv)
    if not urls:
        sys.exit("Error: no URLs found in the CSV file.")

    print(
        f"Found {len(urls)} URL(s). "
        f"Generating {args.size}×{args.size} mm label PDF …\n"
    )

    generate_pdf(
        urls=urls,
        output_path=args.output_pdf,
        label_size_mm=args.size,
    )

    size_pt = args.size * mm
    print(
        f"\n✓ PDF written to: {args.output_pdf}  "
        f"({len(urls)} pages, {args.size}×{args.size} mm / "
        f"{size_pt:.2f}×{size_pt:.2f} pt each)"
    )


if __name__ == "__main__":
    main()
