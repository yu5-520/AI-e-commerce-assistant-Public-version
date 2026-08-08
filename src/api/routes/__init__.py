"""API route modules aligned to product modules.

Competition sample XLSX routes are attached to the existing data-import router so
production packages do not depend on mutable/binary static sample assets.
"""
from __future__ import annotations

from zipfile import BadZipFile

from . import competition_samples as competition_samples
from . import data_import as data_import

_ORIGINAL_PARSE_UPLOAD_FILE = data_import.parse_upload_file


def _competition_safe_parse_upload_file(filename, content, content_type=None):
    try:
        return _ORIGINAL_PARSE_UPLOAD_FILE(filename, content, content_type=content_type)
    except BadZipFile as exc:
        raise ValueError(
            "XLSX 文件损坏或不是有效的 Excel OpenXML 文件；请重新下载评委样例或上传有效报表。"
        ) from exc


data_import.parse_upload_file = _competition_safe_parse_upload_file
data_import.router.include_router(competition_samples.router)
