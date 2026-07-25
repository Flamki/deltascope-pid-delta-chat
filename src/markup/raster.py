from __future__ import annotations

import fitz


OVERLAY_INK_COLORS = {"red", "green"}


def colorize_ink(pixmap: fitz.Pixmap, color: str) -> fitz.Pixmap:
    """Map dark drawing ink to a revision color while preserving a white page."""
    if color not in OVERLAY_INK_COLORS:
        raise ValueError(f"Unsupported overlay ink color: {color}")
    if pixmap.n < 3:
        pixmap = fitz.Pixmap(fitz.csRGB, pixmap)

    source = pixmap.samples
    channels = pixmap.n
    output = bytearray(pixmap.width * pixmap.height * 3)
    target_index = 0
    for source_index in range(0, len(source), channels):
        red, green, blue = source[source_index : source_index + 3]
        gray = (77 * red + 150 * green + 29 * blue) >> 8
        if color == "red":
            output[target_index] = 255
            output[target_index + 1] = gray
            output[target_index + 2] = gray
        else:
            green_channel = 165 + (gray * 90) // 255
            output[target_index] = gray
            output[target_index + 1] = green_channel
            output[target_index + 2] = gray
        target_index += 3

    return fitz.Pixmap(fitz.csRGB, pixmap.width, pixmap.height, bytes(output), False)
