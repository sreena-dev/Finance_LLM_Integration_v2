"""Tests for backend/tools/input_legacy_formats.py's PasswordProtectedError
addition (ingestion error catalog Section 1 -- file/upload level)."""

import io

import openpyxl
import pytest

msoffcrypto = pytest.importorskip("msoffcrypto")

from modes.trial_balance.pipeline.tools.input_legacy_formats import (  # noqa: E402
    PasswordProtectedError,
    UnsupportedWorkbookFormatError,
    _sniff_format,
    load_all_sheets_any_format,
)


def _write_plain_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["GL Code", "GL Name", "Closing Balance"])
    ws.append(["1001", "Cash", 1000])
    wb.save(path)


def test_password_protected_xlsx_raises_friendly_error(tmp_path):
    plain_path = tmp_path / "plain.xlsx"
    _write_plain_workbook(plain_path)

    from msoffcrypto.format.ooxml import OOXMLFile

    with open(plain_path, "rb") as f:
        plain_bytes = f.read()

    enc_out = io.BytesIO()
    OOXMLFile(io.BytesIO(plain_bytes)).encrypt("secretpass", enc_out)
    enc_path = tmp_path / "encrypted.xlsx"
    enc_path.write_bytes(enc_out.getvalue())

    with pytest.raises(PasswordProtectedError):
        _sniff_format(enc_path)

    with pytest.raises(PasswordProtectedError):
        load_all_sheets_any_format(enc_path)


def test_plain_xlsx_is_not_flagged_as_password_protected(tmp_path):
    plain_path = tmp_path / "plain.xlsx"
    _write_plain_workbook(plain_path)

    fmt = _sniff_format(plain_path)  # must not raise
    assert fmt is not None


def test_corrupted_file_still_raises_unsupported_format_not_password(tmp_path):
    path = tmp_path / "garbage.xlsx"
    path.write_bytes(b"this is not a real workbook at all")

    with pytest.raises(UnsupportedWorkbookFormatError):
        _sniff_format(path)
