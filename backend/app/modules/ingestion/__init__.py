"""Ingestion: store sources, handle edits/deletions/duplicates, chunk, embed and extract a graph."""

from app.modules.ingestion.chunking import Chunk, Chunker
from app.modules.ingestion.service import IngestionReport, IngestionService

__all__ = ["Chunk", "Chunker", "IngestionReport", "IngestionService"]
