#!/usr/bin/env python3
"""Chroma-backed retrieval over the local codebase."""

import hashlib
import os


TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".ini",
    ".cfg", ".html", ".css", ".js",
}
IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "workspace",
    "data",
}


def _chromadb():
    try:
        import chromadb  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "chromadb is required for reliability_engine.rag_index; "
            "install chromadb before using codebase retrieval"
        ) from exc
    return chromadb


def _iter_files(root: str):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for filename in files:
            suffix = os.path.splitext(filename)[1].lower()
            if suffix in TEXT_SUFFIXES:
                yield os.path.join(dirpath, filename)


def _chunks(text: str, max_chars: int = 1800):
    lines = text.splitlines()
    buf: list[str] = []
    size = 0
    start = 1
    for idx, line in enumerate(lines, 1):
        if buf and size + len(line) + 1 > max_chars:
            yield start, "\n".join(buf)
            buf = []
            size = 0
            start = idx
        buf.append(line)
        size += len(line) + 1
    if buf:
        yield start, "\n".join(buf)


class CodebaseRAGIndex:
    def __init__(self, root: str, persist_dir: str | None = None,
                 collection_name: str = "autocorp_code", embedding_function=None):
        self.root = os.path.abspath(root)
        self.collection_name = collection_name
        self.embedding_function = embedding_function
        chromadb = _chromadb()
        path = persist_dir or os.path.join(self.root, "data", "chroma")
        self.client = chromadb.PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(
            collection_name,
            embedding_function=embedding_function,
        )

    def rebuild(self) -> int:
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            self.collection_name,
            embedding_function=self.embedding_function,
        )
        added = 0
        for path in _iter_files(self.root):
            rel = os.path.relpath(path, self.root)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            for start, chunk in _chunks(text):
                if not chunk.strip():
                    continue
                digest = hashlib.sha1(f"{rel}:{start}:{chunk}".encode("utf-8")).hexdigest()
                self.collection.upsert(
                    ids=[digest],
                    documents=[chunk],
                    metadatas=[{"path": rel, "start_line": start}],
                )
                added += 1
        return added

    def query(self, text: str, n_results: int = 5) -> list[dict]:
        result = self.collection.query(query_texts=[text], n_results=n_results)
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0] if result.get("distances") else [None] * len(docs)
        return [
            {"document": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(docs, metas, distances)
        ]
