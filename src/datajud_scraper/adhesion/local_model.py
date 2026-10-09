"""Local structured inference. No source text is sent to a cloud service."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

DEFAULT_MODEL = "qwen3.5:27b"
REFERENCE_MODEL = "qwen3-vl:8b-instruct-q8_0"
PROMPT_VERSION = "card-2026-10-08-index-v3"
BACKEND_DURATIONS = {
    "load_seconds": "load_duration",
    "prompt_seconds": "prompt_eval_duration",
    "decode_seconds": "eval_duration",
}


def inference_snapshot(model):
    return {
        "calls": getattr(model, "calls", 0),
        "seconds": dict(getattr(model, "backend_seconds", {})),
        "samples": dict(getattr(model, "backend_samples", {})),
    }


def inference_timings(model, before):
    """Backend durations for this run; None means Ollama did not report every call."""
    after = inference_snapshot(model)
    calls = after["calls"] - before["calls"]
    return {
        name: round(after["seconds"].get(name, 0) - before["seconds"].get(name, 0), 4)
        if after["samples"].get(name, 0) - before["samples"].get(name, 0) == calls
        else None
        for name in BACKEND_DURATIONS
    }


def canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def private_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf8") as stream:
        temporary.chmod(0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


class LocalModel:
    def __init__(
        self,
        cache: Path,
        *,
        endpoint: str = "http://127.0.0.1:11434",
        model: str = DEFAULT_MODEL,
        timeout: float = 300,
        context_tokens: int = 32768,
        output_tokens: int = 2048,
        progress=None,
    ):
        parsed = urlparse(endpoint)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        if not loopback or parsed.scheme != "http" or parsed.username or parsed.password:
            raise ValueError("la inferencia requiere un endpoint HTTP local sin credenciales")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("endpoint local invalido")
        if "cloud" in model.lower():
            raise ValueError("no se admiten modelos cloud para estos documentos")
        if not 2048 <= context_tokens <= 131072 or not 128 <= output_tokens < context_tokens:
            raise ValueError("invalid local inference context/output limits")
        self.client = httpx.Client(base_url=endpoint.rstrip("/"), timeout=timeout, trust_env=False)
        self.model = model
        self.options = {
            "temperature": 0,
            "seed": 20260920,
            "num_ctx": context_tokens,
            "num_predict": output_tokens,
        }
        self.progress = progress
        self.cache = cache
        self.calls = 0
        self.cache_hits = 0
        self.seconds = 0.0
        self.backend_seconds = dict.fromkeys(BACKEND_DURATIONS, 0.0)
        self.backend_samples = dict.fromkeys(BACKEND_DURATIONS, 0)
        try:
            response = self.client.get("/api/tags")
            response.raise_for_status()
            installed = {m["name"]: m for m in response.json()["models"]}
            if model not in installed:
                raise ValueError(
                    f"modelo local no instalado: {model}; ejecutar setup_local_model.py"
                )
            self.digest = installed[model]["digest"]
            response = self.client.get("/api/version")
            response.raise_for_status()
            self.runtime = response.json()["version"]
        except BaseException:
            self.client.close()
            raise

    def close(self):
        self.client.close()

    def ask(self, task: str, system: str, content: str, schema: dict, images=()):
        image_data = [base64.b64encode(image).decode("ascii") for image in images]
        key = canonical_hash(
            {
                "version": PROMPT_VERSION,
                "model": self.digest,
                "runtime": self.runtime,
                "options": self.options,
                "task": task,
                "system": system,
                "content": content,
                "schema": schema,
                "images": [hashlib.sha256(image).hexdigest() for image in images],
            }
        )
        path = self.cache / key[:2] / f"{key}.json"
        if path.exists():
            cached = json.loads(path.read_text())
            if cached["key"] != key or cached["model_digest"] != self.digest:
                raise ValueError("cache de inferencia no corresponde al modelo o entrada")
            self.cache_hits += 1
            return cached["answer"]
        message = {"role": "user", "content": content}
        if len((system + content + json.dumps(schema)).encode("utf8")) > 80000:
            raise ValueError("entrada demasiado larga; dividir antes de inferir")
        if image_data:
            message["images"] = image_data
        started = time.monotonic()
        if self.progress:
            self.progress(
                {
                    "event": "model_started",
                    "task": task,
                    "model": self.model,
                    "image_count": len(images),
                }
            )
        response = self.client.post(
            "/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": system}, message],
                "format": schema,
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "options": self.options,
            },
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("done") or result.get("done_reason") == "length":
            raise ValueError("respuesta del modelo incompleta; no se acepta como ausencia")
        if result.get("prompt_eval_count", 0) >= self.options["num_ctx"] - 256:
            raise ValueError("contexto del modelo saturado; no se acepta una respuesta truncada")
        answer = json.loads(result["message"]["content"])
        self.calls += 1
        elapsed = time.monotonic() - started
        self.seconds += elapsed
        durations = {
            name: result[key] / 1e9
            if isinstance(result.get(key), (int, float)) and result[key] >= 0
            else None
            for name, key in BACKEND_DURATIONS.items()
        }
        for name, value in durations.items():
            if value is not None:
                self.backend_seconds[name] += value
                self.backend_samples[name] += 1
        if self.progress:
            self.progress(
                {
                    "event": "model_finished",
                    "task": task,
                    "model": self.model,
                    "elapsed_seconds": round(elapsed, 3),
                    "prompt_tokens": result.get("prompt_eval_count"),
                    "output_tokens": result.get("eval_count"),
                    "backend_seconds": durations,
                }
            )
        private_json(
            path,
            {
                "key": key,
                "model_digest": self.digest,
                "task": task,
                "answer": answer,
                "prompt_tokens": result.get("prompt_eval_count"),
                "output_tokens": result.get("eval_count"),
                "elapsed_seconds": elapsed,
                **durations,
            },
        )
        return answer
