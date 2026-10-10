from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from datajud_scraper.adhesion.ocr import sparse_words

pymupdf = pytest.importorskip("pymupdf")
Image = pytest.importorskip("PIL.Image")


def runtime_present():
    return (
        shutil.which("tesseract")
        or (Path.home() / ".local/opt/datajud-tesseract/usr/bin/tesseract").exists()
    )


@pytest.mark.skipif(not runtime_present(), reason="requiere OCR de tablas local")
def test_sparse_ocr_reads_form_identifiers_and_returns_actual_pixel_boxes():
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=250)
        page.draw_rect((20, 20, 380, 90))
        page.draw_line((20, 52), (380, 52))
        page.insert_text((30, 43), "CONTRATO 52-0298810/18", fontsize=15)
        page.insert_text((30, 78), "CPF: 123.456.789-00", fontsize=15)
        page.insert_text((30, 160), "Taxa de juros: 2,00% ao mes.", fontsize=15)
        pix = page.get_pixmap(dpi=240, alpha=False)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    words = sparse_words(im)
    assert any("0298810" in w[4] for w in words)
    assert any("123.456.789" in w[4] for w in words)
    assert all(0 <= w[0] < w[2] <= 1 and 0 <= w[1] < w[3] <= 1 for w in words)


@pytest.mark.skipif(not runtime_present(), reason="requiere OCR de tablas local")
def test_missing_tsv_configuration_cannot_be_mistaken_for_empty_page(monkeypatch):
    monkeypatch.setattr(
        "datajud_scraper.adhesion.ocr.subprocess.run",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout=b"ordinary text, not TSV"),
    )
    with pytest.raises(RuntimeError, match="no devolvio TSV"):
        sparse_words(Image.new("RGB", (100, 100), "white"))


def test_neural_device_rejects_unknown_configuration(monkeypatch):
    from datajud_scraper.adhesion import ocr

    monkeypatch.setenv("DATAJUD_NEURAL_OCR_DEVICE", "silent-fallback")
    with pytest.raises(ValueError, match="cpu o cuda"):
        ocr.neural_device()


def test_neural_configuration_binds_cpu_and_cuda_options(monkeypatch):
    from datajud_scraper.adhesion import ocr

    monkeypatch.setenv("DATAJUD_NEURAL_OCR_DEVICE", "cpu")
    cpu = ocr.neural_configuration()
    monkeypatch.setenv("DATAJUD_NEURAL_OCR_DEVICE", "cuda")
    cuda = ocr.neural_configuration()
    assert cpu["device"] == "cpu" and cpu["cuda_options"] is None
    assert cuda["device"] == "cuda"
    assert cuda["cuda_options"]["use_tf32"] is False
    assert cpu["packages"] == cuda["packages"]
    assert cpu["models"] == cuda["models"]
    assert cpu != cuda


def test_requested_cuda_without_provider_fails_explicitly(monkeypatch):
    import onnxruntime

    from datajud_scraper.adhesion import ocr

    monkeypatch.setenv("DATAJUD_NEURAL_OCR_DEVICE", "cuda")
    monkeypatch.setattr(onnxruntime, "preload_dlls", lambda **kwargs: None)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: ["CPUExecutionProvider"])
    ocr._neural_engine.cache_clear()
    with pytest.raises(RuntimeError, match="contracts-cuda"):
        ocr.neural_engine()


@pytest.mark.parametrize("active_cuda", [True, False])
def test_cuda_checks_actual_sessions_and_never_reports_cpu_fallback_as_cuda(
    monkeypatch, active_cuda
):
    import onnxruntime
    import rapidocr

    from datajud_scraper.adhesion import ocr

    monkeypatch.setenv("DATAJUD_NEURAL_OCR_DEVICE", "cuda")
    monkeypatch.setattr(onnxruntime, "preload_dlls", lambda **kwargs: None)
    monkeypatch.setattr(
        onnxruntime, "get_available_providers", lambda: ["CUDAExecutionProvider"]
    )
    monkeypatch.setattr(ocr, "NEURAL_MODELS", {})
    configuration = []
    fallback = {"enabled": True}

    def disable_fallback():
        fallback["enabled"] = False

    session = SimpleNamespace(
        get_providers=lambda: ["CUDAExecutionProvider" if active_cuda else "CPUExecutionProvider"],
        disable_fallback=disable_fallback,
    )
    component = SimpleNamespace(session=SimpleNamespace(session=session))
    engine = SimpleNamespace(text_det=component, text_cls=component, text_rec=component)

    def create(*, params: dict):
        configuration.append(params)
        return engine

    monkeypatch.setattr(rapidocr, "RapidOCR", create)
    ocr._neural_engine.cache_clear()
    try:
        if active_cuda:
            assert ocr.neural_engine() is engine
            assert configuration[0]["EngineConfig.onnxruntime.use_cuda"] is True
            assert configuration[0]["EngineConfig.onnxruntime.cuda_ep_cfg.use_tf32"] is False
            assert fallback["enabled"] is False
        else:
            with pytest.raises(RuntimeError, match="no se acepta CPU como CUDA"):
                ocr.neural_engine()
    finally:
        ocr._neural_engine.cache_clear()
