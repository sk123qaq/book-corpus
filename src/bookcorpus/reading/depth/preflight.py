"""深读提交前的容量预检：只看块数与规划，不读模型、不写盘。

判定用的是与生成相同的 ``plan_chunks``，所以「预检说几块」和「实际切几块」
不会漂移。阈值是起始值，实施后用真实任务日志校准（见设计 §阈值与判定）。
"""

from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class Limits:
    max_blocks_per_chunk: int = 100
    max_tokens_per_chunk: int = 96000
    max_blocks_per_chapter: int = 5000
    max_chunks_per_chapter: int = 100
    max_output_tokens_per_chapter: int = 1_000_000
    max_seconds_per_chapter: int = 7200
    tokens_per_block: int = 800
    tokens_per_chunk_overhead: int = 2000
    safety: float = 1.2
    tokens_per_second: float = 109.0


@dataclass(frozen=True)
class Gate:
    ok: bool
    code: str | None
    warnings: tuple[str, ...]
    stats: dict
    remediation: str | None

    def raise_if_blocked(self):
        if not self.ok:
            raise SourceTooLarge(self)

    def to_dict(self):
        return {
            "ok": self.ok,
            "code": self.code,
            "warnings": list(self.warnings),
            "stats": self.stats,
            "remediation": self.remediation,
        }


class SourceTooLarge(RuntimeError):  # noqa: N818 —— 名字是设计文档与 422 契约的一部分
    """源文件超出单章预算；调用方按 422 / blocked 处理，不写失败现场。"""

    kind = "source_too_large"

    def __init__(self, gate):
        self.stats = gate.stats
        self.remediation = gate.remediation
        super().__init__(_blocked_message(gate.stats))

    def to_dict(self):
        return {
            "error": str(self),
            "code": self.kind,
            "stats": self.stats,
            "remediation": self.remediation,
        }


def _blocked_message(stats):
    limits = stats["limits"]
    blocks = stats["blocks"]
    chunks = stats["chunks"]
    tokens = stats["estimated_output_tokens"]
    hours = stats["estimated_seconds"] / 3600
    if blocks > limits["blocks"]:
        return (
            f"本章 {blocks:,} 块，超过单章上限 {limits['blocks']:,} 块；"
            f"按 {limits['max_blocks_per_chunk']} 块切分约 {chunks:,} 个分块、"
            f"约 {tokens:,} 输出 token、约 {hours:.1f} 小时。"
        )
    if chunks > limits["chunks"]:
        return (
            f"本章 {blocks:,} 块按每 {limits['max_blocks_per_chunk']} 块切分约 {chunks:,} 个分块，"
            f"超过单章上限 {limits['chunks']:,} 个分块；约 {tokens:,} 输出 token、约 {hours:.1f} 小时。"
        )
    if tokens > limits["tokens"]:
        return (
            f"本章 {blocks:,} 块，预计输出约 {tokens:,} token，超过单章上限 {limits['tokens']:,}；约 {hours:.1f} 小时。"
        )
    return (
        f"本章 {blocks:,} 块，预计输出约 {tokens:,} token、约 {hours:.1f} 小时，超过单章上限 {limits['seconds']:,} 秒。"
    )


DEFAULT_LIMITS = Limits()


def evaluate(blocks, plans, limits=DEFAULT_LIMITS):
    """按固定顺序判四条硬门槛；第一条命中的就是返回的 code。"""
    blocks = list(blocks or ())
    plans = list(plans or ())
    chars = sum(len(block.get("text") or "") for block in blocks)
    chunks = len(plans)
    max_chunk_blocks = max((len(plan.get("blocks") or []) for plan in plans), default=0)
    estimated_output_tokens = ceil(
        (chunks * limits.tokens_per_chunk_overhead + len(blocks) * limits.tokens_per_block) * limits.safety
    )
    estimated_seconds = ceil(estimated_output_tokens / limits.tokens_per_second)
    stats = {
        "blocks": len(blocks),
        "chars": chars,
        "chunks": chunks,
        "max_chunk_blocks": max_chunk_blocks,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_seconds": estimated_seconds,
        "limits": {
            "blocks": limits.max_blocks_per_chapter,
            "chunks": limits.max_chunks_per_chapter,
            "tokens": limits.max_output_tokens_per_chapter,
            "seconds": limits.max_seconds_per_chapter,
            "max_blocks_per_chunk": limits.max_blocks_per_chunk,
            "max_tokens_per_chunk": limits.max_tokens_per_chunk,
        },
    }
    warnings = []
    minutes = round(estimated_seconds / 60)
    if len(blocks) > 1000:
        warnings.append(f"本章 {len(blocks):,} 块，超过 1,000 块；会切成多个分块，预计约 {minutes} 分钟。")
    if chunks > 20:
        warnings.append(f"会切成 {chunks} 个分块，约 {estimated_output_tokens:,} 输出 token、约 {minutes} 分钟。")
    if estimated_seconds > 3600:
        warnings.append(f"预计约 {estimated_seconds / 3600:.1f} 小时，超过 1 小时。")
    if max_chunk_blocks > limits.max_blocks_per_chunk:
        warnings.append(
            f"单个分块最多 {max_chunk_blocks} 块，超过 {limits.max_blocks_per_chunk} 块；"
            f"阶段 2 会按 {limits.max_blocks_per_chunk} 块切开。"
        )

    code = None
    if (
        len(blocks) > limits.max_blocks_per_chapter
        or chunks > limits.max_chunks_per_chapter
        or estimated_output_tokens > limits.max_output_tokens_per_chapter
        or estimated_seconds > limits.max_seconds_per_chapter
    ):
        code = "source_too_large"

    remediation = None
    if code:
        remediation = (
            f"把 md/ 中的这个文件按章节或每 {limits.max_blocks_per_chunk} 块拆成多个 md，再对每个 md 提交深读。"
        )
    return Gate(
        ok=code is None,
        code=code,
        warnings=tuple(warnings),
        stats=stats,
        remediation=remediation,
    )
