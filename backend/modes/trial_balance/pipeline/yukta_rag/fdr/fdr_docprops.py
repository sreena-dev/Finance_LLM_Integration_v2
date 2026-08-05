"""Extract an .xlsx file's own internal document properties.

OOXML `.xlsx` is a zip archive; `docProps/app.xml` carries extended properties
(Company, Manager) and `docProps/core.xml` carries core properties (title,
subject, creator). This is read directly via ``zipfile`` + ``xml.etree`` rather
than via openpyxl, whose ``Workbook.properties`` only exposes ``core.xml`` and
has no ``company`` attribute at all — the field this module cares about most
lives exclusively in ``app.xml``.

Legacy ``.xls`` (OLE2 binary format) has no equivalent easy-to-read properties
part and is out of scope; callers get ``{}`` for those, same as for any
malformed or property-less ``.xlsx``. Never raises — a corrupt or unusual file
degrades to an empty result rather than blocking the upload.
"""

from __future__ import annotations

import io
import logging
import zipfile
from xml.etree import ElementTree as ET

logger = logging.getLogger("yukta_rag.fdr.fdr_docprops")

_CORE_NS = "{http://purl.org/dc/elements/1.1/}"
_APP_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"


def _text(root, tag: str) -> str | None:
    el = root.find(tag)
    return el.text.strip() if el is not None and el.text and el.text.strip() else None


def extract_xlsx_properties(filename: str, data: bytes) -> dict:
    """Best-effort ``{"company", "title", "subject", "creator"}`` (only the
    keys with a non-empty value are included); ``{}`` for non-.xlsx or on any
    read failure."""
    if not (filename or "").lower().endswith(".xlsx"):
        return {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            out: dict = {}
            if "docProps/app.xml" in names:
                root = ET.fromstring(z.read("docProps/app.xml"))
                company = _text(root, f"{_APP_NS}Company")
                if company:
                    out["company"] = company
            if "docProps/core.xml" in names:
                root = ET.fromstring(z.read("docProps/core.xml"))
                for key, tag in (("title", f"{_CORE_NS}title"),
                                 ("subject", f"{_CORE_NS}subject"),
                                 ("creator", f"{_CORE_NS}creator")):
                    v = _text(root, tag)
                    if v:
                        out[key] = v
            return out
    except Exception as exc:  # noqa: BLE001 - never block an upload on this
        logger.warning("could not read xlsx document properties for %r: %s", filename, exc)
        return {}
