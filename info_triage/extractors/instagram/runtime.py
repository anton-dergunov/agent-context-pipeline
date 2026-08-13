"""Compatibility imports for the shared media runtime utilities."""

from info_triage.extractors.media.runtime import (
    LEGACY_THREAD_ENV,
    THREAD_ENV,
    THREAD_ENV_VARS,
    apply_runtime_threads,
    cgroup_cpu_limit,
    configure_threads,
    describe,
    platform_defaults,
    resolve_threads,
)

__all__ = [
    "THREAD_ENV",
    "THREAD_ENV_VARS",
    "LEGACY_THREAD_ENV",
    "apply_runtime_threads",
    "cgroup_cpu_limit",
    "configure_threads",
    "describe",
    "platform_defaults",
    "resolve_threads",
]
