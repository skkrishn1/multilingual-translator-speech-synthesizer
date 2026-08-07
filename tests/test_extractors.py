import pytest

from src.config import MAX_FILE_BYTES
from src.extractors import ExtractionError, extract_text
from tests.conftest import make_blank_pdf, make_pdf


class TestTxt:
    def test_utf8(self):
        assert extract_text("Hello world".encode("utf-8"), "note.txt") == "Hello world"

    def test_non_latin_script_survives_round_trip(self):
        text = "வணக்கம் உலகம்"
        assert extract_text(text.encode("utf-8"), "tamil.txt") == text

    def test_bom_is_stripped(self):
        assert extract_text("Hello".encode("utf-8-sig"), "bom.txt") == "Hello"

    def test_cp1252_falls_back(self):
        # 0x93/0x94 are smart quotes in cp1252 and invalid UTF-8.
        assert "quoted" in extract_text(b"\x93quoted\x94", "legacy.txt")


class TestPdf:
    def test_extracts_embedded_text(self):
        assert "Hello from a PDF" in extract_text(make_pdf("Hello from a PDF"), "doc.pdf")

    def test_image_only_pdf_explains_no_ocr(self):
        with pytest.raises(ExtractionError, match="OCR"):
            extract_text(make_blank_pdf(), "scan.pdf")

    def test_corrupt_pdf_raises_extraction_error(self):
        with pytest.raises(ExtractionError):
            extract_text(b"%PDF-1.4 this is not really a pdf", "broken.pdf")


class TestCsv:
    def test_headers_and_rows(self):
        result = extract_text(b"Product,Note\nTea,Hot drink\n", "table.csv")
        assert "Product" in result
        assert "Tea | Hot drink" in result

    def test_blank_cells_do_not_become_nan(self):
        result = extract_text(b"A,B\nvalue,\n", "sparse.csv")
        assert "nan" not in result.lower()


class TestXlsx:
    def test_reads_cells(self, xlsx_bytes):
        result = extract_text(xlsx_bytes, "book.xlsx")
        assert "Coffee" in result
        assert "Hot drink" in result


class TestGuards:
    def test_rejects_unknown_extension(self):
        with pytest.raises(ExtractionError, match=r"\.docx"):
            extract_text(b"content", "report.docx")

    def test_rejects_empty_file(self):
        with pytest.raises(ExtractionError, match="empty"):
            extract_text(b"", "empty.txt")

    def test_rejects_oversized_file(self):
        with pytest.raises(ExtractionError, match="limit"):
            extract_text(b"x" * (MAX_FILE_BYTES + 1), "huge.txt")

    def test_whitespace_only_file_is_rejected(self):
        with pytest.raises(ExtractionError):
            extract_text(b"   \n\n  ", "blank.txt")

    def test_extension_matching_is_case_insensitive(self):
        assert extract_text(b"Hello", "NOTE.TXT") == "Hello"
