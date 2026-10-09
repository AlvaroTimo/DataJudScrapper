"""Render approved contract regions and irreversibly replace approved sensitive pixels.

Rectangles are normalized coordinates in the displayed page / cropped contract page.
A new image-only PDF avoids carrying hidden source text, cropped-out content, attachment
streams, forms, links, annotations, or incremental revisions into a released artifact.
All mask decisions still require visual review.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import uuid
from pathlib import Path


def pixel_box(rect: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    if (
        len(rect) != 4
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in rect)
        or any(not math.isfinite(v) for v in rect)
        or not (0 <= rect[0] < rect[2] <= 1 and 0 <= rect[1] < rect[3] <= 1)
    ):
        raise ValueError("rectangulo normalizado invalido")
    return (
        math.floor(rect[0] * width),
        math.floor(rect[1] * height),
        math.ceil(rect[2] * width),
        math.ceil(rect[3] * height),
    )


def region_rotation(region: dict) -> int:
    """Clockwise quarter turns applied after cropping, before mask coordinates."""
    rotation = region.get("rotation", 0)
    if type(rotation) is not int or rotation not in (0, 90, 180, 270):
        raise ValueError("rotacion debe ser 0, 90, 180 o 270 grados enteros")
    return rotation


def render_contract_page(
    source, region: dict, *, dpi: int = 240, with_geometry=False, raster_source=False
):
    import pymupdf
    from PIL import Image

    rotation = region_rotation(region)
    page_number = region["page"]
    if type(page_number) is not int or not 1 <= page_number <= source.page_count:
        raise ValueError("pagina fuente fuera de rango")
    page = source[page_number - 1]
    if raster_source:
        if rotation or region.get("rect", [0, 0, 1, 1]) != [0, 0, 1, 1] or page.rotation:
            raise ValueError("a phase input must be a complete unrotated raster page")
        images = page.get_images()
        if len(images) != 1 or page.get_text().strip() or list(page.annots() or []):
            raise ValueError("invalid extracted raster contract")
        pixmap = pymupdf.Pixmap(source, images[0][0])
        if pixmap.n != 3:
            raise ValueError("extracted contract must contain RGB pixels")
    else:
        pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False, annots=True)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    source_size = image.size
    box = pixel_box(region.get("rect", [0, 0, 1, 1]), image.width, image.height)
    image = image.crop(box)
    if rotation:
        operation = {
            90: Image.Transpose.ROTATE_270,
            180: Image.Transpose.ROTATE_180,
            270: Image.Transpose.ROTATE_90,
        }[rotation]
        image = image.transpose(operation)
    if with_geometry:
        return image, {
            "source_size": list(source_size),
            "crop_box": list(box),
            "rotation": rotation,
            "output_size": list(image.size),
        }
    return image


def region_inventory(page, region, geometry):
    """Map source words and images to exactly the cropped/rotated privacy pixel space."""
    from .inventory import words_normalized

    source_width, source_height = geometry["source_size"]
    left, top, right, bottom = geometry["crop_box"]
    width, height = right - left, bottom - top
    rotation = geometry["rotation"]
    output_width, output_height = geometry["output_size"]

    def mapped(rect):
        x0, y0, x1, y1 = rect
        x0, x1 = max(left, x0 * source_width), min(right, x1 * source_width)
        y0, y1 = max(top, y0 * source_height), min(bottom, y1 * source_height)
        if x1 <= x0 or y1 <= y0:
            return None
        corners = [(x - left, y - top) for x in (x0, x1) for y in (y0, y1)]
        if rotation == 90:
            corners = [(height - y, x) for x, y in corners]
        elif rotation == 180:
            corners = [(width - x, height - y) for x, y in corners]
        elif rotation == 270:
            corners = [(y, width - x) for x, y in corners]
        return [
            min(x for x, _ in corners),
            min(y for _, y in corners),
            max(x for x, _ in corners),
            max(y for _, y in corners),
        ]

    words = []
    for word in words_normalized(page):
        rect = mapped(word[:4])
        if rect:
            words.append([*rect, word[4]])
    image_page = {**page, "words": [[*rect, ""] for rect in page.get("image_rects", [])]}
    images = [box for word in words_normalized(image_page) if (box := mapped(word[:4]))]
    text = " ".join(w[4] for w in words)
    return {
        **{k: v for k, v in page.items() if k not in ("_normalized_body", "_normalized_heading")},
        "width": output_width,
        "height": output_height,
        "rotation": 0,
        "words": words,
        "text": text,
        "text_chars": len(text),
        "native_text": None,
        "image_rects": images,
        "image_fraction": min(
            1.0, sum((r[2] - r[0]) * (r[3] - r[1]) for r in images) / (output_width * output_height)
        ),
        "source_region": region,
        "crop_geometry": geometry,
    }


def redact_pixels(image, masks: list[dict], *, decision_mode: str = "manual"):
    from PIL import ImageDraw

    if decision_mode not in ("manual", "automatic"):
        raise ValueError("modo de decision invalido")
    cleaned = image.copy()
    draw = ImageDraw.Draw(cleaned)
    applied = []
    for mask in masks:
        if not mask.get("category"):
            raise ValueError("la mascara no tiene categoria")
        if decision_mode == "manual" and mask.get("confirmed") is not True:
            raise ValueError("la mascara no tiene categoria y confirmacion manual")
        if decision_mode == "automatic" and mask.get("origin") != "automatic":
            raise ValueError("la mascara no tiene procedencia automatica")
        x0, y0, x1, y1 = pixel_box(mask["rect"], image.width, image.height)
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=(255, 255, 255))
        applied.append((x0, y0, x1, y1))
    return cleaned, applied


def write_cleaned_contract(
    source_path: Path,
    output_path: Path,
    regions: list[dict],
    masks_by_page: dict[int, list[dict]],
    *,
    dpi: int = 240,
    decision_mode: str = "manual",
    raster_source: bool = False,
) -> dict:
    """Build a draft; caller must separately verify identity, reviews and release gates."""
    import pymupdf

    if not regions or not 150 <= dpi <= 600:
        raise ValueError("se requieren paginas y resolucion entre 150 y 600 DPI")
    if set(masks_by_page) - set(range(1, len(regions) + 1)):
        raise ValueError("hay mascaras para paginas que no existen")
    if source_path.resolve() == output_path.resolve():
        raise ValueError("el PDF original no se puede sobrescribir")
    if output_path.exists():
        raise ValueError("el PDF de salida ya existe; cree una nueva revision")
    output_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4()}.part")
    evidence = []
    try:
        with pymupdf.open(source_path) as source, pymupdf.open() as result:
            for number, region in enumerate(regions, 1):
                original, geometry = render_contract_page(
                    source, region, dpi=dpi, raster_source=raster_source, with_geometry=True
                )
                cleaned, boxes = redact_pixels(
                    original, masks_by_page.get(number, []), decision_mode=decision_mode
                )
                buffer = io.BytesIO()
                cleaned.save(buffer, format="PNG", compress_level=1)
                page = result.new_page(
                    width=cleaned.width * 72 / dpi, height=cleaned.height * 72 / dpi
                )
                page.insert_image(page.rect, stream=buffer.getvalue())
                evidence.append(
                    {
                        "page_number": number,
                        "source_page": region["page"],
                        "source_region": region.get("rect", [0, 0, 1, 1]),
                        "source_rotation": region_rotation(region),
                        "geometry": geometry,
                        "width_pixels": original.width,
                        "height_pixels": original.height,
                        "original_pixels_sha256": hashlib.sha256(original.tobytes()).hexdigest(),
                        "cleaned_pixels_sha256": hashlib.sha256(cleaned.tobytes()).hexdigest(),
                        "mask_pixel_boxes": boxes,
                        "masks_sha256": hashlib.sha256(
                            json.dumps(
                                masks_by_page.get(number, []), sort_keys=True, separators=(",", ":")
                            ).encode()
                        ).hexdigest(),
                    }
                )
            result.set_metadata({})
            result.save(temporary, garbage=4, deflate=True)
        temporary.chmod(0o640)
        with pymupdf.open(temporary) as reopened:
            if reopened.page_count != len(regions) or reopened.embfile_count():
                raise ValueError("salida con paginas o adjuntos inesperados")
            for page in reopened:
                if page.get_text().strip() or page.get_links() or list(page.annots() or []):
                    raise ValueError("la salida contiene capas de texto o anotaciones inesperadas")
                if list(page.widgets() or []):
                    raise ValueError("la salida conserva campos de formulario")
                images = page.get_images()
                if len(images) != 1:
                    raise ValueError("la pagina limpia debe contener una sola imagen")
                pixels = pymupdf.Pixmap(reopened, images[0][0])
                if (
                    hashlib.sha256(pixels.samples).hexdigest()
                    != evidence[page.number]["cleaned_pixels_sha256"]
                ):
                    raise ValueError("la imagen guardada no coincide con los pixeles aprobados")
            if reopened.get_xml_metadata() or any(
                reopened.metadata.get(k)
                for k in (
                    "title",
                    "author",
                    "subject",
                    "keywords",
                    "creator",
                    "producer",
                    "creationDate",
                    "modDate",
                )
            ):
                raise ValueError("la salida conserva metadatos")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        # Refuse overwriting an output created concurrently.
        os.link(temporary, output_path)
        temporary.unlink()
        return {
            "path": str(output_path.resolve()),
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            "page_count": len(regions),
            "dpi": dpi,
            "pages": evidence,
            "manual_review_status": "pending",
            "decision_mode": decision_mode,
        }
    finally:
        temporary.unlink(missing_ok=True)
