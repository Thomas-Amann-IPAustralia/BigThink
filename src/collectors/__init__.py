"""Signal collectors.

Importing this package imports every collector module, and each module's
`@register` decorator adds it to the registry. The imports below therefore look
unused to a linter but are the mechanism by which `get_collector()` can find
anything at all — hence the `noqa: F401`.
"""

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
