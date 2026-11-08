"""Tesseract discovery tests, without assuming a Windows/Linux install path."""

from pathlib import Path
import pytesseract
from fakturama import ocr


def test_explicit_path_configures_pytesseract(monkeypatch, tmp_path):
    fake = tmp_path / "tesseract.exe"
    fake.write_bytes(b"test-only placeholder")
    previous = pytesseract.pytesseract.tesseract_cmd
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", previous)
    assert ocr.configure_tesseract(fake) == str(fake)
    assert pytesseract.pytesseract.tesseract_cmd == str(fake)


def test_env_var_configures_pytesseract(monkeypatch, tmp_path):
    fake = tmp_path / "tesseract.exe"
    fake.write_bytes(b"test-only placeholder")
    monkeypatch.setenv("TESSERACT_CMD", str(fake))
    previous = pytesseract.pytesseract.tesseract_cmd
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", previous)
    assert ocr.configure_tesseract() == str(fake)
    assert pytesseract.pytesseract.tesseract_cmd == str(fake)
