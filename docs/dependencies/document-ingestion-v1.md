# Document intake dependencies

Adoption date: 2026-11-01. These tools extract or display source material. They
have no authority to prove a theorem, certify a translation or verify a paper.
They are optional to the core verifier and installed inside the project using
`python3 ops/scripts/prepare_document_runtime.py --with-ocr`.

| Component | Pinned version | License and use | Limits |
| --- | --- | --- | --- |
| pypdf | 6.10.0 | BSD-3-Clause; PDF text and metadata | Extraction can lose mathematical layout and symbols |
| pypdfium2 | 5.9.0 | Wrapper Apache-2.0/BSD-3-Clause; PDFium BSD and retained bundled dependency notices; page rendering | Native parser runs in the bounded document child, not the verifier |
| Pillow | 12.2.0 | MIT-CMU with bundled codec notices; page image output | Retain wheel notices when distributing a runtime |
| typing_extensions | 4.16.0 | PSF-2.0; pypdf typing compatibility on Python 3.10 | No mathematical role |
| Tesseract / libtesseract4 | Ubuntu 4.1.1-2.1build1 | Apache-2.0 upstream with Debian copyright notices; optional local OCR executable | English text OCR does not reliably recover mathematical formulas |
| Leptonica | Ubuntu 1.82.0-3build1 | BSD-style upstream and Debian notices; OCR image processing | Transitive executable dependency |
| English traineddata | Ubuntu 1:4.00~git30-7274cfa-1.1 | Apache-2.0 upstream, retain package copyright notice | No Chinese OCR data or general mathematical OCR model is included |
| giflib / libgif7 | Ubuntu 5.1.9-2ubuntu0.3 | MIT-style upstream, retained Debian notices; OCR codec dependency | Used by local Leptonica |
| libwebpmux3 | Ubuntu 1.2.2-2ubuntu0.22.04.2 | BSD-3-Clause upstream and retained Debian notices; OCR codec dependency | Used by local Leptonica |

Canonical sources: <https://pypi.org/project/pypdf/6.10.0/>,
<https://pypi.org/project/pypdfium2/5.9.0/>,
<https://pypi.org/project/Pillow/12.2.0/>,
<https://github.com/tesseract-ocr/tesseract>,
<https://github.com/tesseract-ocr/tessdata>, and the Ubuntu Jammy package index.

The preparation script validates OCR .deb hashes against local signed apt index
metadata, downloads over HTTPS, and extracts them without root or global package
changes. Python wheels remain in the project pip cache. The installation manifest,
wheel notices and extracted Debian copyright files stay with the local runtime;
runtime distributions must retain them. System libraries remain a host dependency.
The installed PyMuPDF/AGPL package is not used or adopted. No cloud OCR, uploaded
paper transfer, language model or Lean execution is enabled by document intake.
