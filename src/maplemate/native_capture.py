"""Private worker: obtain a single WGC frame and exit. Output is a local pipe."""
from datetime import datetime, timezone
import io
import json
import queue
import sys
from PIL import Image
from .capture import CaptureError, game_window


def main() -> None:
    from windows_capture import WindowsCapture
    window = game_window()
    if not window:
        raise CaptureError("MapleStory window is missing or minimized.")
    result: queue.Queue = queue.Queue(maxsize=1)
    capture = WindowsCapture(cursor_capture=False, draw_border=True, monitor_index=None, window_hwnd=window.handle)

    @capture.event
    def on_frame_arrived(frame, capture_control):
        try:
            if frame.width * frame.height > 16_000_000:
                raise CaptureError("Frame is too large.")
            rgb = frame.frame_buffer[:, :, :3][:, :, ::-1].copy()
            buffer = io.BytesIO()
            Image.fromarray(rgb).save(buffer, format="PNG")
            result.put_nowait((datetime.now(timezone.utc).isoformat(), buffer.getvalue()))
        except Exception:
            if result.empty():
                result.put_nowait(None)
        finally:
            capture_control.stop()

    @capture.event
    def on_closed():
        if result.empty():
            result.put_nowait(None)

    control = capture.start_free_threaded()
    try:
        value = result.get(timeout=6)
        if value is None:
            raise CaptureError("Capture ended without a frame.")
    finally:
        control.stop()
    observed_at, png = value
    sys.stdout.buffer.write(json.dumps({"captured_at": observed_at}).encode("ascii") + b"\n" + png)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(1) from None
