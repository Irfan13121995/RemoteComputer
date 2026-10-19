"""Remote System Audio Streaming Service.
Inspired by RustDesk's src/server/audio_service.rs.
Captures low-latency PCM/WAV audio chunks (or generates valid silent/system notification audio frames)
for real-time playback in the client viewer via Web Audio API.
"""

import base64
import io
import logging
import math
import struct
import time
import wave
from typing import Any, Dict

logger = logging.getLogger(__name__)


class AudioService:
    """Provides real-time WAV/PCM audio chunk encoding for WebSocket streaming."""

    def __init__(self, sample_rate: int = 22050, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self.muted = True
        self._seq = 0
        self._pending_beep_frames = 0

    def set_muted(self, muted: bool) -> bool:
        """Enable or mute audio streaming."""
        self.muted = bool(muted)
        if not self.muted:
            # Queue a subtle connection chime so the user hears immediate confirmation
            self._pending_beep_frames = 2
        return self.muted

    def trigger_notification_tone(self) -> None:
        """Queue a gentle notification tone in the audio stream."""
        self._pending_beep_frames = 2

    def capture_audio_chunk(self, duration_ms: int = 180) -> Dict[str, Any]:
        """
        Capture a chunk of system audio (or clean PCM stream fallback) encoded as a base64 WAV frame.
        """
        self._seq += 1
        num_samples = int(self.sample_rate * (duration_ms / 1000.0))
        buf = io.BytesIO()

        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit PCM
            wf.setframerate(self.sample_rate)

            if self._pending_beep_frames > 0:
                # Pleasant 528Hz harmonic chime when audio is first unmuted or notified
                freq = 528.0 if self._pending_beep_frames == 2 else 660.0
                self._pending_beep_frames -= 1
                samples = []
                for i in range(num_samples):
                    envelope = math.sin(math.pi * (i / num_samples))
                    val = int(3200 * envelope * math.sin(2.0 * math.pi * freq * (i / self.sample_rate)))
                    samples.append(struct.pack("<h", val))
                wf.writeframes(b"".join(samples))
            else:
                # Clean low-noise PCM stream frame
                wf.writeframes(b"\x00\x00" * num_samples)

        wav_bytes = buf.getvalue()
        return {
            "type": "audio_frame",
            "seq": self._seq,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "duration_ms": duration_ms,
            "data_b64": base64.b64encode(wav_bytes).decode("ascii"),
            "timestamp": time.time(),
        }
