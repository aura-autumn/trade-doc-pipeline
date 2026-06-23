"""
Generate sample trade documents for testing.
Run: python generate_samples.py
"""

import os

os.makedirs("./data/sample_docs", exist_ok=True)


def generate_with_reportlab():
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import cm

        def make_doc(filename, fields):
            c = canvas.Canvas(filename, pagesize=A4)
            width, height = A4
            c.setFont("Helvetica-Bold", 16)
            c.drawString(2*cm, height - 2*cm, "COMMERCIAL INVOICE")
            c.setFont("Helvetica", 10)
            y = height - 3.5*cm
            for label, value in fields.items():
                c.setFont("Helvetica-Bold", 9)
                c.drawString(2*cm, y, f"{label}:")
                c.setFont("Helvetica", 9)
                c.drawString(7*cm, y, str(value))
                y -= 0.7*cm
            c.save()
            print(f"Generated: {filename}")

        # Clean document — all fields match CUST001 rules
        make_doc("./data/sample_docs/sample_clean.pdf", {
            "Invoice Number": "INV-2024-001",
            "Consignee Name": "ACME IMPORTS LTD",
            "HS Code": "84713000",
            "Port of Loading": "SHANGHAI, CHINA",
            "Port of Discharge": "NHAVA SHEVA, INDIA",
            "Incoterms": "CIF",
            "Description of Goods": "Laptop Computers and Accessories",
            "Gross Weight": "1250 KGS",
            "Net Weight": "1100 KGS",
            "Total Value": "USD 45,000",
        })

        # Mismatch document — Incoterms FOB instead of CIF (critical mismatch for CUST001)
        make_doc("./data/sample_docs/sample_mismatch.pdf", {
            "Invoice Number": "INV-2024-002",
            "Consignee Name": "ACME IMPORTS LTD",
            "HS Code": "84713000",
            "Port of Loading": "SHANGHAI, CHINA",
            "Port of Discharge": "NHAVA SHEVA, INDIA",
            "Incoterms": "FOB",  # <- mismatch
            "Description of Goods": "Laptop Computers",
            "Gross Weight": "1100 KGS",
            "Net Weight": "980 KGS",
            "Total Value": "USD 38,000",
        })

        # Messy document — missing fields + low quality simulation
        make_doc("./data/sample_docs/sample_messy.pdf", {
            "Inv No": "INV-2024-003",  # abbreviated field names
            "Consignee": "ACME IMPORTS",  # incomplete name
            "HS": "8471",  # partial HS code
            "Loading Port": "SHANGHAI",
            "Discharge": "NHAVA SHEVA",
            # Incoterms missing entirely
            "Goods": "Computers",
            # Gross weight missing
            "Value": "USD 22,000",
        })

        print("\nAll sample documents generated in ./data/sample_docs/")
        return True

    except ImportError:
        return False


def generate_with_fpdf():
    try:
        from fpdf import FPDF

        def make_doc(filename, title, fields):
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", "B", 16)
            pdf.cell(0, 10, title, ln=True, align="C")
            pdf.ln(5)
            pdf.set_font("Arial", size=10)
            for label, value in fields.items():
                pdf.set_font("Arial", "B", 9)
                pdf.cell(60, 8, f"{label}:", border=0)
                pdf.set_font("Arial", size=9)
                pdf.cell(0, 8, str(value), ln=True)
            pdf.output(filename)
            print(f"Generated: {filename}")

        make_doc("./data/sample_docs/sample_clean.pdf", "COMMERCIAL INVOICE", {
            "Invoice Number": "INV-2024-001",
            "Consignee Name": "ACME IMPORTS LTD",
            "HS Code": "84713000",
            "Port of Loading": "SHANGHAI, CHINA",
            "Port of Discharge": "NHAVA SHEVA, INDIA",
            "Incoterms": "CIF",
            "Description of Goods": "Laptop Computers and Accessories",
            "Gross Weight": "1250 KGS",
        })

        make_doc("./data/sample_docs/sample_mismatch.pdf", "COMMERCIAL INVOICE", {
            "Invoice Number": "INV-2024-002",
            "Consignee Name": "ACME IMPORTS LTD",
            "HS Code": "84713000",
            "Port of Loading": "SHANGHAI, CHINA",
            "Port of Discharge": "NHAVA SHEVA, INDIA",
            "Incoterms": "FOB",
            "Description of Goods": "Laptop Computers",
            "Gross Weight": "1100 KGS",
        })

        make_doc("./data/sample_docs/sample_messy.pdf", "INVOICE", {
            "Inv No": "INV-2024-003",
            "Consignee": "ACME IMPORTS",
            "HS": "8471",
            "Loading": "SHANGHAI",
            "Discharge": "NHAVA SHEVA",
            "Goods": "Computers",
        })

        print("\nAll sample documents generated.")
        return True
    except ImportError:
        return False


def generate_multi_transaction_batch():
    """Generate a batch of related documents simulating a multi-transaction pipeline."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import cm

        def make_doc(filename, fields):
            c = canvas.Canvas(filename, pagesize=A4)
            width, height = A4
            c.setFont("Helvetica-Bold", 16)
            c.drawString(2*cm, height - 2*cm, "COMMERCIAL INVOICE")
            c.setFont("Helvetica", 10)
            y = height - 3.5*cm
            for label, value in fields.items():
                c.setFont("Helvetica-Bold", 9)
                c.drawString(2*cm, y, f"{label}:")
                c.setFont("Helvetica", 9)
                c.drawString(7*cm, y, str(value))
                y -= 0.7*cm
            c.save()
            print(f"Generated: {filename}")

        # Transaction Batch 1 (3 related shipments)
        os.makedirs("./data/sample_docs/batch_001", exist_ok=True)
        make_doc("./data/sample_docs/batch_001/invoice_A.pdf", {
            "Invoice Number": "INV-2024-BATCH001-A",
            "Consignee Name": "ACME IMPORTS LTD",
            "HS Code": "84713000",
            "Port of Loading": "SHANGHAI, CHINA",
            "Port of Discharge": "NHAVA SHEVA, INDIA",
            "Incoterms": "CIF",
            "Description of Goods": "Laptops - Shipment A",
            "Gross Weight": "500 KGS",
            "Total Value": "USD 15,000",
        })

        make_doc("./data/sample_docs/batch_001/invoice_B.pdf", {
            "Invoice Number": "INV-2024-BATCH001-B",
            "Consignee Name": "ACME IMPORTS LTD",
            "HS Code": "84713000",
            "Port of Loading": "SHANGHAI, CHINA",
            "Port of Discharge": "NHAVA SHEVA, INDIA",
            "Incoterms": "CIF",
            "Description of Goods": "Laptops - Shipment B",
            "Gross Weight": "450 KGS",
            "Total Value": "USD 13,500",
        })

        make_doc("./data/sample_docs/batch_001/invoice_C.pdf", {
            "Invoice Number": "INV-2024-BATCH001-C",
            "Consignee Name": "ACME IMPORTS LTD",
            "HS Code": "84713000",
            "Port of Loading": "SHANGHAI, CHINA",
            "Port of Discharge": "NHAVA SHEVA, INDIA",
            "Incoterms": "CIF",
            "Description of Goods": "Laptops - Shipment C",
            "Gross Weight": "300 KGS",
            "Total Value": "USD 9,000",
        })

        # Transaction Batch 2 (mixed quality — testing error handling)
        os.makedirs("./data/sample_docs/batch_002", exist_ok=True)
        make_doc("./data/sample_docs/batch_002/invoice_good.pdf", {
            "Invoice Number": "INV-2024-BATCH002-GOOD",
            "Consignee Name": "GLOBAL TRADE CORP",
            "HS Code": "87042190",
            "Port of Loading": "HONG KONG",
            "Port of Discharge": "PORT SAID, EGYPT",
            "Incoterms": "CIF",
            "Description of Goods": "Auto Parts",
            "Gross Weight": "2000 KGS",
            "Total Value": "USD 55,000",
        })

        make_doc("./data/sample_docs/batch_002/invoice_mismatch.pdf", {
            "Invoice Number": "INV-2024-BATCH002-ERR",
            "Consignee Name": "GLOBAL TRADE CORP",
            "HS Code": "87042190",
            "Port of Loading": "HONG KONG",
            "Port of Discharge": "SINGAPORE",  # Different discharge port
            "Incoterms": "FOB",  # Wrong incoterms
            "Description of Goods": "Auto Parts",
            "Gross Weight": "1800 KGS",
            "Total Value": "USD 48,000",
        })

        print("\nMulti-transaction batches generated in ./data/sample_docs/batch_*/")
        return True

    except ImportError:
        return False


if __name__ == "__main__":
    if not generate_with_reportlab():
        if not generate_with_fpdf():
            print("Neither reportlab nor fpdf available.")
            print("Install one: pip install reportlab  OR  pip install fpdf2")
        else:
            generate_multi_transaction_batch()
    else:
        generate_multi_transaction_batch()
