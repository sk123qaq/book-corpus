"""交叉编码器精排环境与模型部署：<root>/工作区/.venv-rerank 与 工作区/models/bge-reranker-v2-m3。

部署前先做 GPU 与显存硬门槛预检：无可用 GPU 或显存不足时直接跳过，不装 GPU 版
PyTorch 也不下载 2.27 GB 模型。需要下载时，Python 包先对官方源与清华源测速择优，
模型默认走 hf-mirror 镜像（HF_ENDPOINT=https://hf-mirror.com）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODEL_REPO_ID = "BAAI/bge-reranker-v2-m3"
MIN_TOTAL_VRAM_GB = 2.5
MIN_FREE_VRAM_GB = 1.8

PYPI_OFFICIAL = "https://pypi.org/simple"
PYPI_TSINGHUA = "https://pypi.tuna.tsinghua.edu.cn/simple"
HF_MIRROR = "https://hf-mirror.com"
HF_OFFICIAL = "https://huggingface.co"

DISABLE_ENV = "BOOKS_NO_RERANK_VENV"
TIMEOUT = 1800
_OFF = ("", "0", "false", "no")

REQUIREMENTS = ("torch>=2.2", "transformers>=4.40", "numpy>=1.26")


@dataclass(frozen=True)
class GpuInfo:
    available: bool
    vendor: str
    name: str
    total_gb: float
    free_gb: float
    arch: str = ""
    reason: str = ""


@dataclass(frozen=True)
class MirrorProbe:
    name: str
    url: str
    ok: bool
    ms: float
    bytes_read: int = 0
    error: str = ""


def venv_dir(root: str | Path) -> str:
    return str(Path(root) / "工作区" / ".venv-rerank")


def python(root: str | Path) -> str:
    return str(Path(venv_dir(root)) / "bin" / "python")


def model_dir(root: str | Path) -> str:
    env = (os.environ.get("BOOKS_RERANK_MODEL") or "").strip()
    if env:
        return env
    return str(Path(root) / "工作区" / "models" / "bge-reranker-v2-m3")


def disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() not in _OFF


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _read_int(path: Path) -> int | None:
    raw = _read_text(path)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _detect_nvidia_gpu(min_total_gb: float, min_free_gb: float) -> GpuInfo | None:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        proc = subprocess.run(
            [smi, "--query-gpu=name,memory.free,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    best: GpuInfo | None = None
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        name = parts[0]
        try:
            free_gb = float(parts[1]) / 1024.0
            total_gb = float(parts[2]) / 1024.0
        except ValueError:
            continue
        ok = total_gb >= min_total_gb and free_gb >= min_free_gb
        reason = (
            ""
            if ok
            else (
                f"NVIDIA GPU {name} 显存不足"
                f"（总 {total_gb:.2f} GB / 可用 {free_gb:.2f} GB，需 ≥ {min_total_gb:.1f}/{min_free_gb:.1f} GB）"
            )
        )
        cand = GpuInfo(ok, "nvidia", name, round(total_gb, 2), round(free_gb, 2), "cuda", reason)
        if best is None or (cand.available and not best.available) or cand.free_gb > best.free_gb:
            best = cand
    return best


def _amd_gfx_arch(device_hex: str) -> str:
    enumerator = shutil.which("rocm_agent_enumerator")
    if enumerator:
        try:
            proc = subprocess.run([enumerator], capture_output=True, text=True, timeout=3)
            for line in (proc.stdout or "").splitlines():
                line = line.strip()
                if line.startswith("gfx") and line != "gfx000":
                    return line
        except (OSError, subprocess.TimeoutExpired):
            pass
    # Navi 48 (RX 9070 / 9070 XT / 9070 GRE) PCI device IDs
    if device_hex.lower() in ("0x7550", "0x7551"):
        return "gfx1201"
    return ""


def _detect_amd_gpu(
    min_total_gb: float, min_free_gb: float, drm_root: Path | None = None, kfd_path: Path | None = None
) -> GpuInfo | None:
    drm = drm_root or Path("/sys/class/drm")
    kfd = kfd_path or Path("/dev/kfd")
    if not drm.is_dir():
        return None
    cards = sorted(drm.glob("card*/device"))
    amd_cards: list[tuple[str, float, float, str]] = []
    for dev in cards:
        vendor = _read_text(dev / "vendor").lower()
        if vendor != "0x1002":
            continue
        total_b = _read_int(dev / "mem_info_vram_total")
        used_b = _read_int(dev / "mem_info_vram_used")
        if total_b is None or total_b <= 0:
            continue
        free_b = max(0, total_b - (used_b or 0))
        dev_id = _read_text(dev / "device")
        arch = _amd_gfx_arch(dev_id)
        name = f"AMD GPU ({dev_id or '0x1002'}{', ' + arch if arch else ''})"
        amd_cards.append((name, total_b / 1e9, free_b / 1e9, arch))
    if not amd_cards:
        return None
    name, total_gb, free_gb, arch = max(amd_cards, key=lambda x: x[2])
    if not kfd.exists():
        return GpuInfo(
            False,
            "amd",
            name,
            round(total_gb, 2),
            round(free_gb, 2),
            arch,
            "检测到 AMD 显卡但缺少 /dev/kfd（未启用 ROCm 内核驱动）",
        )
    if not os.access(kfd, os.R_OK | os.W_OK):
        return GpuInfo(
            False, "amd", name, round(total_gb, 2), round(free_gb, 2), arch, "/dev/kfd 无读写权限，无法跑 ROCm"
        )
    ok = total_gb >= min_total_gb and free_gb >= min_free_gb
    reason = (
        ""
        if ok
        else (
            f"{name} 显存不足"
            f"（总 {total_gb:.2f} GB / 可用 {free_gb:.2f} GB，需 ≥ {min_total_gb:.1f}/{min_free_gb:.1f} GB）"
        )
    )
    return GpuInfo(ok, "amd", name, round(total_gb, 2), round(free_gb, 2), arch, reason)


def detect_gpu(min_total_gb: float = MIN_TOTAL_VRAM_GB, min_free_gb: float = MIN_FREE_VRAM_GB) -> GpuInfo:
    """检查是否存在满足精排显存要求的 GPU（NVIDIA CUDA 或 AMD ROCm）。"""
    nv = _detect_nvidia_gpu(min_total_gb, min_free_gb)
    if nv is not None:
        return nv
    amd = _detect_amd_gpu(min_total_gb, min_free_gb)
    if amd is not None:
        return amd
    return GpuInfo(False, "none", "", 0.0, 0.0, "", "未检测到可用的 NVIDIA 或 AMD ROCm GPU")


def model_ready(root: str | Path, path: str | Path | None = None) -> bool:
    """检查本地 bge-reranker-v2-m3 目录是否包含完整权重与分词器。"""
    p = Path(path or model_dir(root))
    if not p.is_dir():
        return False
    cfg = p / "config.json"
    tok = (p / "tokenizer.json").is_file() or (p / "sentencepiece.bpe.model").is_file()
    weights = None
    for name in ("model.safetensors", "pytorch_model.bin"):
        cand = p / name
        if cand.is_file():
            weights = cand
            break
    if not (cfg.is_file() and tok and weights is not None):
        return False
    try:
        return weights.stat().st_size > 100 * 1024 * 1024
    except OSError:
        return False


def _probe_url(name: str, index_url: str, probe_path: str = "/packaging/", timeout: float = 5.0) -> MirrorProbe:
    target = index_url.rstrip("/") + probe_path
    req = urllib.request.Request(target, method="GET", headers={"User-Agent": "book-corpus-probe/1.0"})  # noqa: S310
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = resp.read(256 * 1024)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            if 200 <= resp.status < 400:
                return MirrorProbe(name, index_url, True, ms, len(data))
            return MirrorProbe(name, index_url, False, ms, error=f"HTTP {resp.status}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return MirrorProbe(name, index_url, False, ms, error=str(exc))


def pick_fastest_index(
    candidates: list[tuple[str, str, str]], timeout: float = 5.0
) -> tuple[str | None, list[MirrorProbe]]:
    """逐个测速候选索引，返回 (最快可用 index_url, 全部测速明细)。"""
    probes = [_probe_url(name, url, path, timeout=timeout) for name, url, path in candidates]
    ok_probes = [p for p in probes if p.ok]
    if not ok_probes:
        return None, probes
    best = min(ok_probes, key=lambda p: p.ms)
    return best.url, probes


def pick_pypi_index(timeout: float = 5.0) -> tuple[str, list[MirrorProbe]]:
    """对比官方 PyPI 与清华源速度，哪个快用哪个。"""
    candidates = [
        ("官方PyPI", PYPI_OFFICIAL, "/packaging/"),
        ("清华源", PYPI_TSINGHUA, "/packaging/"),
    ]
    best, probes = pick_fastest_index(candidates, timeout=timeout)
    return best or PYPI_TSINGHUA, probes


def pick_torch_index(gpu: GpuInfo, pypi_url: str, timeout: float = 5.0) -> tuple[str | None, list[MirrorProbe]]:
    """按 GPU 厂商测速选择最快的 PyTorch GPU wheel 索引（AMD 仅选 ROCm 源，不误选 CUDA PyPI）。"""
    if gpu.vendor == "amd":
        cands = []
        if gpu.arch.startswith("gfx12"):
            cands.append(("AMD gfx120X ROCm", "https://repo.amd.com/rocm/whl/gfx120X-all", "/torch/"))
        cands.append(("PyTorch ROCm6.4", "https://download.pytorch.org/whl/rocm6.4", "/torch/"))
        return pick_fastest_index(cands, timeout=timeout)
    if gpu.vendor == "nvidia":
        cands = [
            ("PyTorch CUDA12.4", "https://download.pytorch.org/whl/cu124", "/torch/"),
            ("PyTorch CUDA12.6", "https://download.pytorch.org/whl/cu126", "/torch/"),
            ("PyPI/镜像", pypi_url, "/torch/"),
        ]
        return pick_fastest_index(cands, timeout=timeout)
    return None, []


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    lines = [x.strip() for x in ((proc.stderr or "") + "\n" + (proc.stdout or "")).splitlines() if x.strip()]
    return lines[-1] if lines else f"退出码 {proc.returncode}"


def versions(root: str | Path, timeout: int = 120) -> tuple[bool, str]:
    """检查工作区 .venv-rerank 是否能导入 torch/transformers 且 GPU 可用。"""
    py = python(root)
    if not Path(py).exists():
        return False, f"没有 {venv_dir(root)}"
    code = (
        "import torch, transformers, numpy;"
        "ok=torch.cuda.is_available();"
        "dev=torch.cuda.get_device_name(0) if ok else 'cpu';"
        "print(f'torch={torch.__version__} transformers={transformers.__version__} cuda={ok} dev={dev}')"
    )
    try:
        proc = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"导入失败：{exc}"
    if proc.returncode != 0:
        return False, f"导入失败：{_tail(proc)}"
    out = (proc.stdout or "").strip()
    if "cuda=True" not in out:
        return False, f"torch 未启用 GPU：{out}"
    return True, out


def _install_packages(py: str, pypi_url: str, torch_index: str | None, gpu: GpuInfo, timeout: int) -> tuple[bool, str]:
    uv = shutil.which("uv")
    torch_spec = "torch>=2.2"
    if gpu.vendor == "amd" and gpu.arch.startswith("gfx12") and torch_index and "gfx120X-all" in torch_index:
        torch_spec = "torch==2.11.0+rocm7.13.0"

    if uv:
        cmd = [uv, "pip", "install", "--python", py, "--index-strategy", "unsafe-best-match"]
        if torch_index and torch_index != pypi_url:
            cmd.extend(["--index-url", torch_index, "--extra-index-url", pypi_url])
        else:
            cmd.extend(["--index-url", pypi_url])
        cmd.extend([torch_spec, "transformers>=4.40", "numpy>=1.26"])
    else:
        cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "-i", pypi_url]
        if torch_index and torch_index != pypi_url:
            cmd.extend(["--extra-index-url", torch_index])
        cmd.extend([torch_spec, "transformers>=4.40", "numpy>=1.26"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"装包失败：{exc}"
    if proc.returncode != 0:
        return False, f"装包失败：{_tail(proc)}"
    return True, f"已装包（PyPI={pypi_url}，Torch={torch_index or pypi_url}）"


def ensure_model(root: str | Path, gpu: GpuInfo | None = None, timeout: int = TIMEOUT) -> tuple[bool, str]:
    """若模型不在本地且 GPU 达标，则优先走 hf-mirror 下载到 工作区/models/bge-reranker-v2-m3。"""
    target = Path(model_dir(root))
    if model_ready(root, target):
        return True, f"模型已就绪：{target}"
    gpu_info = gpu or detect_gpu()
    if not gpu_info.available:
        return False, f"跳过下载模型：{gpu_info.reason}"
    py = python(root)
    if not Path(py).exists():
        return False, f"缺少精排环境 {venv_dir(root)}，先装环境再下载模型"

    target.parent.mkdir(parents=True, exist_ok=True)
    dl_code = (
        "import os, sys; from huggingface_hub import snapshot_download; "
        "snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2])"
    )
    for label, endpoint in (("hf-mirror", HF_MIRROR), ("HuggingFace官方", HF_OFFICIAL)):
        env = dict(os.environ)
        env["HF_ENDPOINT"] = endpoint
        env["HF_HUB_DISABLE_XET"] = "1"
        try:
            proc = subprocess.run(
                [py, "-c", dl_code, MODEL_REPO_ID, str(target)],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_err = f"{label} 下载异常：{exc}"
            continue
        if proc.returncode == 0 and model_ready(root, target):
            return True, f"模型已从 {label} ({endpoint}) 下载到 {target}"
        last_err = f"{label} 失败：{_tail(proc)}"
    return False, last_err


def create(root: str | Path, timeout: int = TIMEOUT, force: bool = False) -> tuple[bool, dict]:
    """部署精排 venv 与模型。无达标 GPU 直接返回 skipped，不下载任何模型或轮子。"""
    gpu = detect_gpu()
    report: dict = {
        "gpu": {
            "available": gpu.available,
            "vendor": gpu.vendor,
            "name": gpu.name,
            "total_gb": gpu.total_gb,
            "free_gb": gpu.free_gb,
            "arch": gpu.arch,
            "reason": gpu.reason,
        },
        "skipped": False,
        "probes": [],
        "venv": "",
        "model": "",
    }
    if not gpu.available:
        report["skipped"] = True
        report["reason"] = f"跳过精排部署：{gpu.reason}（不下载模型与 GPU 依赖）"
        return False, report

    if not force:
        ok_v, det_v = versions(root)
        if ok_v and model_ready(root):
            report["venv"] = det_v
            report["model"] = f"已就绪：{model_dir(root)}"
            return True, report

    pypi_url, pypi_probes = pick_pypi_index()
    torch_idx, torch_probes = pick_torch_index(gpu, pypi_url)
    report["probes"] = [
        {"group": "pypi", "name": p.name, "url": p.url, "ok": p.ok, "ms": p.ms, "error": p.error} for p in pypi_probes
    ] + [
        {"group": "torch", "name": p.name, "url": p.url, "ok": p.ok, "ms": p.ms, "error": p.error} for p in torch_probes
    ]
    report["selected_pypi"] = pypi_url
    report["selected_torch"] = torch_idx or pypi_url

    py = python(root)
    d = venv_dir(root)
    if force or not Path(py).exists():
        Path(d).parent.mkdir(parents=True, exist_ok=True)
        uv = shutil.which("uv")
        cmd = [uv, "venv", "--python", "3.13", d] if uv else [sys.executable, "-m", "venv", d]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            # uv 指定 3.13 不在时回退当前解释器
            if uv:
                proc = subprocess.run(
                    [sys.executable, "-m", "venv", d], capture_output=True, text=True, timeout=timeout
                )
            if proc.returncode != 0:
                report["reason"] = f"建 venv 失败：{_tail(proc)}"
                return False, report

    ok_v, det_v = versions(root)
    if force or not ok_v:
        ok_pkg, det_pkg = _install_packages(py, pypi_url, torch_idx, gpu, timeout)
        if not ok_pkg:
            report["reason"] = det_pkg
            return False, report
        ok_v, det_v = versions(root)
        if not ok_v:
            report["reason"] = det_v
            return False, report
    report["venv"] = det_v

    ok_m, det_m = ensure_model(root, gpu=gpu, timeout=timeout)
    report["model"] = det_m
    if not ok_m:
        report["reason"] = det_m
        return False, report
    return True, report


def status(root: str | Path) -> tuple[str, str]:
    """供 book-corpus doctor 展示精排状态（无 GPU 不报 fail，只报 warn）。"""
    gpu = detect_gpu()
    ok_v, det_v = versions(root)
    has_model = model_ready(root)
    if ok_v and has_model:
        return "ok", f"工作区/.venv-rerank 与模型就绪（{gpu.name or 'GPU'}, {gpu.free_gb:.1f}/{gpu.total_gb:.1f} GB）"
    if not gpu.available:
        return "warn", f"未启用：{gpu.reason}（自动降级为 BM25+向量）"
    missing = []
    if not ok_v:
        missing.append(det_v)
    if not has_model:
        missing.append(f"缺少模型 {model_dir(root)}")
    return "warn", "；".join(missing) + "；跑 book-corpus rerank-setup 部署"


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="检查 GPU、测速包源并部署精排环境与模型")
    ap.add_argument("--root", default=os.environ.get("BOOKS_ROOT") or ".")
    ap.add_argument("--check", action="store_true", help="只做 GPU 预检与环境核对，不下载")
    ap.add_argument("--probe", action="store_true", help="只测速 PyPI 与 PyTorch 源，不装包")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.probe:
        gpu = detect_gpu()
        pypi_url, pypi_probes = pick_pypi_index()
        torch_url, torch_probes = pick_torch_index(gpu, pypi_url)
        payload = {
            "gpu": gpu.__dict__,
            "selected_pypi": pypi_url,
            "selected_torch": torch_url,
            "probes": [p.__dict__ for p in (pypi_probes + torch_probes)],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if a.check:
        gpu = detect_gpu()
        ok_v, det_v = versions(a.root)
        has_model = model_ready(a.root)
        check_payload: dict[str, Any] = {
            "gpu": gpu.__dict__,
            "venv_ok": ok_v,
            "venv_detail": det_v,
            "model_ok": has_model,
            "model_dir": model_dir(a.root),
        }
        if a.json:
            print(json.dumps(check_payload, ensure_ascii=False, indent=2))
        else:
            gpu_msg = (
                f"可用 {gpu.name} ({gpu.free_gb:.1f}/{gpu.total_gb:.1f} GB)"
                if gpu.available
                else f"不可用 - {gpu.reason}"
            )
            print(f"GPU: {gpu_msg}")
            print(f"环境: {det_v if ok_v else '未就绪 - ' + det_v}")
            print(f"模型: {'就绪 ' + model_dir(a.root) if has_model else '缺失 ' + model_dir(a.root)}")
        return 0 if (gpu.available and ok_v and has_model) else 1

    ok, rep = create(a.root, force=a.force)
    if a.json:
        print(json.dumps({"ok": ok, **rep}, ensure_ascii=False, indent=2))
    else:
        if rep.get("probes"):
            for p in rep["probes"]:
                st = f"{p['ms']:.1f}ms" if p["ok"] else f"失败({p['error']})"
                print(f"测速 [{p['group']}] {p['name']}: {st}")
            print(f"选用源: PyPI={rep.get('selected_pypi')} | Torch={rep.get('selected_torch')}")
        if ok:
            print(f"就绪: {rep['venv']} | {rep['model']}")
        else:
            print(rep.get("reason") or "部署未完成")
    return 0 if ok else (0 if rep.get("skipped") else 1)


if __name__ == "__main__":
    sys.exit(main())
