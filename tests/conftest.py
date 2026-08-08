"""Shared fixtures. Everything is built in memory — the suite touches no network."""

from __future__ import annotations

import io

import pandas as pd
import pytest


def make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF containing `text` in an embedded font.

    Handwritten rather than generated with reportlab so the test suite needs no extra
    dependency. Keep `text` free of parentheses and backslashes — PDF string syntax.
    """
    content = f"BT /F1 24 Tf 40 700 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_position = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_position}\n%%EOF\n"
    ).encode()
    return bytes(out)


def make_blank_pdf() -> bytes:
    """A page with no text object — stands in for a scanned, image-only PDF."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_position = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_position}\n%%EOF\n"
    ).encode()
    return bytes(out)


@pytest.fixture
def xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame({"Product": ["Tea", "Coffee"], "Note": ["Hot drink", "Also hot"]}).to_excel(
        buffer, index=False, engine="openpyxl"
    )
    return buffer.getvalue()


@pytest.fixture
def no_api_key(monkeypatch):
    """Guarantee the key looks absent, whatever the developer's .env holds."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("src.translator.get_api_key", lambda: None)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Make retry backoff instant so the suite stays fast."""
    monkeypatch.setattr("time.sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def fresh_model_availability():
    """The translator remembers exhausted models process-wide; isolate each test from that."""
    from src.translator import reset_model_availability

    reset_model_availability()
    yield
    reset_model_availability()
