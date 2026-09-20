"""Install a pinned, user-local Ollama runtime and pull the document vision model.

No root, shell installer, system service, cloud inference, or global Python packages.
The runtime and model cache can be reused by other projects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import time
from pathlib import Path

import httpx

VERSION = "0.34.2"
ARCHIVE_SHA256 = "e155b83589986d2c581fdbf1381ea3ebdb16549883679cd5a0627f7cdc05b12b"
URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-linux-amd64.tar.zst"
DEFAULT_MODEL = "qwen3.5:27b"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--port", type=int, default=11434)
    args = parser.parse_args()
    if os.uname().machine != "x86_64" or not 1024 <= args.port < 65536:
        raise ValueError("se requiere Linux x86_64 y puerto de usuario valido")
    install = Path.home() / f".local/opt/ollama/{VERSION}"
    cache = Path.home() / ".cache/datajud-scraper"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"ollama-{VERSION}-amd64.tar.zst"
    binary = install / "bin/ollama"
    if not binary.exists():
        if not archive.exists():
            temporary = archive.with_suffix(".part")
            with httpx.stream("GET", URL, follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                count, last = 0, time.monotonic()
                with temporary.open("wb") as stream:
                    for chunk in response.iter_bytes(4 * 1024 * 1024):
                        stream.write(chunk)
                        count += len(chunk)
                        if time.monotonic() - last > 10:
                            print(json.dumps({"runtime_download_mb": count // 1000000}), flush=True)
                            last = time.monotonic()
            temporary.replace(archive)
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != ARCHIVE_SHA256:
            raise ValueError("huella del runtime incorrecta; no se ejecutara")
        install.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:*") as package:
            package.extractall(install, filter="data")
    endpoint = f"http://127.0.0.1:{args.port}"
    try:
        httpx.get(endpoint + "/api/version", timeout=2).raise_for_status()
    except httpx.HTTPError:
        model_root = Path.home() / ".local/share/ollama/models"
        model_root.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(
            OLLAMA_HOST=f"127.0.0.1:{args.port}",
            OLLAMA_MODELS=str(model_root),
            OLLAMA_NO_CLOUD="1",
            OLLAMA_NUM_PARALLEL="1",
            OLLAMA_CONTEXT_LENGTH="32768",
            OLLAMA_FLASH_ATTENTION="1",
        )
        with (cache / "ollama-server.log").open("ab") as log:
            process = subprocess.Popen(
                [str(binary), "serve"],
                env=env,
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        (cache / "ollama-server.pid").write_text(str(process.pid) + "\n")
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError("Ollama no inicio; consultar el log local") from None
            try:
                httpx.get(endpoint + "/api/version", timeout=1).raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise RuntimeError("Ollama no responde")
    with httpx.stream(
        "POST", endpoint + "/api/pull", json={"model": args.model}, timeout=300
    ) as response:
        response.raise_for_status()
        last = 0
        for line in response.iter_lines():
            if not line:
                continue
            item = json.loads(line)
            if "error" in item:
                raise RuntimeError(item["error"])
            if time.monotonic() - last > 10 or item.get("status") == "success":
                print(json.dumps(item), flush=True)
                last = time.monotonic()
    models = httpx.get(endpoint + "/api/tags", timeout=30).json()["models"]
    model = next(m for m in models if m["name"] == args.model)
    manifest = {
        "runtime_version": VERSION,
        "runtime_sha256": ARCHIVE_SHA256,
        "binary": str(binary),
        "endpoint": endpoint,
        "model": args.model,
        "model_digest": model["digest"],
    }
    (cache / "local-model.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
