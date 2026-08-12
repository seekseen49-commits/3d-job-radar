"""Бесплатные внешние доски вакансий для одноразового запуска радара."""

from .base import ExternalJob, ExternalState, process_external_sources
from .himalayas import HimalayasSource
from .himalayas_mcp import HimalayasMcpSource
from .jobicy import JobicySource
from .remotive import RemotiveSource
from .threads import ThreadsSource, process_threads_source

__all__ = (
    "ExternalJob",
    "ExternalState",
    "HimalayasSource",
    "HimalayasMcpSource",
    "JobicySource",
    "RemotiveSource",
    "ThreadsSource",
    "process_threads_source",
    "process_external_sources",
)
