"""Install pinned Tesseract language data for PyMuPDF's integrated local OCR."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path

MODEL_ROOT = Path.home() / ".local/share/datajud-scraper/tessdata"


def install_models(root: Path, quality: str) -> list[dict]:
    manifest = json.loads(Path(__file__).with_suffix(".json").read_text())
    installed = []
    for model in manifest:
        if model["quality"] != quality:
            continue
        folder = root / quality
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{model['language']}.traineddata"
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != model["sha256"]:
            with urllib.request.urlopen(model["url"], timeout=60) as response:
                content = response.read(model["size_bytes"] + 1)
            if (
                len(content) != model["size_bytes"]
                or hashlib.sha256(content).hexdigest() != model["sha256"]
            ):
                raise ValueError("la huella o el tamano del modelo OCR no coincide")
            temporary = path.with_suffix(".part")
            temporary.write_bytes(content)
            os.replace(temporary, path)
        installed.append({"path": str(path), "sha256": model["sha256"]})
    return installed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Modelos OCR locales fijados por SHA-256.")
    parser.add_argument("--quality", choices=("fast", "best"), default="fast")
    parser.add_argument("--destination", type=Path, default=MODEL_ROOT)
    args = parser.parse_args(argv)
    print(json.dumps(install_models(args.destination, args.quality), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
