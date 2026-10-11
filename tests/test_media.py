import base64
import io
import json
import unittest
import numpy as np
from PIL import Image
from pydantic import ValidationError
from maplemate.capture import CaptureError
from maplemate.maple_mcp.media import MAX_RESPONSE_BYTES, capture_tool_result
from maplemate.maple_mcp.models import CaptureRegion


class ImageDeliveryTests(unittest.TestCase):
    metadata = {"screenshot_id": "a" * 32, "captured_at": "2026-10-11T00:00:00+00:00",
                "source": "windows_graphics_capture", "is_live": False, "delivery": "requested"}

    @staticmethod
    def png(image):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def check_result(self, result):
        response = {"jsonrpc": "2.0", "id": 1, "result": result.model_dump(mode="json", by_alias=True)}
        self.assertLess(len(json.dumps(response, ensure_ascii=False).encode("utf-8")), 1_000_000)
        self.assertLessEqual(len(result.model_dump_json(by_alias=True).encode("utf-8")), MAX_RESPONSE_BYTES)
        content = [block for block in result.content if block.type == "image"]
        self.assertEqual(len(content), 1)
        raw = base64.b64decode(content[0].data, validate=True)
        image = Image.open(io.BytesIO(raw))
        self.assertEqual(image.size, (result.structuredContent["width"], result.structuredContent["height"]))
        self.assertEqual(image.format, "PNG" if content[0].mimeType == "image/png" else "JPEG")
        self.assertEqual(result.structuredContent["screenshot_id"], self.metadata["screenshot_id"])
        self.assertEqual(result.structuredContent["captured_at"], self.metadata["captured_at"])
        self.assertFalse(result.structuredContent["is_live"])
        return raw, image

    def test_small_png_is_kept_exactly_without_quality_loss(self):
        original = self.png(Image.new("RGB", (640, 480), "green"))
        result = capture_tool_result(self.metadata, original)
        raw, _ = self.check_result(result)
        self.assertEqual(raw, original)
        self.assertFalse(result.structuredContent["image_resized"])

    def test_large_png_can_be_delivered_at_full_resolution_as_jpeg(self):
        image = Image.fromarray(np.random.default_rng(7).integers(0, 256, (480, 720, 3), dtype=np.uint8))
        original = self.png(image)
        self.assertGreater(len(base64.b64encode(original)), 1_000_000)
        result = capture_tool_result(self.metadata, original)
        _, decoded = self.check_result(result)
        self.assertEqual(decoded.size, image.size)
        self.assertFalse(result.structuredContent["image_resized"])
        self.assertEqual(result.structuredContent["image_mime_type"], "image/jpeg")

    def test_noisy_4k_frame_is_resized_to_fit_complete_tool_result(self):
        image = Image.fromarray(np.random.default_rng(11).integers(0, 256, (2160, 3840, 3), dtype=np.uint8))
        result = capture_tool_result(self.metadata, self.png(image))
        self.check_result(result)
        value = result.structuredContent
        self.assertTrue(value["image_resized"])
        self.assertEqual((value["original_width"], value["original_height"]), image.size)
        self.assertAlmostEqual(value["width"] / value["height"], image.width / image.height, delta=0.01)
        self.assertIsNone(value["capture_region"])

    def test_region_preserves_original_pixels_and_reports_original_coordinates(self):
        image = Image.fromarray(np.random.default_rng(3).integers(0, 256, (600, 900, 3), dtype=np.uint8))
        region = CaptureRegion(x=100, y=200, width=300, height=90)
        result = capture_tool_result(self.metadata, self.png(image), region)
        _, decoded = self.check_result(result)
        self.assertEqual(decoded.tobytes(), image.crop((100, 200, 400, 290)).tobytes())
        self.assertEqual(result.structuredContent["capture_region"], region.model_dump())
        self.assertEqual(result.structuredContent["original_width"], 900)
        self.assertFalse(result.structuredContent["image_resized"])

    def test_invalid_region_or_image_is_rejected_without_oversized_output(self):
        original = self.png(Image.new("RGB", (100, 100), "green"))
        with self.assertRaises(CaptureError):
            capture_tool_result(self.metadata, original, CaptureRegion(x=90, y=0, width=20, height=20))
        with self.assertRaises(CaptureError):
            capture_tool_result(self.metadata, b"not an image")
        with self.assertRaises(CaptureError):
            capture_tool_result({**self.metadata, "extra": "x" * 1_000_000}, original)
        for bad in ({"x": -1, "y": 0, "width": 10, "height": 10},
                    {"x": True, "y": 0, "width": 10, "height": 10},
                    {"x": 0, "y": 0, "width": 0, "height": 10}):
            with self.assertRaises(ValidationError):
                CaptureRegion(**bad)
