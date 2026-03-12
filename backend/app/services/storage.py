from __future__ import annotations

from pathlib import Path


class LocalStorage:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)

    def save(self, relative_path: str, data: bytes) -> str:
        path = self.base_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def load(self, relative_path: str) -> bytes:
        path = self.base_dir / relative_path
        return path.read_bytes()

    def exists(self, relative_path: str) -> bool:
        path = self.base_dir / relative_path
        return path.exists()

