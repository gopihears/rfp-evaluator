"""Long-document retrieval (OPT-5): section-aware chunks + hybrid BM25 / embedding search.

Used only when a proposal is too long to hand to the model whole. Per criterion, the model
then sees the best-matching passages (in page order) instead of the full text. Deterministic
apart from the embeddings themselves, which come from the provider.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from core.models import Criterion
from core.tools.document_tool import ExtractedDoc, Section

DEFAULT_TARGET_TOKENS = 600
DEFAULT_TOP_K = 8
RRF_K = 60
PRICING_QUERY = "price pricing total cost fee usd assumptions payment"

_PAGE_MARK = re.compile(r"^\[p\.(\d+)\]$")
_TOKEN = re.compile(r"[^\W_]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "with",
        "will",
        "we",
        "our",
        "you",
        "your",
        "this",
        "these",
        "those",
        "their",
        "they",
        "was",
        "were",
        "which",
        "who",
        "not",
        "but",
        "can",
        "may",
        "also",
        "all",
        "any",
        "each",
        "per",
        "such",
        "than",
        "then",
        "into",
        "over",
        "under",
        "more",
        "most",
        "other",
        "out",
        "up",
        "about",
    ]
)
_SUFFIXES = ("ability", "ations", "ation", "ities", "ing", "ies", "ed", "es", "s")


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    return [_stem(w) for w in _TOKEN.findall(text.lower()) if w not in _STOP and len(w) > 1]


@dataclass(frozen=True)
class Chunk:
    id: int
    text: str
    start_page: int
    end_page: int
    section: str
    est_tokens: int


def _split_pages(full_text: str) -> list[tuple[int, list[str]]]:
    pages: list[tuple[int, list[str]]] = []
    for line in full_text.splitlines():
        m = _PAGE_MARK.match(line.strip())
        if m:
            pages.append((int(m.group(1)), []))
        elif pages and line.strip():
            pages[-1][1].append(line.strip())
    return pages


def _section_for(sections: Sequence[Section], page: int) -> str:
    title = ""
    for s in sections:
        if s.start_page <= page:
            title = s.title
    return title


def chunk_document(
    doc: ExtractedDoc, *, target_tokens: int = DEFAULT_TARGET_TOKENS, overlap_lines: int = 2
) -> list[Chunk]:
    """Split into ~target_tokens chunks on line boundaries, keeping tables and page markers."""
    limit = target_tokens * 4
    hard = limit * 2
    chunks: list[Chunk] = []
    current: list[tuple[int, str]] = []  # (page, line)
    lead = 0  # leading lines of `current` that are overlap copied from the previous chunk

    def is_row(line: str) -> bool:
        return " | " in line

    def flush() -> None:
        if not current:
            return
        start, end = current[0][0], current[-1][0]
        # Label by the first NEW line, so overlap from the previous section cannot mislabel it.
        section = _section_for(doc.sections, current[min(lead, len(current) - 1)][0])
        body: list[str] = []
        last_page = 0
        for page, line in current:
            if page != last_page:
                body.append(f"[p.{page}]")
                last_page = page
            body.append(line)
        text = (f"[Section: {section}]\n" if section else "") + "\n".join(body)
        chunks.append(Chunk(len(chunks), text, start, end, section, math.ceil(len(text) / 4)))

    size = 0
    pending_overlap: list[tuple[int, str]] = []
    for page, lines in _split_pages(doc.text):
        for i, line in enumerate(lines):
            if not current and pending_overlap:
                current.extend(pending_overlap)
                lead = len(pending_overlap)
                size = sum(len(x[1]) + 1 for x in current)
                pending_overlap = []
            current.append((page, line))
            size += len(line) + 1
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            in_table = is_row(line) and nxt is not None and is_row(nxt)
            if (size >= limit and not in_table) or size >= hard:
                flush()
                tail = [x for x in current[-overlap_lines:] if not is_row(x[1])]
                pending_overlap = tail
                current = []
                lead = 0
                size = 0
    flush()
    return chunks


Vector = list[float]
EmbedFn = Callable[[list[str]], list[Vector]]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class Index:
    chunks: list[Chunk]
    _tf: list[Counter[str]] = field(default_factory=list)
    _df: Counter[str] = field(default_factory=Counter)
    _avg_len: float = 0.0
    _vectors: list[Vector] | None = None
    _embed: EmbedFn | None = None

    @classmethod
    def build(cls, chunks: list[Chunk], embed: EmbedFn | None = None) -> Index:
        idx = cls(chunks)
        lengths = []
        for ch in chunks:
            tokens = tokenize(ch.text)
            tf = Counter(tokens)
            idx._tf.append(tf)
            lengths.append(len(tokens))
            idx._df.update(tf.keys())
        idx._avg_len = sum(lengths) / len(lengths) if lengths else 0.0
        if embed is not None and chunks:
            idx._vectors = embed([c.text for c in chunks])
            idx._embed = embed
        return idx

    @property
    def has_embeddings(self) -> bool:
        return self._vectors is not None

    def bm25(self, query: str) -> list[float]:
        terms = sorted(set(tokenize(query)))  # repeated query words must not outvote rare ones
        n = len(self.chunks)
        scores = []
        for tf in self._tf:
            length = sum(tf.values())
            s = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                s += idf * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * length / (self._avg_len or 1)))
            scores.append(s)
        return scores

    def search(self, query: str, k: int) -> tuple[list[Chunk], bool]:
        """Top-k chunks by reciprocal-rank fusion. Second value: True when nothing matched."""
        bm = self.bm25(query)
        order_bm = sorted(range(len(bm)), key=lambda i: (-bm[i], i))
        fused: dict[int, float] = {}
        for rank_, i in enumerate(order_bm):
            if bm[i] > 0:
                fused[i] = fused.get(i, 0.0) + 1 / (RRF_K + rank_)
        if self._vectors is not None and self._embed is not None:
            qv = self._embed([query])[0]
            sims = [_cosine(qv, v) for v in self._vectors]
            order_emb = sorted(range(len(sims)), key=lambda i: (-sims[i], i))
            for rank_, i in enumerate(order_emb):
                fused[i] = fused.get(i, 0.0) + 1 / (RRF_K + rank_)
        weak = not any(s > 0 for s in bm)
        ranked = sorted(fused, key=lambda i: (-fused[i], i))[:k]
        return [self.chunks[i] for i in ranked], weak


@dataclass
class RetrievedContext:
    text: str
    pages: list[int]
    chunk_ids: list[int]
    weak: bool


def criterion_query(c: Criterion) -> str:
    parts = [c.name, c.description]
    if c.rubric:
        parts += [v for _, v in sorted(c.rubric.items())]
    return " ".join(p for p in parts if p)


def retrieve_for_criterion(
    index: Index, c: Criterion, *, top_k: int = DEFAULT_TOP_K
) -> RetrievedContext:
    """Best passages for one criterion, plus the opening chunk and the pricing chunk, by page."""
    hits, weak = index.search(criterion_query(c), top_k)
    chosen = {h.id: h for h in hits}
    if index.chunks:
        chosen.setdefault(0, index.chunks[0])
        pricing, _ = index.search(PRICING_QUERY, 1)
        for p in pricing:
            chosen.setdefault(p.id, p)
    ordered = sorted(chosen.values(), key=lambda ch: (ch.start_page, ch.id))
    pages = sorted({p for ch in ordered for p in range(ch.start_page, ch.end_page + 1)})
    return RetrievedContext(
        "\n\n".join(ch.text for ch in ordered), pages, [ch.id for ch in ordered], weak
    )
