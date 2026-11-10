"""结构模型命令行：generate、validate、dispose、report。

只写 ``depth/`` 目录，不碰旧根目录的 chapter.html、annotation.json 与 export。
"""

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..library import Library
from ..model import ModelClient
from ..render import asset_url
from ..store import Store
from .citations import (
    FetchError,
    HttpPageFetcher,
    SearchError,
    audit_citations,
    build_citations,
    fetcher_from_env,
    load_dotenv,
    provider_from_env,
)
from .deep import DeepError, audit_deep, generate_deep, validate_deep
from .disposition import TIER_ORDER, audit_conservation, plan_all_dispositions
from .model import DepthModelError, generate_chapter_model
from .render import render_depth_chapter
from .research import ResearchError, audit_research, generate_research, validate_research
from .schema import PROMPT_VERSION, ChapterModelError, validate_chapter_model
from .skim import audit_skim, generate_skim, validate_skim
from .standard import build_standard


@dataclass(frozen=True)
class ChapterContext:
    uid: str
    n: int
    store: Store
    raw: dict


def _parser():
    parser = argparse.ArgumentParser(prog="python -m bookcorpus.reading.depth.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "validate", "dispose", "report"):
        item = sub.add_parser(name)
        item.add_argument("--root", help="实例根目录；默认从当前目录向上找 books.toml")
        item.add_argument("--uid", required=True)
        item.add_argument("--n", type=int, required=True)
        item.add_argument("--file", help="chapter_model 或 ledger 的 JSON 路径")
        item.add_argument("--model")
        item.add_argument("--effort")
        if name == "generate":
            item.add_argument("--no-skim", action="store_true", help="只生成章节模型与账，不编译速览")
            item.add_argument("--skip-render", action="store_true", help="生成速览但不写 chapter.html")
            item.add_argument(
                "--no-standard", action="store_true", help="不写 layers/standard.json（页面仍用确定性标准层）"
            )
            item.add_argument("--no-deep", action="store_true", help="不生成 layers/deep.json 与深读注解")
            item.add_argument("--no-research", action="store_true", help="不生成 layers/research.json 与研究议程")
            item.add_argument("--no-citations", action="store_true", help="不检索、不核验引用")
        if name in ("report", "validate"):
            item.add_argument("--gold", help="带 core_units 的验收 JSON；用于核对核心单元")
    return parser


def _load_json(path):
    with Path(path).open(encoding="utf-8") as fh:
        return json.load(fh)


def _context(args):
    library = Library(args.root)
    raw = library.read_chapter(args.uid, args.n)
    if raw is None:
        raise FileNotFoundError(f"找不到章节 {args.uid}/{args.n:02d}")
    return ChapterContext(args.uid, args.n, Store(library.layout), raw)


def _strip_gold_expectations(payload):
    if not isinstance(payload, dict) or "core_units" not in payload or "tiers" not in payload:
        return payload
    model = dict(payload)
    model.pop("tiers", None)
    model.pop("core_units", None)
    return model


def _validate_payload(payload, context):
    return validate_chapter_model(
        _strip_gold_expectations(payload),
        context.raw.get("blocks") or [],
        uid=context.uid,
        n=context.n,
        source_sha256=context.raw.get("source_sha256"),
    )


def _load_model(args, context):
    if args.file:
        payload = _load_json(args.file)
        if isinstance(payload, dict) and "tiers" in payload and "core_units" not in payload:
            raise ValueError("--file 是 ledger；请直接给 chapter_model 或省略 --file 读 depth/")
        return _validate_payload(payload, context)
    payload = context.store.depth_json(context.uid, context.n, "chapter_model.json")
    if payload is None:
        raise FileNotFoundError("depth/chapter_model.json 不存在，也没有给 --file")
    return _validate_payload(payload, context)


def _chapter_context(library, context):
    raw = context.raw
    chapter = {
        "uid": context.uid,
        "n": context.n,
        "title": raw.get("title") or raw.get("stem") or "",
        "source_sha256": raw.get("source_sha256"),
        "stem": raw.get("stem"),
        "ordinal": raw.get("ordinal"),
    }
    return library.book(context.uid) or {"uid": context.uid}, chapter


def _state_text(
    model,
    raw,
    meta,
    uid,
    n,
    *,
    status="model",
    skim=None,
    standard=None,
    deep=None,
    research=None,
    citations=None,
    generated_at=None,
):
    model_text = json.dumps(model, ensure_ascii=False, indent=1)
    state = {
        "schema": 1,
        "uid": uid,
        "n": n,
        "source_sha256": raw.get("source_sha256"),
        "chapter_model_sha": hashlib.sha256(model_text.encode("utf-8")).hexdigest(),
        "prompt_version": model.get("prompt_version", PROMPT_VERSION),
        "status": status,
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "model": meta,
    }
    if skim is not None:
        skim_text = json.dumps(skim, ensure_ascii=False, indent=1)
        state["layers_sha"] = hashlib.sha256(skim_text.encode("utf-8")).hexdigest()
        state["skim_prompt_version"] = skim.get("prompt_version")
        state["skim_supported"] = bool(skim.get("supported"))
    if standard is not None:
        standard_text = json.dumps(standard, ensure_ascii=False, indent=1)
        state["standard_sha"] = hashlib.sha256(standard_text.encode("utf-8")).hexdigest()
        state["standard_prompt_version"] = standard.get("prompt_version")
    if deep is not None:
        deep_text = json.dumps(deep, ensure_ascii=False, indent=1)
        state["deep_sha"] = hashlib.sha256(deep_text.encode("utf-8")).hexdigest()
        state["deep_prompt_version"] = deep.get("prompt_version")
    if research is not None:
        research_text = json.dumps(research, ensure_ascii=False, indent=1)
        state["research_sha"] = hashlib.sha256(research_text.encode("utf-8")).hexdigest()
        state["research_prompt_version"] = research.get("prompt_version")
    if citations is not None:
        citations_text = json.dumps(citations, ensure_ascii=False, indent=1)
        state["citations_sha"] = hashlib.sha256(citations_text.encode("utf-8")).hexdigest()
        state["citations_prompt_version"] = citations.get("prompt_version")
    if meta.get("cache_key"):
        state["cache_key"] = meta["cache_key"]
    if meta.get("layers_sha"):
        state["layers_sha"] = meta["layers_sha"]
    return model_text, json.dumps(state, ensure_ascii=False, indent=1)


def _load_gold(path):
    payload = _load_json(path)
    if isinstance(payload, list):
        core = payload
    elif isinstance(payload, dict):
        core = payload.get("core_units") or payload.get("core_unit_ids") or payload.get("core")
    else:
        core = None
    if not isinstance(core, list):
        raise ValueError("--gold 必须是核心单元 ID 数组，或含 core_units 的对象")
    return [str(item) for item in core]


def _usage_usd(usage):
    opencode = usage.get("opencode") if isinstance(usage, dict) else None
    if isinstance(opencode, dict):
        cost = opencode.get("list_cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            return round(float(cost), 6)
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    return round(prompt / 1_000_000 * 1.0 + completion / 1_000_000 * 2.0, 6)


def _generate(args):
    if args.file:
        raise ValueError("generate 不接受 --file")
    started = time.monotonic()
    context = _context(args)
    client = ModelClient()
    library = Library(args.root)
    book, chapter = _chapter_context(library, context)
    blocks = context.raw.get("blocks") or []
    model, meta = generate_chapter_model(
        client,
        book,
        chapter,
        blocks,
        model=args.model,
        effort=args.effort,
        cache_dir=context.store.depth_cache_dir(context.uid, context.n),
    )
    ledger = plan_all_dispositions(model, blocks)
    gate = audit_conservation(model, ledger, blocks)
    if gate["status"] != "pass":
        print(gate["detail"], file=sys.stderr)
        return 1
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    model_text = json.dumps(model, ensure_ascii=False, indent=1)
    if args.no_skim:
        _, state_text = _state_text(
            model, context.raw, meta, context.uid, context.n, status="model", generated_at=generated_at
        )
        files = {
            "chapter_model.json": model_text,
            "ledger.json": json.dumps(ledger, ensure_ascii=False, indent=1),
            "state.json": state_text,
        }
        context.store.save_depth(context.uid, context.n, files)
        usage = getattr(client, "total_usage", {}) or {}
        print(
            json.dumps(
                {
                    "status": "model",
                    "uid": context.uid,
                    "n": context.n,
                    "units": len(model.get("units", [])),
                    "sections": len(model.get("sections", [])),
                    "gate": gate["status"],
                    "cache_key": meta.get("cache_key"),
                    "cache_hit": bool(meta.get("cache_hit")),
                    "usage": usage,
                    "usd": _usage_usd(usage),
                    "elapsed_s": round(time.monotonic() - started, 1),
                },
                ensure_ascii=False,
            )
        )
        return 0

    skim = generate_skim(
        client,
        book,
        chapter,
        model,
        blocks,
        model=args.model,
        effort=args.effort,
    )
    skim = validate_skim(skim, model, blocks)
    skim_audit = audit_skim(skim, model, blocks)
    if skim_audit["errors"]:
        print("速览审计未通过：" + "；".join(skim_audit["errors"]), file=sys.stderr)
        return 1
    for warning in skim_audit["warnings"]:
        print(f"速览警告：{warning}", file=sys.stderr)
    skim_text = json.dumps(skim, ensure_ascii=False, indent=1)
    standard = build_standard(model, blocks)
    deep = None
    deep_audit = None
    if not args.no_deep:
        deep, _deep_meta = generate_deep(
            client,
            book,
            chapter,
            model,
            blocks,
            model=args.model,
            effort=args.effort,
        )
        deep = validate_deep(deep, model, blocks)
        deep_audit = audit_deep(deep, model, blocks)
        if deep_audit["errors"]:
            print("深读审计未通过：" + "；".join(deep_audit["errors"]), file=sys.stderr)
            return 1
        for warning in deep_audit["warnings"]:
            print(f"深读警告：{warning}", file=sys.stderr)
    research = None
    research_audit = None
    if not args.no_research:
        research, _research_meta = generate_research(
            client,
            book,
            chapter,
            model,
            blocks,
            model=args.model,
            effort=args.effort,
        )
        research = validate_research(research, model, blocks)
        research_audit = audit_research(research, model, blocks)
        if research_audit["errors"]:
            print("研究审计未通过：" + "；".join(research_audit["errors"]), file=sys.stderr)
            return 1
        for warning in research_audit["warnings"]:
            print(f"研究警告：{warning}", file=sys.stderr)
    citations = None
    citations_audit = None
    citations_error = None
    citations_provider = None
    if research is not None and not args.no_citations:
        try:
            provider = provider_from_env()
            citations_provider = provider.name
            fetcher = HttpPageFetcher() if provider.name == "none" else fetcher_from_env()
            citations = build_citations(client, research, provider=provider, fetcher=fetcher)
            citations_audit = audit_citations(citations, research)
            if citations_audit["errors"]:
                print("引用审计未通过：" + "；".join(citations_audit["errors"]), file=sys.stderr)
                return 1
            for warning in citations_audit["warnings"]:
                print(f"引用警告：{warning}", file=sys.stderr)
        except (SearchError, FetchError) as exc:
            print(f"检索失败，保留研究议程：{exc}", file=sys.stderr)
            citations = None
            citations_audit = None
            citations_error = str(exc)
    files = {
        "chapter_model.json": model_text,
        "ledger.json": json.dumps(ledger, ensure_ascii=False, indent=1),
        "layers/skim.json": skim_text,
    }
    if not args.no_standard:
        files["layers/standard.json"] = json.dumps(standard, ensure_ascii=False, indent=1)
    if deep is not None:
        files["layers/deep.json"] = json.dumps(deep, ensure_ascii=False, indent=1)
    if research is not None:
        files["layers/research.json"] = json.dumps(research, ensure_ascii=False, indent=1)
    if citations is not None:
        files["citations.json"] = json.dumps(citations, ensure_ascii=False, indent=1)
    if not args.skip_render:
        files["chapter.html"] = render_depth_chapter(
            book,
            chapter,
            blocks,
            model,
            ledger,
            skim,
            resolver=lambda rel: asset_url(context.uid, rel),
            generated_at=generated_at,
            standard=standard,
            deep=deep,
            research=research,
            citations=citations,
        )
    status = "model" if args.skip_render else "ready"
    state_text = _state_text(
        model,
        context.raw,
        meta,
        context.uid,
        context.n,
        status=status,
        skim=skim,
        standard=standard,
        deep=deep,
        research=research,
        citations=citations,
        generated_at=generated_at,
    )[1]
    files["state.json"] = state_text
    context.store.save_depth(context.uid, context.n, files)
    stats = skim_audit["stats"] or {}
    usage = getattr(client, "total_usage", {}) or {}
    print(
        json.dumps(
            {
                "status": status,
                "uid": context.uid,
                "n": context.n,
                "units": len(model.get("units", [])),
                "sections": len(model.get("sections", [])),
                "gate": gate["status"],
                "cache_key": meta.get("cache_key"),
                "cache_hit": bool(meta.get("cache_hit")),
                "skim_supported": bool(skim.get("supported")),
                "skim_ratio": stats.get("ratio"),
                "skim_core_claim_coverage": stats.get("core_claim_coverage"),
                "skim_detail_count": stats.get("detail_count"),
                "standard_notes": len(standard.get("notes") or []),
                "figures": len(standard.get("figures") or []),
                "g12": standard["audit"]["status"],
                "deep_notes": len(deep.get("notes") or []) if deep else 0,
                "deep_chains": len(deep.get("chains") or []) if deep else 0,
                "research_matrix": len(research.get("matrix") or []) if research else 0,
                "research_questions": len(research.get("questions") or []) if research else 0,
                "deep_audit": deep_audit["status"] if deep_audit else None,
                "research_audit": research_audit["status"] if research_audit else None,
                "citations_verified": (
                    sum(1 for claim in citations.get("claims") or [] if claim.get("status") == "verified")
                    if citations
                    else 0
                ),
                "citations_total": len(citations.get("citations") or []) if citations else 0,
                "citations_g13": citations_audit["status"] if citations_audit else None,
                "citations_error": citations_error,
                "citations_provider": citations_provider,
                "citations_skipped": bool(args.no_citations or research is None),
                "html": "chapter.html" in files,
                "usage": usage,
                "usd": _usage_usd(usage),
                "elapsed_s": round(time.monotonic() - started, 1),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _validate(args):
    context = _context(args)
    model = _load_model(args, context)
    expected_core = _load_gold(args.gold) if args.gold else []
    present = {unit["id"] for unit in model.get("units", [])}
    missing = [unit_id for unit_id in expected_core if unit_id not in present]
    if missing:
        print(
            json.dumps(
                {"status": "error", "uid": model.get("uid"), "n": model.get("n"), "missing_core": missing},
                ensure_ascii=False,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "ok",
                "uid": model.get("uid"),
                "n": model.get("n"),
                "units": len(model.get("units", [])),
                "sections": len(model.get("sections", [])),
                "core_total": len(expected_core),
                "core_present": len(expected_core) - len(missing),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _dispose(args):
    context = _context(args)
    model = _load_model(args, context)
    blocks = context.raw.get("blocks") or []
    ledger = plan_all_dispositions(model, blocks)
    gate = audit_conservation(model, ledger, blocks)
    print(json.dumps({"ledger": ledger, "gate": gate}, ensure_ascii=False, indent=1))
    return 0 if gate["status"] == "pass" else 1


def _resolve_report(args, context):
    blocks = context.raw.get("blocks") or []
    if args.file:
        payload = _load_json(args.file)
        if isinstance(payload, dict) and "tiers" in payload and "core_units" not in payload:
            ledger = payload
            stored = context.store.depth_json(context.uid, context.n, "chapter_model.json")
            if stored is None:
                raise FileNotFoundError("report --file 是 ledger，但 depth/chapter_model.json 不存在")
            model = _validate_payload(stored, context)
            return model, ledger
        model = _validate_payload(payload, context)
        return model, plan_all_dispositions(model, blocks)
    stored_model = context.store.depth_json(context.uid, context.n, "chapter_model.json")
    if stored_model is None:
        raise FileNotFoundError("depth/chapter_model.json 不存在")
    model = _validate_payload(stored_model, context)
    ledger = context.store.depth_json(context.uid, context.n, "ledger.json")
    if ledger is None:
        ledger = plan_all_dispositions(model, blocks)
    return model, ledger


def _skim_summary(context, model, blocks):
    skim = context.store.depth_json(context.uid, context.n, "layers/skim.json")
    if skim is None:
        return None
    try:
        audit = audit_skim(skim, model, blocks)
    except (TypeError, ValueError) as exc:
        return {"supported": bool(isinstance(skim, dict) and skim.get("supported")), "errors": [str(exc)]}
    stats = audit["stats"] or {}
    return {
        "supported": stats.get("supported"),
        "ratio": stats.get("ratio"),
        "core_claim_coverage": stats.get("core_claim_coverage"),
        "detail_count": stats.get("detail_count"),
        "detail_index": stats.get("detail_count"),
        "claims": stats.get("claims"),
        "errors": audit["errors"],
        "warnings": audit["warnings"],
        "gates": audit["gates"],
    }


def _report(args):
    context = _context(args)
    model, ledger = _resolve_report(args, context)
    blocks = context.raw.get("blocks") or []
    expected_core = _load_gold(args.gold) if args.gold else None
    gate = audit_conservation(model, ledger, blocks, expected_core=expected_core)
    tiers = ledger.get("tiers") if isinstance(ledger, dict) else None
    if not isinstance(tiers, dict):
        raise ValueError("ledger 缺少 tiers")
    summary = {
        "uid": ledger.get("uid"),
        "n": ledger.get("n"),
        "tiers": {},
        "warnings": ledger.get("warnings", []),
        "core_total": gate["core_total"],
        "core_covered": gate["core_covered"],
        "core_coverage": gate["core_coverage"],
        "hard_info_total": gate["hard_info_total"],
        "hard_info_covered": gate["hard_info_covered"],
        "hard_info_coverage": gate["hard_info_coverage"],
        "gate": gate["status"],
        "gate_detail": gate["detail"],
        "problems": gate["problems"],
        "core_limitation": gate.get("limitation", ""),
        "skim": _skim_summary(context, model, blocks),
    }
    skim = summary["skim"] or {}
    summary["skim_ratio"] = skim.get("ratio")
    summary["skim_core_claim_coverage"] = skim.get("core_claim_coverage")
    summary["skim_detail_count"] = skim.get("detail_count")
    for tier in TIER_ORDER:
        account = tiers.get(tier) or {}
        summary["tiers"][tier] = {
            "units": (account.get("counts") or {}).get("units", 0),
            "counts": account.get("counts", {}),
            "core_total": account.get("core_total", 0),
            "core_covered": account.get("core_covered", 0),
            "core_coverage": account.get("core_coverage", 0.0),
            "hard_info_total": account.get("hard_info_total", 0),
            "hard_info_covered": account.get("hard_info_covered", 0),
            "hard_info_coverage": account.get("hard_info_coverage", 0.0),
            "unmapped_blocks": account.get("unmapped_blocks", 0),
            "unmapped_block_ids": account.get("unmapped_block_ids", []),
            "omitted_duplicates": account.get("omitted_duplicates", 0),
            "folds_total": account.get("folds_total", 0),
        }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if gate["status"] == "pass" else 1


def _dispatch(args):
    if args.command == "generate":
        return _generate(args)
    if args.command == "validate":
        return _validate(args)
    if args.command == "dispose":
        return _dispose(args)
    if args.command == "report":
        return _report(args)
    raise ValueError(f"未知命令：{args.command}")


def main(argv=None):
    load_dotenv()
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except (
        ChapterModelError,
        DeepError,
        DepthModelError,
        ResearchError,
        ValueError,
        FileNotFoundError,
        RuntimeError,
        OSError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
