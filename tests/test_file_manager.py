"""Unit tests for FileManager core service."""

import os
import tempfile
from pathlib import Path
import pytest
from core.file_manager import FileManager


def test_get_drives_and_shortcuts():
    """Verify drive enumeration and user folder shortcuts."""
    fm = FileManager()
    res = fm.get_drives_and_shortcuts()
    assert "drives" in res
    assert "shortcuts" in res
    assert len(res["shortcuts"]) >= 1
    assert "default_path" in res


def test_file_operations_crud():
    """Verify create folder, write file, list directory, rename, and delete."""
    fm = FileManager()
    with tempfile.TemporaryDirectory() as temp_dir:
        # 1. Create Folder
        subfolder = fm.create_dir(temp_dir, "TestFolder")
        assert Path(subfolder).exists()
        assert Path(subfolder).is_dir()

        # 2. Save Uploaded File
        content = b"Hello Remote Desktop File Manager"
        saved_file = fm.save_uploaded_file(subfolder, "test_doc.txt", content)
        assert Path(saved_file).exists()
        assert Path(saved_file).read_bytes() == content

        # 3. List Directory
        list_res = fm.list_dir(subfolder)
        assert list_res["total_items"] == 1
        assert list_res["items"][0]["name"] == "test_doc.txt"
        assert list_res["items"][0]["size"] == len(content)

        # 4. Rename File
        renamed_file = fm.rename_item(saved_file, "renamed_doc.txt")
        assert Path(renamed_file).exists()
        assert not Path(saved_file).exists()

        # 5. Delete Item
        assert fm.delete_item(renamed_file) is True
        assert not Path(renamed_file).exists()


def test_get_desktop_dir():
    """Verify desktop directory path resolution."""
    fm = FileManager()
    desktop = fm.get_desktop_dir()
    assert desktop is not None
    assert Path(desktop).exists()
