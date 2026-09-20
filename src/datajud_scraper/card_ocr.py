"""Local, pinned OCR for photographed contracts and positional privacy masks."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import urllib.request
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from .ocr_models import MODEL_ROOT

NEURAL_ROOT = Path.home() / ".local/share/datajud-scraper/rapidocr"
NEURAL_MODELS = {
    "Det": (
        "PP-OCRv6/det/PP-OCRv6_det_small.onnx",
        "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    ),
    "Cls": (
        "PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    ),
    "Rec": (
        "PP-OCRv5/rec/latin_PP-OCRv5_rec_mobile.onnx",
        "b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a",
    ),
}


def transform_layout_words(words, region):
    """Place normalized words and their glyphs inside a normalized parent region."""

    def transform(box):
        return [
            region[0] + box[0] * (region[2] - region[0]),
            region[1] + box[1] * (region[3] - region[1]),
            region[0] + box[2] * (region[2] - region[0]),
            region[1] + box[3] * (region[3] - region[1]),
        ]

    result = []
    for word in words:
        item = [*transform(word[:4]), word[4]]
        if len(word) > 5 and isinstance(word[5], dict) and word[5].get("glyphs"):
            item.append({**word[5], "glyphs": [transform(g) for g in word[5]["glyphs"]]})
        result.append(item)
    return result


def merge_reading_words(primary, supplemental):
    """Insert missing OCR words without reordering already resolved slanted lines."""
    words = list(primary)
    for word in supplemental:
        area = (word[2] - word[0]) * (word[3] - word[1])
        if any(
            max(0, min(w[2], word[2]) - max(w[0], word[0]))
            * max(0, min(w[3], word[3]) - max(w[1], word[1]))
            > 0.5 * area
            for w in words
        ):
            continue
        center = (word[1] + word[3]) / 2
        position = len(words)
        for i, current in enumerate(words):
            distance = (current[1] + current[3]) / 2 - center
            tolerance = 0.6 * min(word[3] - word[1], current[3] - current[1])
            if distance > tolerance or abs(distance) <= tolerance and current[0] > word[0]:
                position = i
                break
        words.insert(position, word)
    return words


def neural_configuration():
    return {
        "engine": "rapidocr",
        "packages": {
            name: version(name) for name in ("rapidocr", "onnxruntime", "numpy", "opencv-python")
        },
        "models": {Path(path).name: digest for path, digest in NEURAL_MODELS.values()},
        "unclip_ratio": 1.2,
        "max_side_len": 2000,
        "intra_threads": 8,
        "inter_threads": 2,
        "word_boxes": "character_aligned_v2",
    }


def install_neural_models(root=NEURAL_ROOT):
    """Download public model weights only; inference never downloads or uploads data."""
    root.mkdir(parents=True, exist_ok=True)
    for relative, digest in NEURAL_MODELS.values():
        path = root / Path(relative).name
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            continue
        url = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/" + relative
        with urllib.request.urlopen(url, timeout=60) as response:
            content = response.read(30_000_001)
        if len(content) > 30_000_000 or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("la huella del modelo OCR neuronal no coincide")
        temporary = path.with_suffix(".part")
        temporary.write_bytes(content)
        temporary.replace(path)


@lru_cache(maxsize=1)
def neural_engine():
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    config = neural_configuration()
    params = {
        "Rec.lang_type": LangRec.LATIN,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.MOBILE,
        "Global.max_side_len": config["max_side_len"],
        "Global.log_level": "error",
        "EngineConfig.onnxruntime.intra_op_num_threads": config["intra_threads"],
        "EngineConfig.onnxruntime.inter_op_num_threads": config["inter_threads"],
    }
    for task, (relative, digest) in NEURAL_MODELS.items():
        path = NEURAL_ROOT / Path(relative).name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(
                "OCR neuronal no preparado: ejecutar python -m datajud_scraper.card_ocr"
            )
        # Explicit verified paths prevent the library from fetching weights at runtime.
        params[f"{task}.model_path"] = str(path)
    return RapidOCR(params=params)


def neural_words(image):
    import numpy as np

    result = neural_engine()(
        np.array(image.convert("RGB")),
        return_word_box=True,
        return_single_char_box=True,
        unclip_ratio=1.2,
    )
    if result.txts is None:
        return []  # Segmentation and visual privacy checks still inspect these pixels.
    if not result.word_results:
        raise RuntimeError("OCR neuronal devolvio texto sin coordenadas")
    words = []
    for text, line in zip(result.txts, result.word_results, strict=True):
        characters = []
        for character, _confidence, polygon in line:
            points = np.asarray(polygon, dtype=float)
            if points.shape != (4, 2) or not np.isfinite(points).all():
                raise RuntimeError("coordenadas OCR neuronales invalidas")
            left, top = points.min(axis=0) / (image.width, image.height)
            right, bottom = points.max(axis=0) / (image.width, image.height)
            rect = [
                max(0, float(left)),
                max(0, float(top)),
                min(1, float(right)),
                min(1, float(bottom)),
            ]
            if len(character) != 1:
                raise RuntimeError("OCR neuronal no devolvio coordenadas por caracter")
            characters.append((character, rect))
        if "".join(c for c, _ in characters) != "".join(text.split()):
            raise RuntimeError("texto OCR y posiciones de caracteres no coinciden")
        cursor = 0
        for match in re.finditer(r"\S+", text):
            token = match.group()
            glyphs = [r for _, r in characters[cursor : cursor + len(token)]]
            cursor += len(token)
            rect = [
                min(r[0] for r in glyphs),
                min(r[1] for r in glyphs),
                max(r[2] for r in glyphs),
                max(r[3] for r in glyphs),
            ]
            if rect[0] < rect[2] and rect[1] < rect[3]:
                words.append([*rect, token, {"glyphs": glyphs}])
    return words


def sparse_words(image, *, quality="best", segmentation=11):
    local = Path.home() / ".local/opt/datajud-tesseract"
    binary = shutil.which("tesseract") or str(local / "usr/bin/tesseract")
    if not Path(binary).is_file():
        raise RuntimeError("OCR de tablas no instalado: ejecutar scripts/setup_table_ocr.py")
    if quality not in ("fast", "best") or segmentation not in (3, 6, 11):
        raise ValueError("configuracion OCR invalida")
    env = dict(os.environ)
    env["OMP_THREAD_LIMIT"] = "1"
    if Path(binary).is_relative_to(local):
        env["LD_LIBRARY_PATH"] = str(local / "usr/lib/x86_64-linux-gnu")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = subprocess.run(
        [
            binary,
            "stdin",
            "stdout",
            "--tessdata-dir",
            str(MODEL_ROOT / quality),
            "-l",
            "por+eng",
            "--psm",
            str(segmentation),
            "--dpi",
            "240",
            "-c",
            "tessedit_create_tsv=1",
        ],
        input=buffer.getvalue(),
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("fallo el OCR de tablas; no se acepta como pagina vacia")
    if not result.stdout.startswith(b"level\tpage_num\t"):
        raise RuntimeError("el motor OCR no devolvio TSV; no se acepta como pagina vacia")
    words = []
    for row in csv.DictReader(
        io.StringIO(result.stdout.decode("utf8")), delimiter="\t", quoting=csv.QUOTE_NONE
    ):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        x, y, width, height = (int(row[k]) for k in ("left", "top", "width", "height"))
        if width <= 0 or height <= 0:
            continue
        words.append(
            [
                x / image.width,
                y / image.height,
                (x + width) / image.width,
                (y + height) / image.height,
                row["text"],
            ]
        )
    return words


if __name__ == "__main__":
    install_neural_models()
    neural_engine()
    print(json.dumps(neural_configuration(), indent=2))
