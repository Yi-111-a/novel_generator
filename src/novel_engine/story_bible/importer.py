from __future__ import annotations


class SourceImporter:
    def read_text(self, filename: str, raw_bytes: bytes, fmt: str) -> str:
        if fmt == "txt":
            return raw_bytes.decode("utf-8", errors="ignore")
        raise NotImplementedError(f"unsupported format: {fmt}")
