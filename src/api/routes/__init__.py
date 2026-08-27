"""API route modules aligned to product modules.

Competition sample downloads remain nested below the existing data-import router,
while XLSX validation stays owned by the import adapter itself. The V25.15 RAG
Knowledge Center is nested below the existing system router so it reuses the
release-sealed FastAPI boundary rather than introducing a second application entry.
"""
from __future__ import annotations

from . import competition_samples as competition_samples
from . import data_import as data_import
from . import knowledge_center as knowledge_center
from . import system as system

data_import.router.include_router(competition_samples.router)
system.router.include_router(knowledge_center.router)
