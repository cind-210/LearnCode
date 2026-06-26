"""
Memory management for file content caching.

Mirrors src/memory.ts from the TypeScript version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MemoryFileEntry:
    path: str
    content: str
    version: int = 0
    timestamp: int = 0


@dataclass
class MemoryConfig:
    max_file_size: int = 1_000_000  # 1MB
    max_total_size: int = 50_000_000  # 50MB
    max_entries: int = 200


@dataclass
class MemoryStore:
    entries: dict[str, MemoryFileEntry] = field(default_factory=dict)
    config: MemoryConfig = field(default_factory=MemoryConfig)
    _total_size: int = 0

    def get(self, path: str) -> Optional[str]:
        entry = self.entries.get(path)
        if entry is None:
            return None
        return entry.content

    def set(self, path: str, content: str, version: int = 0) -> None:
        import time
        if len(content) > self.config.max_file_size:
            return

        key = path
        if key in self.entries:
            old = self.entries[key]
            self._total_size -= len(old.content)

        while self._total_size + len(content) > self.config.max_total_size or len(self.entries) >= self.config.max_entries:
            if not self.entries:
                break
            first_key = next(iter(self.entries))
            self._total_size -= len(self.entries[first_key].content)
            del self.entries[first_key]

        self.entries[key] = MemoryFileEntry(
            path=path,
            content=content,
            version=version,
            timestamp=int(time.time() * 1000),
        )
        self._total_size += len(content)

    def delete(self, path: str) -> None:
        if path in self.entries:
            self._total_size -= len(self.entries[path].content)
            del self.entries[path]

    def clear(self) -> None:
        self.entries.clear()
        self._total_size = 0

    def stats(self) -> dict[str, int]:
        return {
            "entries": len(self.entries),
            "total_size": self._total_size,
            "max_total_size": self.config.max_total_size,
        }


DEFAULT_MEMORY_STORE = MemoryStore()


def get_default_memory_store() -> MemoryStore:
    return DEFAULT_MEMORY_STORE


def reset_default_memory_store() -> None:
    global DEFAULT_MEMORY_STORE
    DEFAULT_MEMORY_STORE = MemoryStore()