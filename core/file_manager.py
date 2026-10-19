"""Remote File System Manager supporting drive enumeration, file browsing, upload, download, and folder ZIP archiving."""

import io
import os
import platform
import shutil
import string
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil


class FileManager:
    """Provides cross-platform remote file system operations including RustDesk-style folder ZIP archives."""

    def __init__(self):
        self.is_windows = platform.system() == "Windows"

    def get_drives_and_shortcuts(self) -> Dict[str, Any]:
        """Return list of available storage drives and standard user directories."""
        drives = []
        try:
            partitions = psutil.disk_partitions(all=False)
            for part in partitions:
                drive_info = {
                    "path": part.mountpoint,
                    "label": part.mountpoint,
                    "fstype": part.fstype,
                }
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    drive_info["total"] = usage.total
                    drive_info["free"] = usage.free
                    drive_info["used"] = usage.used
                    drive_info["percent"] = usage.percent
                except Exception:
                    pass
                drives.append(drive_info)
        except Exception:
            if self.is_windows:
                for letter in string.ascii_uppercase:
                    drive_path = f"{letter}:\\"
                    if os.path.exists(drive_path):
                        drives.append({"path": drive_path, "label": drive_path, "fstype": "NTFS"})
            else:
                drives.append({"path": "/", "label": "Root (/)", "fstype": "ext4"})

        home = Path.home()
        shortcuts = [
            {"name": "Home", "path": str(home.resolve())},
            {"name": "Desktop", "path": str((home / "Desktop").resolve()) if (home / "Desktop").exists() else str(home)},
            {"name": "Downloads", "path": str((home / "Downloads").resolve()) if (home / "Downloads").exists() else str(home)},
            {"name": "Documents", "path": str((home / "Documents").resolve()) if (home / "Documents").exists() else str(home)},
        ]

        return {
            "drives": drives,
            "shortcuts": shortcuts,
            "default_path": str(home.resolve()),
        }

    def list_dir(self, directory_path: Optional[str] = None) -> Dict[str, Any]:
        """List contents of specified directory."""
        if not directory_path or directory_path.strip() == "":
            directory_path = str(Path.home().resolve())

        target_path = Path(directory_path).resolve()
        if not target_path.exists() or not target_path.is_dir():
            raise FileNotFoundError(f"Directory not found: {directory_path}")

        items = []
        try:
            with os.scandir(target_path) as entries:
                for entry in entries:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        is_dir = entry.is_dir(follow_symlinks=False)
                        items.append(
                            {
                                "name": entry.name,
                                "path": str(Path(entry.path).resolve()),
                                "is_dir": is_dir,
                                "size": stat.st_size if not is_dir else 0,
                                "modified": stat.st_mtime,
                                "extension": Path(entry.name).suffix.lower() if not is_dir else "",
                            }
                        )
                    except (PermissionError, OSError):
                        continue
        except PermissionError:
            raise PermissionError(f"Access denied to directory: {directory_path}")

        # Sort folders first, then alphabetically
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

        parent_path = str(target_path.parent.resolve()) if target_path.parent != target_path else None

        return {
            "current_path": str(target_path),
            "parent_path": parent_path,
            "items": items,
            "total_items": len(items),
        }

    def get_desktop_dir(self) -> str:
        """Return the user desktop path for dropzone uploads."""
        desktop = Path.home() / "Desktop"
        if desktop.exists():
            return str(desktop.resolve())
        return str(Path.home().resolve())

    def save_uploaded_file(self, target_folder: str, filename: str, content: bytes) -> str:
        """Save an uploaded file safely into the target folder."""
        dest_folder = Path(target_folder).resolve()
        dest_folder.mkdir(parents=True, exist_ok=True)

        safe_filename = Path(filename).name
        target_file = dest_folder / safe_filename

        with open(target_file, "wb") as f:
            f.write(content)

        return str(target_file.resolve())

    def create_folder_zip_bytes(self, folder_path: str, max_bytes: int = 250 * 1024 * 1024) -> Tuple[bytes, str]:
        """Compress a folder into an in-memory .zip archive for one-click folder download."""
        target = Path(folder_path).resolve()
        if not target.exists() or not target.is_dir():
            raise FileNotFoundError(f"Directory not found: {folder_path}")

        buf = io.BytesIO()
        total_written = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, filenames in os.walk(target):
                for fname in filenames:
                    fpath = Path(root) / fname
                    try:
                        fsize = fpath.stat().st_size
                        if total_written + fsize > max_bytes:
                            break
                        arcname = str(fpath.relative_to(target))
                        zf.write(fpath, arcname=arcname)
                        total_written += fsize
                    except (PermissionError, OSError):
                        continue

        zip_filename = f"{target.name or 'folder'}.zip"
        return buf.getvalue(), zip_filename

    def delete_item(self, item_path: str) -> bool:
        """Delete a file or directory."""
        target = Path(item_path).resolve()
        if not target.exists():
            return False

        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True

    def create_dir(self, parent_folder: str, folder_name: str) -> str:
        """Create a new subdirectory."""
        parent = Path(parent_folder).resolve()
        new_dir = parent / Path(folder_name).name
        new_dir.mkdir(parents=True, exist_ok=True)
        return str(new_dir.resolve())

    def rename_item(self, source_path: str, new_name: str) -> str:
        """Rename a file or folder."""
        source = Path(source_path).resolve()
        if not source.exists():
            raise FileNotFoundError(f"Item not found: {source_path}")

        dest = source.parent / Path(new_name).name
        source.rename(dest)
        return str(dest.resolve())
