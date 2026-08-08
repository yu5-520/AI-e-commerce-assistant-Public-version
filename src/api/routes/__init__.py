"""API route modules aligned to product modules.

Competition sample downloads remain nested below the existing data-import router,
while XLSX validation stays owned by the import adapter itself.
"""
from __future__ import annotations

from . import competition_samples as competition_samples
from . import data_import as data_import

data_import.router.include_router(competition_samples.router)
