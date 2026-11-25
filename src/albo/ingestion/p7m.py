"""Unwrap signed documents (.p7m, CAdES): many albo pretorio files are PDFs inside a CMS envelope."""
from __future__ import annotations

import base64

from asn1crypto import cms

MAX_NESTING = 5  # a P7M can be signed again (counter-signatures): unwrap layer by layer


def is_pdf(data: bytes) -> bool:
    return data.lstrip()[:5] == b"%PDF-"


def extract_p7m(data: bytes) -> bytes:
    """Return the PDF inside a P7M (DER or PEM/base64, possibly nested). Raises ValueError otherwise."""
    for _ in range(MAX_NESTING):
        if is_pdf(data):
            return data
        raw = data
        if not raw.startswith(b"\x30"):  # not DER: try PEM / plain base64
            lines = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith(b"-----")]
            try:
                raw = base64.b64decode(b"".join(lines), validate=True)
            except ValueError as exc:
                raise ValueError("Not a PDF or P7M file") from exc
        try:
            info = cms.ContentInfo.load(raw)
            content = info["content"]["encap_content_info"]["content"].native
        except Exception as exc:  # malformed ASN.1
            raise ValueError("Not a valid P7M file") from exc
        if content is None:
            raise ValueError("Detached P7M signature: the signed content is not included in the file")
        data = content
    raise ValueError("P7M content is not a PDF")
