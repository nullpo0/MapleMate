"""Bound image tool responses, including Base64 and duplicated JSON metadata."""
import base64
import io
import json
import math
from PIL import Image
from mcp.types import CallToolResult, ImageContent, TextContent
from ..capture import CaptureError
from .models import CaptureRegion

# Stay below both decimal 1MB and 1MiB client limits, leaving wrapper headroom.
MAX_RESPONSE_BYTES = 900_000
MAX_IMAGE_BYTES = 650_000


def _result(metadata: dict, pixels: bytes, mime_type: str) -> CallToolResult:
    value = {"status": "ok", **metadata, "image_mime_type": mime_type, "image_bytes": len(pixels),
             "next_tool": "record_screen_observation", "image_read_by": "client_llm"}
    result = CallToolResult(
        content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False)),
                 ImageContent(type="image", mimeType=mime_type, data=base64.b64encode(pixels).decode("ascii"))],
        structuredContent=value)
    # Validate the real serialized result, not just the unencoded image size.
    if len(result.model_dump_json(by_alias=True).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise CaptureError("캡처 응답 크기를 제한 안에 맞추지 못했습니다. 필요한 부분만 region으로 요청해 주세요.")
    return result


def capture_tool_result(metadata: dict, png: bytes, region: CaptureRegion | None = None) -> CallToolResult:
    """Preserve small PNGs; prefer full-resolution JPEG before reducing dimensions."""
    try:
        with Image.open(io.BytesIO(png)) as original:
            source_width, source_height = original.size
            if original.format != "PNG" or not 1 <= source_width <= 8192 or not 1 <= source_height <= 8192 or source_width * source_height > 16_000_000:
                raise CaptureError("캡처 이미지 형식이나 크기가 올바르지 않습니다.")
            info = {**metadata, "original_width": source_width, "original_height": source_height,
                    "width": source_width, "height": source_height, "image_resized": False,
                    "image_quality": None, "capture_region": None}
            if region:
                if region.x + region.width > source_width or region.y + region.height > source_height:
                    raise CaptureError("region이 현재 게임 창 범위를 벗어났습니다. 최신 original_width·original_height를 확인해 주세요.")
                image = original.crop((region.x, region.y, region.x + region.width, region.y + region.height))
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                png = buffer.getvalue()
                info.update(width=region.width, height=region.height, capture_region=region.model_dump())
            elif len(png) <= MAX_IMAGE_BYTES:
                return _result(info, png, "image/png")
            else:
                image = original.copy()
            if len(png) <= MAX_IMAGE_BYTES:
                return _result(info, png, "image/png")
            image = image.convert("RGB")
            width, height = image.size
            current = image
            for _ in range(10):
                for quality in (95, 90, 85, 80):
                    buffer = io.BytesIO()
                    # No chroma subsampling: preserve colored UI text and edges.
                    current.save(buffer, format="JPEG", quality=quality, subsampling=0)
                    pixels = buffer.getvalue()
                    if len(pixels) <= MAX_IMAGE_BYTES:
                        info.update(width=current.width, height=current.height,
                                    image_quality=quality, image_resized=current.size != image.size)
                        return _result(info, pixels, "image/jpeg")
                factor = min(0.85, math.sqrt(MAX_IMAGE_BYTES / len(pixels)) * 0.95)
                width, height = max(1, int(width * factor)), max(1, int(height * factor))
                current = image.resize((width, height), Image.Resampling.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise CaptureError("캡처 이미지를 전달용으로 변환하지 못했습니다. 게임 창을 확인하고 다시 요청해 주세요.") from None
    raise CaptureError("캡처 응답 크기를 제한 안에 맞추지 못했습니다. 필요한 부분만 region으로 요청해 주세요.")
