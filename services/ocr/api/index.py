from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR


ENGINE = None
MAX_IMAGE_BYTES = 4 * 1024 * 1024


class handler(BaseHTTPRequestHandler):
    server_version = "DeltaScope-OCR/1.0"

    def send_json(self, payload: dict, status: int = 200):
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        self.send_json({"status": "ok", "engine": "rapidocr-onnxruntime"})

    def do_POST(self):
        expected = os.getenv("OCR_SERVICE_TOKEN", "")
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not expected or not hmac.compare_digest(expected, supplied):
            return self.send_json({"error": "Unauthorized."}, 401)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.send_json({"error": "Invalid content length."}, 400)
        if length <= 0 or length > MAX_IMAGE_BYTES:
            return self.send_json({"error": "Image is empty or exceeds 4 MB."}, 413)
        encoded = np.frombuffer(self.rfile.read(length), dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None:
            return self.send_json({"error": "Invalid image."}, 422)
        global ENGINE
        if ENGINE is None:
            ENGINE = RapidOCR()
        results, elapsed = ENGINE(image)
        serializable = [
            [
                [[float(x), float(y)] for x, y in points],
                str(text),
                float(confidence),
            ]
            for points, text, confidence in (results or [])
        ]
        self.send_json(
            {
                "results": serializable,
                "elapsed_seconds": (
                    [float(value) for value in elapsed]
                    if isinstance(elapsed, (list, tuple))
                    else float(elapsed or 0)
                ),
            }
        )
