"""
QR code image generation. Pure image rendering - no PocketBase writes
here; the caller (main.py's /qr endpoint) is responsible for deciding
whether to persist the result to the qr_codes collection.
"""
from __future__ import annotations

import io

import qrcode
from qrcode.image.pil import PilImage


def generate_qr_png(data: str, box_size: int = 10, border: int = 2) -> bytes:
    """
    Renders `data` (almost always a short link URL, e.g.
    https://mm.synkra.co.za/r/a7k2m9) as a PNG QR code and returns the
    raw image bytes.

    border=2 rather than the qrcode library's default of 4 - the
    default leaves a lot of dead white space when this gets printed
    small on a table card or sticker. 2 modules is still comfortably
    within the QR spec's quiet-zone recommendation for reliable scanning.
    """
    qr = qrcode.QRCode(
        version=None,  # let the library pick the minimum size for the data
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(image_factory=PilImage, fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
