import pytest

from worker.tools import files


def test_reads_pdf_text():
    text = files.read_file("inbox/scan_0012.pdf")
    assert "HCS-INV-5521" in text and "1,24,000.00" in text


def test_accepts_workspace_prefixed_path():
    assert "Approval required" in files.read_file("company_data/policies.md")


def test_refuses_paths_outside_workspace():
    with pytest.raises(PermissionError):
        files.read_file("../.env")
