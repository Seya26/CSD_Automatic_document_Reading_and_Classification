import base64
import shutil
import subprocess

import pytest
from asn1crypto import cms

from albo.ingestion import ingest_bytes, ingest_local
from albo.ingestion.p7m import extract_p7m

from conftest import make_pdf


def wrap(content: bytes | None) -> bytes:
    """Minimal CMS SignedData envelope (no real signature: enough to test unwrapping)."""
    return cms.ContentInfo({
        "content_type": "signed_data",
        "content": {
            "version": "v1",
            "digest_algorithms": [],
            "encap_content_info": {"content_type": "data", "content": content},
            "signer_infos": [],
        },
    }).dump()


@pytest.fixture
def pdf_bytes(tmp_path):
    return make_pdf(tmp_path / "a.pdf", "COMUNE DI CAMERINO\nORDINANZA N. 3 del 01/02/2025").read_bytes()


def test_plain_pdf_passes_through(pdf_bytes):
    assert extract_p7m(pdf_bytes) == pdf_bytes


def test_der_nested_and_base64(pdf_bytes):
    assert extract_p7m(wrap(pdf_bytes)) == pdf_bytes
    assert extract_p7m(wrap(wrap(pdf_bytes))) == pdf_bytes
    b64 = base64.encodebytes(wrap(pdf_bytes))
    assert extract_p7m(b64) == pdf_bytes
    pem = b"-----BEGIN PKCS7-----\n" + b64 + b"-----END PKCS7-----\n"
    assert extract_p7m(pem) == pdf_bytes


def test_errors():
    with pytest.raises(ValueError, match="Detached"):
        extract_p7m(wrap(None))
    with pytest.raises(ValueError):
        extract_p7m(b"hello world, not a document")
    with pytest.raises(ValueError, match="PDF"):
        extract_p7m(wrap(b"just text"))


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")
def test_real_openssl_signature(tmp_path, pdf_bytes):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                    "-subj", "/CN=test", "-days", "1"], check=True, capture_output=True)
    src, out = tmp_path / "in.pdf", tmp_path / "in.pdf.p7m"
    src.write_bytes(pdf_bytes)
    # -binary: without it openssl rewrites line endings and corrupts the PDF
    subprocess.run(["openssl", "cms", "-sign", "-binary", "-in", src, "-signer", crt, "-inkey", key,
                    "-outform", "DER", "-out", out, "-nodetach"], check=True, capture_output=True)
    assert extract_p7m(out.read_bytes()) == pdf_bytes


def test_ingest_dedupes_pdf_and_p7m(env, pdf_bytes):
    settings, db = env
    a, created_a = ingest_bytes(db, settings, pdf_bytes, municipality="Comune di Camerino")
    b, created_b = ingest_bytes(db, settings, wrap(pdf_bytes), municipality="Comune di Camerino")
    assert a == b and created_a and not created_b
    with pytest.raises(ValueError):
        ingest_bytes(db, settings, b"nope", municipality="Comune di Camerino")


def test_ingest_local_reads_p7m_files(env, tmp_path, pdf_bytes):
    settings, db = env
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "atto.pdf.p7m").write_bytes(wrap(pdf_bytes))
    (folder / "rotto.p7m").write_bytes(b"garbage")
    counts = ingest_local(db, settings, folder, "Comune di Camerino")
    assert counts == {"added": 1, "duplicates": 0, "skipped": 1}
