"""Crea la carpeta trusted-store con certificados raiz de confianza en formato PEM
"""
from __future__ import annotations

import re
import ssl
import sys
from pathlib import Path
from typing import List

TARGET = Path(__file__).resolve().parent / "trusted-store"
SERVER_AUTH_OID = "1.3.6.1.5.5.7.3.1"
_PEM_RE = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.DOTALL)


def windows_roots() -> List[str]:
    pems = []
    for der, encoding, trust in ssl.enum_certificates("ROOT"): 
        if encoding != "x509_asn":
            continue
        if trust is True or SERVER_AUTH_OID in trust:
            pems.append(ssl.DER_cert_to_PEM_cert(der))
    return pems


def certifi_roots() -> List[str]:
    import certifi

    return _PEM_RE.findall(Path(certifi.where()).read_text(encoding="ascii"))


def main() -> int:
    pems = windows_roots() if sys.platform == "win32" else certifi_roots()
    if not pems:
        print("ERROR: no se encontraron certificados raiz.")
        return 1

    TARGET.mkdir(exist_ok=True)
    for old in TARGET.glob("root_*.pem"):
        old.unlink()
    for index, pem in enumerate(pems, start=1):
        (TARGET / f"root_{index:03d}.pem").write_text(pem.strip() + "\n", encoding="ascii")

    print(f"Listo: {len(pems)} certificados raiz escritos en {TARGET}\n")
    print(f"SOLACE_TRUST_STORE={TARGET.as_posix()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
