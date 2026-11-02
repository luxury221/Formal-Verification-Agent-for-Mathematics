"""Owned source/PDF transport fixtures, not mathematical verification evidence."""

import base64
import io
from pathlib import Path
import zlib

HTML = '<article><section id="s1"><h2>1. Addition</h2><div class="ltx_theorem" id="t1"><p>Theorem 1. For every natural number n, n plus zero equals n.</p></div><p id="p1">Proof. Apply the definition of addition.</p><p id="p2">This passage remains unverified.</p></section></article>'
TEX = r"""\documentclass{article}
\newtheorem{thm}{Theorem}
\begin{document}
\section{Addition}
\begin{thm}\label{thm:add}
For every natural number $n$, we have $n+0=n$.
\end{thm}
\begin{proof}
This proves Theorem~\ref{thm:add} by the definition of addition.
\end{proof}
\input{parts/note}
\end{document}
"""


def source_request(rid="fixture-import", **changes):
    return {
        "request_id": rid,
        "title": "Owned document intake fixture",
        "url": "https://example.org/intake/v1",
        "pdf": None,
        "sources": [{"name": "paper.html", "content": HTML}],
        "entrypoint": None,
        "ocr": "auto",
        **changes,
    }


def pdf_bytes(text="Theorem 1. For every natural number n, n plus zero equals n.", *, scan=False):
    objects = []

    def add(raw):
        objects.append(raw)
        return len(objects)

    def stream(meta, raw):
        return (
            b"<< "
            + meta
            + b" /Length "
            + str(len(raw)).encode()
            + b" >>\nstream\n"
            + raw
            + b"\nendstream"
        )

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    if scan:
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(image)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font = (
            ImageFont.truetype(font_path, 26)
            if Path(font_path).exists()
            else ImageFont.load_default(size=26)
        )
        lines = [
            "Theorem 2. Addition of natural numbers.",
            "For every natural number n, n plus zero equals n.",
            "Proof. Apply the definition of addition.",
        ]
        for i, line in enumerate(lines):
            draw.text((90, 140 + i * 70), line, font=font, fill="black")
        raw = zlib.compress(image.tobytes())
        image.close()
        add(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 620 877] /Resources << /XObject << /I0 5 0 R >> >> /Contents 4 0 R >>"
        )
        add(stream(b"", b"q 620 0 0 877 0 0 cm /I0 Do Q"))
        add(
            stream(
                b"/Type /XObject /Subtype /Image /Width 1240 /Height 1754 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode",
                raw,
            )
        )
    else:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii")
        add(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 620 877] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        )
        add(stream(b"", b"BT /F1 11 Tf 40 780 Td (" + escaped + b") Tj ET"))
        add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, raw in enumerate(objects, 1):
        offsets.append(output.tell())
        output.write(str(index).encode() + b" 0 obj\n" + raw + b"\nendobj\n")
    start = output.tell()
    output.write(b"xref\n0 " + str(len(offsets)).encode() + b"\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(("%010d 00000 n \n" % offset).encode())
    output.write(
        b"trailer\n<< /Size "
        + str(len(offsets)).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(start).encode()
        + b"\n%%EOF\n"
    )
    return output.getvalue()


def pdf_request(rid="pdf-fixture", *, scan=False, **changes):
    return source_request(
        rid,
        pdf={"name": "paper.pdf", "base64": base64.b64encode(pdf_bytes(scan=scan)).decode()},
        sources=[],
        **changes,
    )
