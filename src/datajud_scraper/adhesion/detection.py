"""High-recall lexical routing and document-family hints."""

from __future__ import annotations

import re

from .inventory import body_text

ADHESION = re.compile(
    r"(?:termo|yermo|proposta|contrato).{0,65}ades[a4]o|proposta.{0,35}emiss[a4]o|"
    r"solicito.{0,35}emiss[a4]o|(?:adere|adiro).{0,25}(?:regulamento|condicoes)"
)
CARD = re.compile(r"cart[a4]o|cartoes|\brmc\b|\brcc\b")


def family(page):
    text = body_text(page)
    for name, pattern in (
        ("cencosud", r"cencosud|bradescard"),
        ("daycoval", r"daycoval"),
        ("pan", r"(?:banco|consignado|beneficio) pan\b"),
        ("bmg", r"\bbmg\b"),
        ("bb", r"banco do brasil|\bbb\b"),
        ("santander", r"santander|\bole consignado\b"),
        ("mercantil", r"banco mercantil|mercantil do brasil"),
        ("master", r"banco master|credcesta|credicesta"),
        ("bradesco", r"bradesco"),
        ("credsystem", r"cred[ -]?system|esposende"),
        ("agibank", r"agibank"),
    ):
        if re.search(pattern, text):
            return name
    return "generic"
