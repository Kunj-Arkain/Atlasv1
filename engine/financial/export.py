"""
engine.financial.export — CSV & PDF Export
=============================================
Phase 2: Export tool run results to downloadable formats.

CSV: stdlib csv module (zero dependencies)
PDF: reportlab if available, otherwise plain-text fallback

Usage:
    from engine.financial.export import export_csv, export_pdf

    csv_bytes = export_csv("amortization", inputs, outputs)
    pdf_bytes = export_pdf("irr_npv", inputs, outputs)
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# CSV EXPORT
# ═══════════════════════════════════════════════════════════════

def export_csv(
    tool_name: str,
    inputs: Dict[str, Any],
    outputs: Dict[str, Any],
    meta: Optional[Dict] = None,
) -> bytes:
    """Export tool results to CSV bytes.

    Different tools get different CSV layouts:
      - amortization → full schedule table
      - sensitivity → matrix table
      - others → key-value pairs
    """
    buf = io.StringIO()
    writer = csv.writer(buf)

    if tool_name == "amortization":
        return _csv_amortization(inputs, outputs, meta)
    elif tool_name == "sensitivity":
        return _csv_sensitivity(inputs, outputs, meta)
    else:
        return _csv_generic(tool_name, inputs, outputs, meta)


def _csv_amortization(
    inputs: Dict, outputs: Dict, meta: Optional[Dict],
) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)

    # Header section
    writer.writerow(["Amortization Schedule"])
    writer.writerow(["Generated", datetime.now(timezone.utc).isoformat()])
    writer.writerow([])
    writer.writerow(["Input Parameters"])
    writer.writerow(["Principal", f"${inputs.get('principal', 0):,.2f}"])
    writer.writerow(["Annual Rate", f"{inputs.get('annual_rate', 0):.4%}"])
    writer.writerow(["Term (months)", inputs.get("term_months", 0)])
    if inputs.get("extra_monthly", 0) > 0:
        writer.writerow(["Extra Monthly", f"${inputs['extra_monthly']:,.2f}"])
    writer.writerow([])

    # Summary
    writer.writerow(["Summary"])
    writer.writerow(["Monthly Payment", f"${outputs.get('monthly_payment', 0):,.2f}"])
    writer.writerow(["Total Interest", f"${outputs.get('total_interest', 0):,.2f}"])
    writer.writerow(["Total Paid", f"${outputs.get('total_paid', 0):,.2f}"])
    writer.writerow(["Actual Term", f"{outputs.get('actual_term_months', 0)} months"])
    writer.writerow([])

    # Schedule
    schedule = outputs.get("schedule", [])
    if schedule:
        writer.writerow([
            "Month", "Payment", "Principal", "Interest",
            "Extra Principal", "Remaining Balance",
        ])
        for row in schedule:
            writer.writerow([
                row.get("month", ""),
                f"${row.get('payment', 0):,.2f}",
                f"${row.get('principal_portion', 0):,.2f}",
                f"${row.get('interest_portion', 0):,.2f}",
                f"${row.get('extra_principal', 0):,.2f}",
                f"${row.get('remaining_balance', 0):,.2f}",
            ])

    return buf.getvalue().encode("utf-8")


def _csv_sensitivity(
    inputs: Dict, outputs: Dict, meta: Optional[Dict],
) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)

    row_var = outputs.get("row_variable", "row")
    col_var = outputs.get("col_variable", "col")
    label = outputs.get("output_label", "result")

    writer.writerow(["Sensitivity Analysis"])
    writer.writerow(["Generated", datetime.now(timezone.utc).isoformat()])
    writer.writerow(["Output Metric", label])
    writer.writerow(["Base Case Value", outputs.get("base_case_value", "")])
    writer.writerow([])

    # Matrix header
    col_values = outputs.get("col_values", [])
    writer.writerow([f"{row_var} \\ {col_var}"] + [str(c) for c in col_values])

    # Matrix body
    row_values = outputs.get("row_values", [])
    matrix = outputs.get("matrix", [])
    for i, row in enumerate(matrix):
        rv = row_values[i] if i < len(row_values) else ""
        writer.writerow([str(rv)] + [str(v) for v in row])

    return buf.getvalue().encode("utf-8")


def _csv_generic(
    tool_name: str, inputs: Dict, outputs: Dict, meta: Optional[Dict],
) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow([f"Tool: {tool_name}"])
    writer.writerow(["Generated", datetime.now(timezone.utc).isoformat()])
    if meta:
        writer.writerow(["Run ID", meta.get("run_id", "")])
        writer.writerow(["Execution (ms)", meta.get("execution_ms", "")])
    writer.writerow([])

    writer.writerow(["Inputs"])
    writer.writerow(["Parameter", "Value"])
    for k, v in inputs.items():
        if isinstance(v, list):
            writer.writerow([k, str(v)])
        else:
            writer.writerow([k, v])
    writer.writerow([])

    writer.writerow(["Outputs"])
    writer.writerow(["Metric", "Value"])
    for k, v in outputs.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list) and len(v) > 10:
            writer.writerow([k, f"[{len(v)} items]"])
        elif isinstance(v, dict):
            writer.writerow([k, str(v)])
        else:
            writer.writerow([k, v])

    return buf.getvalue().encode("utf-8")


# ═══════════════════════════════════════════════════════════════
# PDF EXPORT
# ═══════════════════════════════════════════════════════════════

def export_pdf(
    tool_name: str,
    inputs: Dict[str, Any],
    outputs: Dict[str, Any],
    meta: Optional[Dict] = None,
    title: str = "",
) -> bytes:
    """Export tool results to PDF bytes.

    Uses reportlab if available, otherwise falls back to
    a text-based PDF using fpdf2, or a minimal hand-built PDF.
    """
    try:
        return _pdf_reportlab(tool_name, inputs, outputs, meta, title)
    except ImportError:
        pass

    try:
        return _pdf_fpdf2(tool_name, inputs, outputs, meta, title)
    except ImportError:
        pass

    # Minimal fallback — valid PDF with just text
    return _pdf_minimal(tool_name, inputs, outputs, meta, title)


def _pdf_reportlab(
    tool_name: str, inputs: Dict, outputs: Dict,
    meta: Optional[Dict], title: str,
) -> bytes:
    """Generate PDF using reportlab."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    # Title
    header = title or f"Financial Tool Report: {tool_name}"
    story.append(Paragraph(header, styles["Title"]))
    story.append(Spacer(1, 0.2 * inch))

    # Metadata
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph(f"Generated: {ts}", styles["Normal"]))
    if meta:
        if meta.get("run_id"):
            story.append(Paragraph(f"Run ID: {meta['run_id']}", styles["Normal"]))
        if meta.get("execution_ms"):
            story.append(Paragraph(
                f"Execution Time: {meta['execution_ms']}ms", styles["Normal"]
            ))
    story.append(Spacer(1, 0.3 * inch))

    # Inputs table
    story.append(Paragraph("Input Parameters", styles["Heading2"]))
    input_data = [["Parameter", "Value"]]
    for k, v in inputs.items():
        if isinstance(v, float):
            input_data.append([k, f"{v:,.6g}"])
        elif isinstance(v, list) and len(v) <= 20:
            input_data.append([k, str(v)])
        else:
            input_data.append([k, str(v)[:80]])

    t = Table(input_data, colWidths=[2.5 * inch, 4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#ecf0f1")]),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.3 * inch))

    # Outputs table
    story.append(Paragraph("Results", styles["Heading2"]))
    output_data = [["Metric", "Value"]]
    for k, v in outputs.items():
        if k.startswith("_"):
            continue
        if k == "schedule" and isinstance(v, list):
            output_data.append(["schedule", f"[{len(v)} periods]"])
        elif k == "matrix" and isinstance(v, list):
            output_data.append(["matrix", f"[{len(v)} rows]"])
        elif isinstance(v, float):
            output_data.append([k, f"{v:,.6g}"])
        else:
            output_data.append([k, str(v)[:80]])

    t2 = Table(output_data, colWidths=[2.5 * inch, 4 * inch])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#27ae60")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eafaf1")]),
    ]))
    story.append(t2)

    # Amortization schedule table (if present and not too long)
    schedule = outputs.get("schedule", [])
    if schedule and len(schedule) <= 120:
        story.append(Spacer(1, 0.3 * inch))
        story.append(Paragraph("Amortization Schedule", styles["Heading2"]))
        sched_data = [["Month", "Payment", "Principal", "Interest", "Balance"]]
        for row in schedule:
            sched_data.append([
                str(row.get("month", "")),
                f"${row.get('payment', 0):,.2f}",
                f"${row.get('principal_portion', 0):,.2f}",
                f"${row.get('interest_portion', 0):,.2f}",
                f"${row.get('remaining_balance', 0):,.2f}",
            ])
        st = Table(sched_data, colWidths=[0.8 * inch] * 5)
        st.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34495e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ]))
        story.append(st)

    doc.build(story)
    return buf.getvalue()


def _pdf_fpdf2(
    tool_name: str, inputs: Dict, outputs: Dict,
    meta: Optional[Dict], title: str,
) -> bytes:
    """Generate PDF using fpdf2 (lightweight alternative)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 16)
    header = title or f"Financial Tool Report: {tool_name}"
    pdf.cell(0, 10, header, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(0, 6, f"Generated: {ts}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Inputs
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Input Parameters", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for k, v in inputs.items():
        val = f"{v:,.6g}" if isinstance(v, float) else str(v)[:80]
        pdf.cell(60, 6, str(k), border=1)
        pdf.cell(0, 6, val, border=1, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Outputs
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Results", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for k, v in outputs.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list) and len(v) > 10:
            val = f"[{len(v)} items]"
        elif isinstance(v, float):
            val = f"{v:,.6g}"
        else:
            val = str(v)[:80]
        pdf.cell(60, 6, str(k), border=1)
        pdf.cell(0, 6, val, border=1, new_x="LMARGIN", new_y="NEXT")

    return pdf.output()


def _pdf_minimal(
    tool_name: str, inputs: Dict, outputs: Dict,
    meta: Optional[Dict], title: str,
) -> bytes:
    """Minimal valid PDF using raw PDF syntax (zero dependencies).

    Produces a basic but valid PDF document readable by any viewer.
    """
    lines = []
    header = title or f"Financial Tool Report: {tool_name}"
    lines.append(header)
    lines.append("=" * len(header))
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"Generated: {ts}")
    if meta:
        if meta.get("run_id"):
            lines.append(f"Run ID: {meta['run_id']}")
    lines.append("")

    lines.append("INPUT PARAMETERS")
    lines.append("-" * 40)
    for k, v in inputs.items():
        val = f"{v:,.6g}" if isinstance(v, float) else str(v)
        lines.append(f"  {k}: {val}")
    lines.append("")

    lines.append("RESULTS")
    lines.append("-" * 40)
    for k, v in outputs.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list) and len(v) > 10:
            val = f"[{len(v)} items]"
        elif isinstance(v, float):
            val = f"{v:,.6g}"
        else:
            val = str(v)[:100]
        lines.append(f"  {k}: {val}")

    text = "\n".join(lines)

    # Build minimal valid PDF
    content_stream = f"BT /F1 10 Tf 50 750 Td "
    y = 750
    for line in lines:
        safe = line.replace("(", "\\(").replace(")", "\\)").replace("\\", "\\\\")
        content_stream += f"({safe}) Tj 0 -14 Td "
        y -= 14
        if y < 50:
            break
    content_stream += "ET"

    objects = []
    # Obj 1: Catalog
    objects.append("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj")
    # Obj 2: Pages
    objects.append("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj")
    # Obj 3: Page
    objects.append(
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj"
    )
    # Obj 4: Content stream
    stream_bytes = content_stream.encode("latin-1")
    objects.append(
        f"4 0 obj\n<< /Length {len(stream_bytes)} >>\n"
        f"stream\n{content_stream}\nendstream\nendobj"
    )
    # Obj 5: Font
    objects.append(
        "5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj"
    )

    pdf_out = io.BytesIO()
    pdf_out.write(b"%PDF-1.4\n")

    offsets = []
    for obj in objects:
        offsets.append(pdf_out.tell())
        pdf_out.write(obj.encode("latin-1") + b"\n")

    xref_offset = pdf_out.tell()
    pdf_out.write(b"xref\n")
    pdf_out.write(f"0 {len(objects) + 1}\n".encode())
    pdf_out.write(b"0000000000 65535 f \n")
    for off in offsets:
        pdf_out.write(f"{off:010d} 00000 n \n".encode())

    pdf_out.write(b"trailer\n")
    pdf_out.write(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
    pdf_out.write(b"startxref\n")
    pdf_out.write(f"{xref_offset}\n".encode())
    pdf_out.write(b"%%EOF\n")

    return pdf_out.getvalue()
