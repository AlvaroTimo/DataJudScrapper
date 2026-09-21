"""Install the Ubuntu Tesseract runtime under the user's home without root."""

from __future__ import annotations

import subprocess
from pathlib import Path


def main():
    root = Path.home() / ".local/opt/datajud-tesseract"
    packages = root / "packages"
    packages.mkdir(parents=True, exist_ok=True)
    # apt-get verifies package downloads against the installed, signed APT indexes.
    subprocess.run(
        [
            "apt-get",
            "download",
            "tesseract-ocr=5.5.0-1build1",
            "libtesseract5=5.5.0-1build1",
            "libleptonica6=1.86.0-1",
        ],
        cwd=packages,
        check=True,
    )
    for package in sorted(packages.glob("*.deb")):
        subprocess.run(["dpkg-deb", "-x", str(package), str(root)], check=True)
    print(root / "usr/bin/tesseract")


if __name__ == "__main__":
    main()
