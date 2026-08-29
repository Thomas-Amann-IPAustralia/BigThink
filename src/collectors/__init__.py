"""Signal collectors. Importing this package registers every collector."""

from src.collectors import (  # noqa: F401
    arxiv,
    crossref,
    datagovau,
    gdelt,
    openalex,
    patentsview,
)
from src.collectors.base import (  # noqa: F401
    Collector,
    build_document,
    document_text,
    get_collector,
    registered_collectors,
)

__all__ = [
    "Collector",
    "build_document",
    "document_text",
    "get_collector",
    "registered_collectors",
]
