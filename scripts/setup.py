#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenRAG — Windows Setup
========================
Tự động phát hiện GPU/CPU, tạo venv, cài dependencies, tải model.

Usage:
    python scripts/setup.py              # full setup
    python scripts/setup.py check        # kiểm tra máy, không cài gì
    python scripts/setup.py model        # chỉ tải/cập nhật model
    python scripts/setup.py --skip-model # full setup, bỏ qua tải model
    python scripts/setup.py --force      # xoá venv cũ, cài lại từ đầu
"""

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# ── Force UTF-8 trên Windows ──────────────────────────────────────────────
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Paths ─────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent.resolve()
VENV       = ROOT / ".venv"          # venv vẫn local theo project

# Cache dùng chung cho toàn máy — nằm ở %LOCALAPPDATA%\openrag\
# Khi setup ở thư mục khác, pip/model/npm đã có sẵn, không tải lại.
_LOCAL_APP = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
SHARED_CACHE = _LOCAL_APP / "openrag"
PIP_CACHE    = SHARED_CACHE / "pip"
HF_CACHE     = SHARED_CACHE / "huggingface"   # dùng chung với mọi HF project
NPM_CACHE    = SHARED_CACHE / "npm"

ML_SERVICE = ROOT / "ml_service"
FRONTEND   = ROOT / "frontend"
DOTNET_API = ROOT / "OpenRAG.Api"

PYTHON_MIN      = (3, 10)
EMBEDDING_MODEL = "BAAI/bge-m3"
RERANKER_MODEL  = "BAAI/bge-reranker-v2-m3"

VENV_PYTHON = VENV / "Scripts" / "python.exe"
VENV_PIP    = VENV / "Scripts" / "pip.exe"

# ── Màu sắc terminal ──────────────────────────────────────────────────────
_COLOR = sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR"))

def _c(code, t):  return f"\033[{code}m{t}\033[0m" if _COLOR else t
def green(t):     return _c("32", t)
def yellow(t):    return _c("33", t)
def red(t):       return _c("31", t)
def cyan(t):      return _c("36", t)
def bold(t):      return _c("1",  t)
def dim(t):       return _c("2",  t)

def header(title):
    bar = "─" * 52
    print(f"\n{bold(bar)}\n  {bold(title)}\n{bold(bar)}")

def step(n, total, msg): print(f"\n{bold(cyan(f'[{n}/{total}]'))} {bold(msg)}")
def ok(msg):   print(f"  {green('✓')} {msg}")
def warn(msg): print(f"  {yellow('!')} {msg}")
def info(msg): print(f"  {dim('·')} {msg}")
def fail(msg): print(f"  {red('✗')} {msg}")
def skip(msg): print(f"  {dim('–')} Bỏ qua: {msg}")

# ── Shell helpers ─────────────────────────────────────────────────────────

def run(cmd, env=None, cwd=None):
    merged = {**os.environ, **(env or {})}
    # Resolve .cmd/.bat on Windows (e.g. npm.cmd, dotnet.cmd)
    resolved = shutil.which(cmd[0])
    if resolved:
        cmd = [resolved] + cmd[1:]
    try:
        subprocess.run(cmd, env=merged, cwd=str(cwd or ROOT), check=True)
        return True
    except subprocess.CalledProcessError as e:
        fail(f"Lệnh thất bại (exit {e.returncode}): {' '.join(str(c) for c in cmd[:5])}")
        return False

def capture(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.stdout.strip()
    except Exception:
        return ""

def pip(args):
    """Chạy pip qua venv python để đảm bảo dùng đúng môi trường."""
    return run([str(VENV_PYTHON), "-m", "pip"] + args + ["--cache-dir", str(PIP_CACHE)])

# ── Phát hiện GPU ─────────────────────────────────────────────────────────

def detect_gpu():
    """
    Trả về dict:
      has_gpu   : bool
      cuda_ver  : str  (vd "12.4") hoặc None
      torch_idx : str  (URL index cho pip)
      gpu_name  : str
      label     : str  (mô tả ngắn để hiển thị)
    """
    # Tìm nvidia-smi ở các vị trí phổ biến trên Windows
    smi_candidates = [
        shutil.which("nvidia-smi"),
        r"C:\Windows\System32\nvidia-smi.exe",
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
    ]
    smi = next((p for p in smi_candidates if p and Path(p).exists()), None)

    if not smi:
        return _cpu_result("nvidia-smi không tìm thấy")

    output = capture([smi])
    if not output:
        return _cpu_result("nvidia-smi không trả về kết quả")

    # Lấy CUDA version từ dòng "CUDA Version: 12.4"
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", output)
    if not m:
        return _cpu_result("Không đọc được CUDA version")

    major, minor = int(m.group(1)), int(m.group(2))
    cuda_ver = f"{major}.{minor}"

    # Map CUDA driver → PyTorch wheel
    # Tham khảo: https://pytorch.org/get-started/locally/
    if major >= 13 or (major == 12 and minor >= 4):
        wheel = "cu124"
    elif major == 12 and minor >= 1:
        wheel = "cu121"
    elif major >= 11 and minor >= 8:
        wheel = "cu118"
    else:
        wheel = "cu118"   # CUDA cũ hơn, dùng wheel cũ nhất còn hỗ trợ

    gpu_name = capture([smi, "--query-gpu=name", "--format=csv,noheader"]).splitlines()[0].strip()

    return {
        "has_gpu":   True,
        "cuda_ver":  cuda_ver,
        "torch_idx": f"https://download.pytorch.org/whl/{wheel}",
        "wheel":     wheel,
        "gpu_name":  gpu_name or "NVIDIA GPU",
        "label":     f"{gpu_name}  (CUDA {cuda_ver} → PyTorch {wheel})",
    }

def _cpu_result(reason=""):
    if reason:
        info(f"GPU: {reason}")
    return {
        "has_gpu":   False,
        "cuda_ver":  None,
        "torch_idx": "https://download.pytorch.org/whl/cpu",
        "wheel":     "cpu",
        "gpu_name":  "",
        "label":     "CPU only  (không có GPU)",
    }

# ── Kiểm tra môi trường ───────────────────────────────────────────────────

def check_python():
    v = sys.version_info
    if v >= PYTHON_MIN:
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
        return True
    fail(f"Python {v.major}.{v.minor} — cần ≥ {PYTHON_MIN[0]}.{PYTHON_MIN[1]}")
    return False

def check_disk():
    free_gb = shutil.disk_usage(ROOT).free / 1e9
    needed  = 8.0   # torch ~1.5 GB + bge-m3 ~2.3 GB + reranker ~0.6 GB + misc
    fn = ok if free_gb >= needed else warn
    fn(f"Ổ đĩa: {free_gb:.1f} GB trống  (cần ≈ {needed:.0f} GB)")

def check_ram():
    try:
        out = capture(["wmic", "OS", "get", "FreePhysicalMemory", "/Value"])
        m = re.search(r"FreePhysicalMemory=(\d+)", out)
        free_gb = int(m.group(1)) / 1e6 if m else 0
        fn = ok if free_gb >= 8 else warn
        fn(f"RAM trống: {free_gb:.1f} GB  (khuyến nghị ≥ 8 GB — bge-m3 + reranker)")
    except Exception:
        warn("Không đọc được thông tin RAM")

def check_dotnet():
    ver = capture(["dotnet", "--version"])
    if not ver:
        warn(".NET SDK chưa cài  →  https://dotnet.microsoft.com/download")
        return False
    major = int(ver.split(".")[0])
    if major >= 8:
        ok(f".NET SDK {ver}")
        return True
    warn(f".NET SDK {ver} — cần ≥ 8.0")
    return False

def check_node():
    ver = capture(["node", "--version"])
    npm_ver = capture(["npm", "--version"])
    if ver:
        ok(f"Node.js {ver}  /  npm {npm_ver}")
        return True
    warn("Node.js chưa cài  →  https://nodejs.org")
    return False

def check_network():
    try:
        urllib.request.urlopen("https://pypi.org", timeout=6)
        ok("Kết nối mạng: OK")
        return True
    except Exception:
        warn("Không có kết nối internet — sẽ dùng cache")
        return False

def cache_sizes():
    def human(p):
        if not Path(p).exists(): return "—"
        total = sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file())
        for u in ["B","KB","MB","GB"]:
            if total < 1024: return f"{total:.0f} {u}"
            total /= 1024
        return f"{total:.1f} TB"
    info(f"Cache pip         : {human(PIP_CACHE)}")
    info(f"Cache HuggingFace : {human(HF_CACHE)}")
    info(f"Cache npm         : {human(NPM_CACHE)}")

# ── Bước cài đặt ─────────────────────────────────────────────────────────

def create_venv(force=False):
    for d in [PIP_CACHE, HF_CACHE, NPM_CACHE]:
        d.mkdir(parents=True, exist_ok=True)

    if VENV.exists() and force:
        info("--force: xoá venv cũ...")
        shutil.rmtree(VENV)

    if VENV.exists():
        ver = capture([str(VENV_PYTHON), "--version"])
        ok(f"venv đã tồn tại  ({ver})")
        return

    info(f"Tạo venv tại {VENV}")
    if not run([sys.executable, "-m", "venv", str(VENV)]):
        sys.exit(1)
    pip(["install", "--upgrade", "pip", "--quiet"])
    ok("venv tạo thành công")

def install_torch(gpu, force=False):
    # Kiểm tra đã cài đúng backend chưa
    if not force and VENV_PYTHON.exists():
        existing = capture([str(VENV_PYTHON), "-c",
            "import torch; print(torch.__version__, torch.cuda.is_available())"])
        if existing:
            ver, has_cuda = existing.split()
            if (gpu["has_gpu"] and has_cuda == "True") or (not gpu["has_gpu"] and has_cuda == "False"):
                ok(f"PyTorch {ver} (đã cài — bỏ qua)")
                return

    info(f"Index: {gpu['torch_idx']}")
    if pip(["install", "torch", "--index-url", gpu["torch_idx"]]):
        ver = capture([str(VENV_PYTHON), "-c", "import torch; print(torch.__version__)"])
        ok(f"PyTorch {ver}")
    else:
        fail("Cài PyTorch thất bại")
        sys.exit(1)

def install_ml_packages():
    req = ML_SERVICE / "requirements.txt"
    if not req.exists():
        warn(f"{req} không tồn tại")
        return

    pkgs = []
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Bỏ torch — đã cài riêng với đúng wheel
        name = re.split(r"[>=<!;\[#\s]", line)[0].lower()
        if name in ("torch", "torchvision", "torchaudio"):
            continue
        pkgs.append(line)

    if pkgs and pip(["install"] + pkgs):
        ok(f"{len(pkgs)} packages  ({req.name})")

def install_dotnet(ok_flag):
    if not ok_flag:
        skip("dotnet không có")
        return
    if not DOTNET_API.exists():
        skip(f"{DOTNET_API.name} không tồn tại")
        return
    if run(["dotnet", "restore", "--verbosity", "minimal"], cwd=DOTNET_API):
        ok(".NET packages restored")

def install_npm(ok_flag):
    if not ok_flag:
        skip("Node.js không có")
        return
    if not FRONTEND.exists():
        skip(f"{FRONTEND.name} không tồn tại")
        return
    env = {"npm_config_cache": str(NPM_CACHE)}
    if run(["npm", "install", "--prefer-offline"], env=env, cwd=FRONTEND):
        ok("npm packages installed")

def _hf_cached(model_name: str) -> bool:
    """Kiểm tra model đã có trong HF cache chưa (tìm theo tên thư mục)."""
    model_dir = HF_CACHE / "hub"
    if not model_dir.exists():
        return False
    slug = model_name.replace("/", "--")
    return any(model_dir.glob(f"models--{slug}"))


def download_model(force=False):
    """Tải embedding model (bge-m3) và reranker model (bge-reranker-v2-m3)."""
    env = {"HF_HOME": str(HF_CACHE)}

    # ── Embedding model ──────────────────────────────────────────────────
    if _hf_cached(EMBEDDING_MODEL) and not force:
        ok(f"Embedding model đã có trong cache  ({EMBEDDING_MODEL})")
    else:
        info(f"Tải {EMBEDDING_MODEL}  (~2.3 GB) — có thể mất vài phút...")
        script = (
            f"import os; os.environ['HF_HOME'] = {str(HF_CACHE)!r}; "
            "from sentence_transformers import SentenceTransformer; "
            f"m = SentenceTransformer({EMBEDDING_MODEL!r}); "
            "print('dim:', m.get_sentence_embedding_dimension())"
        )
        if run([str(VENV_PYTHON), "-c", script], env=env):
            ok(f"Embedding model ready  ({EMBEDDING_MODEL})")
        else:
            warn("Tải embedding model thất bại — thử lại: python scripts/setup.py model")

    # ── Reranker model ───────────────────────────────────────────────────
    if _hf_cached(RERANKER_MODEL) and not force:
        ok(f"Reranker model đã có trong cache  ({RERANKER_MODEL})")
    else:
        info(f"Tải {RERANKER_MODEL}  (~560 MB)...")
        script = (
            f"import os; os.environ['HF_HOME'] = {str(HF_CACHE)!r}; "
            "from FlagEmbedding import FlagReranker; "
            f"r = FlagReranker({RERANKER_MODEL!r}, use_fp16=False); "
            "print('reranker ready')"
        )
        if run([str(VENV_PYTHON), "-c", script], env=env):
            ok(f"Reranker model ready  ({RERANKER_MODEL})")
        else:
            warn("Tải reranker model thất bại — thử lại: python scripts/setup.py model")

# ── Summary ───────────────────────────────────────────────────────────────

def print_summary(gpu):
    header("Hoàn tất — Hướng dẫn khởi động")
    print(f"""
  {bold('Kích hoạt venv:')}
    {green(str(VENV / "Scripts" / "activate.bat"))}

  {bold('Terminal 1 — Python ML Service  (port 8001):')}
    {green('cd ml_service')}
    {green('uvicorn main_ml:app --port 8001 --reload')}

  {bold('Terminal 2 — .NET 8 API  (port 8000):')}
    {green('cd OpenRAG.Api')}
    {green('dotnet run')}

  {bold('Terminal 3 — Vue dev server  (port 5173, tuỳ chọn):')}
    {green('cd frontend')}
    {green('npm run dev')}

  {bold('Build production frontend:')}
    {green('cd frontend && npm run build')}

  {bold('GPU:')}  {gpu['label']}
""")

# ── Commands ──────────────────────────────────────────────────────────────

def cmd_check():
    header("Kiểm tra cấu hình máy")
    gpu = detect_gpu()
    check_python()
    check_disk()
    check_ram()
    check_network()
    ok(f"GPU: {gpu['label']}") if gpu["has_gpu"] else info(f"GPU: {gpu['label']}")
    check_dotnet()
    check_node()
    print()
    cache_sizes()

def cmd_model(force=False):
    header(f"Tải / cập nhật models  [{EMBEDDING_MODEL}  +  {RERANKER_MODEL}]")
    if not VENV_PYTHON.exists():
        fail("venv chưa tạo — chạy: python scripts/setup.py")
        sys.exit(1)
    download_model(force=force)

TOTAL = 7

def cmd_full(skip_model=False, force=False):
    header("OpenRAG — Full Setup")
    n = 0
    def s(msg): nonlocal n; n += 1; step(n, TOTAL, msg)

    s("Kiểm tra hệ thống")
    gpu = detect_gpu()
    if not check_python(): sys.exit(1)
    check_disk()
    check_ram()
    has_net = check_network()
    ok(f"GPU: {gpu['label']}") if gpu["has_gpu"] else info(f"GPU: {gpu['label']}")
    dotnet_ok = check_dotnet()
    node_ok   = check_node()

    s("Tạo virtual environment")
    create_venv(force=force)

    s(f"Cài PyTorch  [{gpu['wheel']}]")
    install_torch(gpu, force=force)

    s("Cài Python packages  (ml_service)")
    install_ml_packages()

    s(".NET restore")
    install_dotnet(dotnet_ok)

    s("npm install  (frontend)")
    install_npm(node_ok)

    s(f"Download models  [{EMBEDDING_MODEL}  +  {RERANKER_MODEL}]")
    if skip_model:
        skip("--skip-model")
    elif not has_net and not (HF_CACHE / "hub").exists():
        warn("Không có mạng và chưa có cache — bỏ qua")
    else:
        download_model(force=force)

    print_summary(gpu)
    ok(bold("Setup hoàn tất!"))

# ── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="OpenRAG Windows setup")
    p.add_argument("command", nargs="?", default="full",
                   choices=["full", "check", "model"])
    p.add_argument("--skip-model", action="store_true",
                   help="Bỏ qua tải model (mạng chậm)")
    p.add_argument("--force", action="store_true",
                   help="Xoá venv cũ, cài lại từ đầu")
    args = p.parse_args()

    if   args.command == "check": cmd_check()
    elif args.command == "model": cmd_model(force=args.force)
    else:                         cmd_full(skip_model=args.skip_model, force=args.force)
