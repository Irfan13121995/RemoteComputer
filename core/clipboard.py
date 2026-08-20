"""Virtual clipboard synchronization manager."""

import logging
from typing import Optional

try:
    import pyperclip
except ImportError:
    pyperclip = None

logger = logging.getLogger(__name__)


class ClipboardManager:
    """Provides cross-platform clipboard reading and writing."""

    def __init__(self):
        self._last_content: str = ""

    def get_text(self) -> str:
        """Get current text content from the host clipboard."""
        if not pyperclip:
            return self._last_content
        try:
            content = pyperclip.paste()
            if isinstance(content, str):
                self._last_content = content
                return content
        except Exception as e:
            logger.debug(f"Failed to read host clipboard: {e}")
        return self._last_content

    def set_text(self, text: str) -> bool:
        """Set text content onto the host clipboard."""
        if not isinstance(text, str):
            return False
        self._last_content = text
        if not pyperclip:
            return True
        try:
            pyperclip.copy(text)
            return True
        except Exception as e:
            logger.debug(f"Failed to write to host clipboard: {e}")
            return False

    def has_changed(self) -> Optional[str]:
        """Check if clipboard changed externally on host. Returns new content or None."""
        current = self.get_text()
        if current != self._last_content:
            self._last_content = current
            return current
        return None
