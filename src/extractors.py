"""Turn an uploaded file into plain text.

One function per format, dispatched on extension by `extract_text`. Everything raises
`ExtractionError` with a message meant to be shown to the user verbatim — the UI never
has to interpret an exception type.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

from .config import MAX_FILE_BYTES, MAX_FILE_MB

SUPPORTED_EXTENSIONS = ("pdf", "txt", "csv", "xlsx")

#: Tried in order when a text file carries no usable encoding declaration.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


class ExtractionError(Exception):
    """Raised when a file cannot be turned into text. The message is user-facing."""


def extract_text(data: bytes, filename: str) -> str:
    """Extract plain text from an uploaded file's bytes.

    Args:
        data: Raw file contents.
        filename: Original name — only its extension is used, for dispatch.

    Returns:
        The extracted text, stripped of leading/trailing whitespace.

    Raises:
        ExtractionError: Unsupported extension, oversized file, corrupt file, or a file
            that parsed successfully but yielded no text (e.g. a scanned PDF).
    """
    if not data:
        raise ExtractionError("That file is empty.")

    if len(data) > MAX_FILE_BYTES:
        actual_mb = len(data) / (1024 * 1024)
        raise ExtractionError(
            f"That file is {actual_mb:.1f} MB, over the {MAX_FILE_MB} MB limit. "
            "Please upload a smaller file or paste the text directly."
        )

    suffix = Path(filename).suffix.lower().lstrip(".")
    extractors = {
        "pdf": _extract_pdf,
        "txt": _extract_txt,
        "csv": _extract_csv,
        "xlsx": _extract_xlsx,
    }
    if suffix not in extractors:
        raise ExtractionError(
            f"Cannot read '.{suffix}' files. Supported formats: "
            + ", ".join(f".{e}" for e in SUPPORTED_EXTENSIONS)
        )

    text = extractors[suffix](data).strip()
    if not text:
        raise ExtractionError(_empty_result_message(suffix))
    return text


def _empty_result_message(suffix: str) -> str:
    if suffix == "pdf":
        return (
            "No text could be extracted from this PDF. It is most likely a scan or "
            "image-only document — this app reads embedded text and does not perform OCR. "
            "Try a text-based PDF, or paste the text directly."
        )
    return "That file contains no readable text."


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"This PDF could not be opened — it may be corrupt or password-protected. ({exc})") from exc

    if reader.is_encrypted:
        # An empty user password is common and decrypts silently; anything else we cannot open.
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ExtractionError("This PDF is password-protected and cannot be read.") from exc

    pages = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            # One unreadable page shouldn't lose the whole document.
            pages.append(f"[page {number} could not be read]")
    return "\n\n".join(p for p in pages if p.strip())


def _extract_txt(data: bytes) -> str:
    return _decode(data)


def _decode(data: bytes) -> str:
    """Decode bytes by trying the common encodings in order.

    latin-1 is last and cannot fail, so this always returns rather than raising — a
    mangled character is a better outcome than refusing the file.
    """
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _extract_csv(data: bytes) -> str:
    try:
        frame = pd.read_csv(io.StringIO(_decode(data)))
    except pd.errors.EmptyDataError:
        return ""
    except Exception as exc:
        raise ExtractionError(f"This CSV could not be parsed: {exc}") from exc
    return _frame_to_text(frame)


def _extract_xlsx(data: bytes) -> str:
    try:
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, engine="openpyxl")
    except Exception as exc:
        raise ExtractionError(f"This Excel file could not be parsed: {exc}") from exc

    parts = []
    for name, frame in sheets.items():
        body = _frame_to_text(frame)
        if body:
            # Sheet names give the translation useful context when a workbook has several.
            parts.append(f"{name}\n{body}" if len(sheets) > 1 else body)
    return "\n\n".join(parts)


def _frame_to_text(frame: pd.DataFrame) -> str:
    """Render a table as readable lines.

    Cells are joined with ' | ' rather than dumped as CSV so the result reads as prose to
    the translation model, and blank cells are dropped instead of becoming 'nan'.
    """
    if frame.empty and not len(frame.columns):
        return ""

    lines = [" | ".join(str(c) for c in frame.columns if not str(c).startswith("Unnamed:"))]
    for row in frame.itertuples(index=False, name=None):
        cells = [str(value).strip() for value in row if pd.notna(value) and str(value).strip()]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(line for line in lines if line.strip())
