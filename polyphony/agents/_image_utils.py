"""Shared image encoding utilities for LLM agent implementations."""

from __future__ import annotations

import base64


EXT_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def encode_image_base64(path: str) -> str:
    """Read an image file and return its base64-encoded content."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
