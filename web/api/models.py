"""pydantic request/response schemas for the secondbrain api."""

from __future__ import annotations

from dataclasses import dataclass, field


# --- server. ---


@dataclass(frozen=True)
class ServerStatus:
    running: bool
    model: str = ""
    context_size: int = 0
    kv_type_k: str = ""
    kv_type_v: str = ""
    parallel: int = 0
    batch_size: int = 0
    slots_used: int = 0
    slots_total: int = 0


@dataclass(frozen=True)
class ServerConfig:
    batch_size: int = 2048
    ubatch_size: int = 512
    context_size: int = 65536
    parallel: int = 2
    kv_type_k: str = "q8_0"
    kv_type_v: str = "turbo4"
    threads: int = 8


@dataclass(frozen=True)
class HealthResponse:
    llm_server: ServerStatus
    embed_server: ServerStatus


# --- search. ---


@dataclass(frozen=True)
class SearchResult:
    name: str
    subdir: str
    score: float
    snippet: str = ""


@dataclass(frozen=True)
class SearchResponse:
    results: list[SearchResult] = field(default_factory=list)
    query: str = ""
    elapsed_ms: float = 0.0
    total: int = 0


# --- wiki. ---


@dataclass(frozen=True)
class WikiPageMeta:
    name: str
    subdir: str
    page_type: str = ""
    tags: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""


@dataclass(frozen=True)
class WikiPageFull:
    name: str
    subdir: str
    content: str
    page_type: str = ""
    tags: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    inbound_links: list[str] = field(default_factory=list)
    outbound_links: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GraphNode:
    name: str
    subdir: str
    link_count: int = 0


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str


@dataclass(frozen=True)
class WikiGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


# --- query. ---


@dataclass(frozen=True)
class QueryRequest:
    question: str
    save: bool = False


@dataclass(frozen=True)
class QueryResponse:
    answer: str
    sources: list[str] = field(default_factory=list)
    search_time_ms: float = 0.0
    generation_time_ms: float = 0.0
    saved_path: str | None = None


# --- ingest. ---


@dataclass(frozen=True)
class RawFile:
    filename: str
    status: str  # "pending", "ingested", "changed"
    size_bytes: int = 0


@dataclass(frozen=True)
class IngestRequest:
    filename: str
    overwrite: bool = False
    use_embeddings: bool = False


@dataclass(frozen=True)
class IngestProgress:
    task_id: str
    status: str  # "running", "complete", "error"
    filename: str = ""
    message: str = ""
    pages_created: int = 0
    pages_updated: int = 0
    elapsed_seconds: float = 0.0


# --- lint. ---


@dataclass(frozen=True)
class LintIssue:
    level: str  # "error", "warning", "info"
    message: str
    page: str = ""
    target: str = ""


@dataclass(frozen=True)
class LintReport:
    errors: list[LintIssue] = field(default_factory=list)
    warnings: list[LintIssue] = field(default_factory=list)
    info: list[LintIssue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


# --- dedup. ---


@dataclass(frozen=True)
class DedupCluster:
    canonical: str
    merge_from: list[str] = field(default_factory=list)
    group_key: str = ""


@dataclass(frozen=True)
class DedupPlan:
    clusters: list[DedupCluster] = field(default_factory=list)
    total_merges: int = 0
    total_deletions: int = 0


@dataclass(frozen=True)
class DedupResult:
    success: bool
    clusters_merged: int = 0
    pages_deleted: int = 0
    files_rewritten: int = 0
    message: str = ""
