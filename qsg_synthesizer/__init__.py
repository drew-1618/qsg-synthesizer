"""QSG Synthesizer."""

import os

# pymupdf4llm's layout analysis loads ONNX Runtime, which by default sends
# usage telemetry to Microsoft whenever a PDF is read. The app promises that
# nothing leaves the computer unless an AI provider is chosen, so switch it off
# before anything can import onnxruntime. Every entry point imports this
# package first. setdefault: an explicit ORT_DISABLE_TELEMETRY=0 still opts in.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
