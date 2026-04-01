#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenRAG — Trình quản lý triển khai
====================================
Tất cả trong một: cài đặt, build, triển khai, dịch vụ, IIS.

Cách dùng:
    python scripts/deploy.py              # menu tương tác
    python scripts/deploy.py setup        # cài đặt dev (venv + packages + models)
    python scripts/deploy.py build        # build frontend + publish .NET
    python scripts/deploy.py deploy       # triển khai lên server
    python scripts/deploy.py status       # kiểm tra trạng thái dịch vụ
"""

import argparse
import ctypes
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
import zipfile
from pathlib import Path

# Force UTF-8 on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ══════════════════════════════════════════════════════════════════════════
#  CẤU HÌNH
# ══════════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).parent.parent.resolve()

# Đường dẫn dev
ML_SERVICE = ROOT / "ml_service"
FRONTEND   = ROOT / "frontend"
DOTNET_API = ROOT / "OpenRAG.Api"
PUBLISH    = ROOT / "publish"

# Đường dẫn server
INSTALL_DIR = Path(r"C:\OpenRAG")
NSSM_EXE    = INSTALL_DIR / "nssm" / "nssm.exe"
PY312_DIR   = INSTALL_DIR / "python312"
PY312_VER   = "3.12.11"
PY312_URL   = f"https://www.python.org/ftp/python/{PY312_VER}/python-{PY312_VER}-amd64.exe"
NSSM_URL    = "https://nssm.cc/release/nssm-2.24.zip"

# Cache
_LOCAL_APP   = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
SHARED_CACHE = _LOCAL_APP / "openrag"
PIP_CACHE    = SHARED_CACHE / "pip"
HF_CACHE     = SHARED_CACHE / "huggingface"
NPM_CACHE    = SHARED_CACHE / "npm"

# Models
EMBEDDING_MODEL = "BAAI/bge-m3"
RERANKER_MODEL  = "BAAI/bge-reranker-v2-m3"

# Giới hạn phiên bản Python
PYTHON_MIN = (3, 10)
PYTHON_MAX = (3, 12)

# Cổng
API_PORT = 8000
ML_PORT  = 8001
IIS_PORT = 80

# ══════════════════════════════════════════════════════════════════════════
#  GIAO DIỆN TERMINAL
# ══════════════════════════════════════════════════════════════════════════

_COLOR = sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR"))

def _c(code, t):   return f"\033[{code}m{t}\033[0m" if _COLOR else str(t)
def green(t):       return _c("32", t)
def yellow(t):      return _c("33", t)
def red(t):         return _c("31", t)
def cyan(t):        return _c("36", t)
def bold(t):        return _c("1",  t)
def dim(t):         return _c("2",  t)
def bg_blue(t):     return _c("44;97", t)
def bg_green(t):    return _c("42;97", t)
def bg_red(t):      return _c("41;97", t)
def bg_yellow(t):   return _c("43;30", t)

def ok(msg):    print(f"  {green('✓')} {msg}")
def warn(msg):  print(f"  {yellow('⚠')} {msg}")
def info(msg):  print(f"  {dim('›')} {msg}")
def fail(msg):  print(f"  {red('✗')} {msg}")
def skip(msg):  print(f"  {dim('–')} {msg}")

def step(n, total, msg):
    print(f"\n  {bold(cyan(f'[{n}/{total}]'))} {bold(msg)}")

def clear():
    os.system("cls" if os.name == "nt" else "clear")

def pause(msg="  Nhấn Enter để tiếp tục..."):
    input(msg)

def confirm(msg, default=False):
    suffix = " (C/k): " if default else " (c/K): "
    ans = input(f"  {msg}{suffix}").strip().lower()
    if not ans:
        return default
    return ans in ("c", "co", "y", "yes")

def banner():
    clear()
    w = 56
    print()
    print(f"  {bg_blue(' ' * w)}")
    print(f"  {bg_blue('   OpenRAG — Trình quản lý triển khai' + ' ' * (w - 38))}")
    print(f"  {bg_blue(' ' * w)}")
    print()

def show_box(title, lines):
    w = max(len(l) for l in lines) + 4
    w = max(w, len(title) + 4, 50)
    print(f"\n  +{'=' * w}+")
    print(f"  | {bold(title)}{' ' * (w - len(title) - 1)}|")
    print(f"  +{'-' * w}+")
    for l in lines:
        print(f"  | {l}{' ' * (w - len(l) - 1)}|")
    print(f"  +{'=' * w}+")

# ══════════════════════════════════════════════════════════════════════════
#  HÀM HỖ TRỢ
# ══════════════════════════════════════════════════════════════════════════

def run(cmd, env=None, cwd=None, quiet=False):
    merged = {**os.environ, **(env or {})}
    resolved = shutil.which(cmd[0])
    if resolved:
        cmd = [resolved] + cmd[1:]
    try:
        kw = dict(env=merged, cwd=str(cwd or ROOT), check=True)
        if quiet:
            kw["stdout"] = subprocess.DEVNULL
            kw["stderr"] = subprocess.DEVNULL
        subprocess.run(cmd, **kw)
        return True
    except subprocess.CalledProcessError as e:
        fail(f"Lỗi (exit {e.returncode}): {' '.join(str(c) for c in cmd[:5])}")
        return False
    except FileNotFoundError:
        fail(f"Không tìm thấy: {cmd[0]}")
        return False

def capture(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.stdout.strip()
    except Exception:
        return ""

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def is_port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def get_pids_on_port(port):
    pids = set()
    try:
        out = subprocess.check_output(
            f"netstat -ano | findstr LISTENING | findstr :{port}",
            shell=True, text=True, stderr=subprocess.DEVNULL,
        )
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 5:
                pid = int(parts[-1])
                if pid > 0:
                    pids.add(pid)
    except (subprocess.CalledProcessError, ValueError):
        pass
    return list(pids)

def pip_install(python_exe, args, cache=None):
    cmd = [str(python_exe), "-m", "pip"] + args
    if cache:
        cmd += ["--cache-dir", str(cache)]
    return run(cmd)

# ══════════════════════════════════════════════════════════════════════════
#  PHÁT HIỆN GPU
# ══════════════════════════════════════════════════════════════════════════

def detect_gpu():
    smi_candidates = [
        shutil.which("nvidia-smi"),
        r"C:\Windows\System32\nvidia-smi.exe",
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
    ]
    smi = next((p for p in smi_candidates if p and Path(p).exists()), None)
    if not smi:
        return {"has_gpu": False, "wheel": "cpu", "torch_idx": "https://download.pytorch.org/whl/cpu",
                "label": "Chỉ CPU (không có GPU)", "gpu_name": ""}

    output = capture([smi])
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", output or "")
    if not m:
        return {"has_gpu": False, "wheel": "cpu", "torch_idx": "https://download.pytorch.org/whl/cpu",
                "label": "Chỉ CPU (không có GPU)", "gpu_name": ""}

    major, minor = int(m.group(1)), int(m.group(2))
    cuda_ver = f"{major}.{minor}"
    if major >= 13 or (major == 12 and minor >= 4):
        wheel = "cu124"
    elif major == 12 and minor >= 1:
        wheel = "cu121"
    else:
        wheel = "cu118"

    gpu_name = capture([smi, "--query-gpu=name", "--format=csv,noheader"])
    gpu_name = gpu_name.splitlines()[0].strip() if gpu_name else "NVIDIA GPU"

    return {
        "has_gpu": True, "wheel": wheel,
        "torch_idx": f"https://download.pytorch.org/whl/{wheel}",
        "label": f"{gpu_name} (CUDA {cuda_ver})", "gpu_name": gpu_name,
    }

# ══════════════════════════════════════════════════════════════════════════
#  QUẢN LÝ PHIÊN BẢN PYTHON
# ══════════════════════════════════════════════════════════════════════════

def find_compatible_python():
    """Tìm Python 3.10-3.12. Trả về path hoặc None."""
    # 1) Python 3.12 đã cài riêng
    if PY312_DIR.exists() and (PY312_DIR / "python.exe").exists():
        return str(PY312_DIR / "python.exe")

    # 2) py launcher
    for minor in (12, 11, 10):
        ver = capture(["py", f"-3.{minor}", "--version"])
        if ver:
            return f"py|-3.{minor}"

    # 3) python trong PATH
    ver = capture(["python", "--version"])
    if ver:
        m = re.match(r"Python (\d+)\.(\d+)", ver)
        if m:
            mj, mn = int(m.group(1)), int(m.group(2))
            if PYTHON_MIN <= (mj, mn) <= PYTHON_MAX:
                return "python"

    return None

def get_python_cmd(python_spec):
    if not python_spec:
        return None
    if "|" in python_spec:
        return python_spec.split("|")
    return [python_spec]

def download_python312():
    """Tải và cài Python 3.12 vào INSTALL_DIR/python312."""
    installer = Path(tempfile.gettempdir()) / f"python-{PY312_VER}-amd64.exe"

    if not installer.exists():
        info(f"Đang tải Python {PY312_VER}...")
        try:
            urllib.request.urlretrieve(PY312_URL, str(installer))
        except Exception as e:
            fail(f"Tải Python thất bại: {e}")
            return None

    info(f"Cài đặt Python {PY312_VER} vào {PY312_DIR}...")
    PY312_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        str(installer), "/quiet",
        f"TargetDir={PY312_DIR}",
        "InstallAllUsers=0", "Include_launcher=0",
        "Include_test=0", "Include_doc=0",
        "Include_tcltk=0", "CompileAll=0",
        "Shortcuts=0", "AssociateFiles=0",
    ])
    if result.returncode != 0:
        fail("Cài đặt Python thất bại!")
        return None

    exe = PY312_DIR / "python.exe"
    if exe.exists():
        ok(f"Đã cài Python 3.12: {exe}")
        return str(exe)
    fail("Không tìm thấy python.exe sau khi cài!")
    return None

def ensure_compatible_python():
    """Tìm hoặc tải Python tương thích. Trả về path."""
    py = find_compatible_python()
    if py:
        cmd = get_python_cmd(py)
        ver = capture(cmd + ["--version"])
        ok(f"Python tương thích: {ver}")
        return py

    v = sys.version_info
    warn(f"Python hiện tại: {v.major}.{v.minor}.{v.micro} (cần 3.10–3.12)")
    info("PyTorch chưa hỗ trợ Python 3.13+")

    if confirm("Tự động tải Python 3.12?", default=True):
        return download_python312()

    fail("Không có Python tương thích!")
    return None

def create_venv(python_spec, venv_path, force=False):
    """Tạo venv từ Python tương thích."""
    venv_python = venv_path / "Scripts" / "python.exe"

    if venv_path.exists() and force:
        info("Xoá venv cũ...")
        shutil.rmtree(venv_path)

    if venv_path.exists() and venv_python.exists():
        ver = capture([str(venv_python), "--version"])
        ok(f"venv đã tồn tại ({ver})")
        return str(venv_python)

    cmd = get_python_cmd(python_spec)
    if not cmd:
        fail("Không có Python tương thích!")
        return None

    info(f"Tạo venv tại {venv_path}...")
    result = subprocess.run(cmd + ["-m", "venv", str(venv_path)])
    if result.returncode != 0:
        fail("Tạo venv thất bại!")
        return None

    ok("Đã tạo venv")
    return str(venv_python)

# ══════════════════════════════════════════════════════════════════════════
#  KIỂM TRA HỆ THỐNG
# ══════════════════════════════════════════════════════════════════════════

def check_system():
    """Kiểm tra toàn bộ hệ thống."""
    banner()
    print(f"  {bold('Kiểm tra cấu hình máy')}\n")

    # Python
    v = sys.version_info
    print(f"  Python hiện tại:  {v.major}.{v.minor}.{v.micro}")
    py = find_compatible_python()
    if py:
        cmd = get_python_cmd(py)
        ver = capture(cmd + ["--version"])
        ok(f"Python tương thích: {ver}")
    else:
        warn("Không có Python 3.10–3.12 (sẽ tự tải khi triển khai)")

    # .NET
    dotnet = capture(["dotnet", "--version"])
    if dotnet:
        ok(f".NET SDK: {dotnet}")
    else:
        warn(".NET SDK chưa cài")

    # Node.js
    node = capture(["node", "--version"])
    npm = capture(["npm", "--version"])
    if node:
        ok(f"Node.js: {node}  /  npm: {npm}")
    else:
        warn("Node.js chưa cài")

    # GPU
    gpu = detect_gpu()
    if gpu["has_gpu"]:
        ok(f"GPU: {gpu['label']}")
    else:
        info("GPU: Không có (sẽ dùng CPU)")

    # Disk
    free_gb = shutil.disk_usage(ROOT).free / 1e9
    fn = ok if free_gb >= 8 else warn
    fn(f"Ổ đĩa: {free_gb:.1f} GB trống (cần ~8 GB)")

    # RAM
    try:
        out = capture(["wmic", "OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory", "/Value"])
        total_m = re.search(r"TotalVisibleMemorySize=(\d+)", out)
        free_m = re.search(r"FreePhysicalMemory=(\d+)", out)
        if total_m and free_m:
            total_gb = int(total_m.group(1)) / 1e6
            free_gb = int(free_m.group(1)) / 1e6
            fn = ok if free_gb >= 4 else warn
            fn(f"RAM: {free_gb:.1f} / {total_gb:.1f} GB")
    except Exception:
        pass

    # Admin
    if is_admin():
        ok("Quyền Administrator: Có")
    else:
        info("Quyền Administrator: Không (cần cho dịch vụ/IIS)")

    # Network
    try:
        urllib.request.urlopen("https://pypi.org", timeout=5)
        ok("Kết nối mạng: OK")
    except Exception:
        warn("Không có internet")

    # NSSM
    if NSSM_EXE.exists():
        ok(f"NSSM: {NSSM_EXE}")
    else:
        info("NSSM: Chưa cài (cần cho Windows Services)")

    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  CÀI ĐẶT DEV
# ══════════════════════════════════════════════════════════════════════════

def cmd_dev_setup(skip_model=False, force=False):
    """Cài đặt môi trường dev: venv + packages + models."""
    banner()
    print(f"  {bold('Cài đặt môi trường phát triển')}\n")

    total = 7
    n = 0
    def s(msg): nonlocal n; n += 1; step(n, total, msg)

    s("Kiểm tra hệ thống")
    gpu = detect_gpu()
    info(f"GPU: {gpu['label']}")

    s("Tìm Python tương thích")
    py = ensure_compatible_python()
    if not py:
        return

    s("Tạo môi trường ảo (venv)")
    for d in [PIP_CACHE, HF_CACHE, NPM_CACHE]:
        d.mkdir(parents=True, exist_ok=True)

    venv_path = ROOT / ".venv"
    venv_python = create_venv(py, venv_path, force=force)
    if not venv_python:
        return

    s(f"Cài PyTorch [{gpu['wheel']}]")
    existing = capture([venv_python, "-c", "import torch; print(torch.__version__)"])
    if existing and not force:
        ok(f"PyTorch {existing} (đã có)")
    else:
        pip_install(venv_python, ["install", "--upgrade", "pip", "--quiet"], PIP_CACHE)
        pip_install(venv_python, ["install", "torch", "--index-url", gpu["torch_idx"]], PIP_CACHE)

    s("Cài gói Python (ml_service)")
    req = ML_SERVICE / "requirements.txt"
    if req.exists():
        pkgs = []
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[>=<!;\[#\s]", line)[0].lower()
            if name in ("torch", "torchvision", "torchaudio"):
                continue
            pkgs.append(line)
        if pkgs:
            pip_install(venv_python, ["install"] + pkgs, PIP_CACHE)

    s("Khôi phục .NET + npm")
    dotnet_ok = bool(capture(["dotnet", "--version"]))
    node_ok = bool(capture(["node", "--version"]))
    if dotnet_ok and DOTNET_API.exists():
        run(["dotnet", "restore", "--verbosity", "minimal"], cwd=DOTNET_API)
    if node_ok and FRONTEND.exists():
        run(["npm", "install", "--prefer-offline"], env={"npm_config_cache": str(NPM_CACHE)}, cwd=FRONTEND)

    s("Tải mô hình AI")
    if skip_model:
        skip("--skip-model")
    else:
        env = {"HF_HOME": str(HF_CACHE)}
        slug = EMBEDDING_MODEL.replace("/", "--")
        if (HF_CACHE / "hub").exists() and any((HF_CACHE / "hub").glob(f"models--{slug}")):
            ok(f"Mô hình nhúng đã có ({EMBEDDING_MODEL})")
        else:
            info(f"Đang tải {EMBEDDING_MODEL} (~2.3 GB)...")
            run([venv_python, "-c",
                 f"import os; os.environ['HF_HOME']={str(HF_CACHE)!r}; "
                 f"from sentence_transformers import SentenceTransformer; "
                 f"m=SentenceTransformer({EMBEDDING_MODEL!r}); "
                 f"print('dim:', m.get_sentence_embedding_dimension())"], env=env)

        slug2 = RERANKER_MODEL.replace("/", "--")
        if (HF_CACHE / "hub").exists() and any((HF_CACHE / "hub").glob(f"models--{slug2}")):
            ok(f"Mô hình reranker đã có ({RERANKER_MODEL})")
        else:
            info(f"Đang tải {RERANKER_MODEL} (~560 MB)...")
            run([venv_python, "-c",
                 f"import os; os.environ['HF_HOME']={str(HF_CACHE)!r}; "
                 f"from FlagEmbedding import FlagReranker; "
                 f"r=FlagReranker({RERANKER_MODEL!r}, use_fp16=False); "
                 f"print('ok')"], env=env)

    print(f"\n  {green('✓')} {bold('Cài đặt dev hoàn tất!')}\n")
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  BUILD
# ══════════════════════════════════════════════════════════════════════════

def cmd_build():
    """Build frontend + publish .NET API."""
    banner()
    print(f"  {bold('Build phiên bản chính thức')}\n")

    total = 3
    n = 0
    def s(msg): nonlocal n; n += 1; step(n, total, msg)

    s("Build giao diện (Vue → wwwroot)")
    if not FRONTEND.exists():
        fail("Thư mục frontend/ không tồn tại!")
        return False
    if not run(["npm", "install", "--prefer-offline"], cwd=FRONTEND):
        return False
    if not run(["npm", "run", "build"], cwd=FRONTEND):
        return False
    ok("Giao diện → OpenRAG.Api/wwwroot")

    s("Xuất bản .NET API")
    api_publish = PUBLISH / "api"
    if not run(["dotnet", "publish", "-c", "Release", "-o", str(api_publish), "--self-contained", "false"],
               cwd=DOTNET_API):
        return False
    ok(f"API → {api_publish}")

    s("Sao chép dịch vụ ML")
    ml_publish = PUBLISH / "ml_service"
    ml_publish.mkdir(parents=True, exist_ok=True)
    (ml_publish / "rag").mkdir(exist_ok=True)

    for f in ML_SERVICE.glob("*.py"):
        shutil.copy2(f, ml_publish)
    if (ML_SERVICE / "rag").exists():
        shutil.copytree(ML_SERVICE / "rag", ml_publish / "rag", dirs_exist_ok=True)
    shutil.copy2(ML_SERVICE / "requirements.txt", ml_publish)
    env_file = ROOT / ".env"
    if env_file.exists():
        shutil.copy2(env_file, ml_publish)
        shutil.copy2(env_file, api_publish)

    data_pub = PUBLISH / "data"
    data_pub.mkdir(exist_ok=True)
    for d in ["chroma", "uploads", "bm25"]:
        (data_pub / d).mkdir(exist_ok=True)
    db = ROOT / "data" / "openrag.db"
    if db.exists():
        shutil.copy2(db, data_pub)
        ok("Đã sao chép CSDL")

    ok("Đã sao chép dịch vụ ML")

    show_box("Build hoàn tất!", [
        "publish/api/           .NET API + giao diện",
        "publish/ml_service/    Dịch vụ ML Python",
        "publish/data/          SQLite + ChromaDB",
        "",
        "Bước tiếp: Sao chép 'publish/' lên máy chủ",
        "           rồi chạy mục [4] Triển khai",
    ])
    print()
    pause()
    return True

# ══════════════════════════════════════════════════════════════════════════
#  TRIỂN KHAI LÊN MÁY CHỦ
# ══════════════════════════════════════════════════════════════════════════

def cmd_server_deploy():
    """Triển khai trên server: tạo venv, cài packages, cấu hình."""
    banner()
    print(f"  {bold('Triển khai lên máy chủ')}\n")

    if not is_admin():
        warn("Nên chạy với quyền Administrator!")
        if not confirm("Vẫn tiếp tục?"):
            return

    total = 6
    n = 0
    def s(msg): nonlocal n; n += 1; step(n, total, msg)

    s("Tạo thư mục")
    for d in [INSTALL_DIR, INSTALL_DIR / "data", INSTALL_DIR / "data" / "chroma",
              INSTALL_DIR / "data" / "uploads", INSTALL_DIR / "data" / "bm25",
              INSTALL_DIR / "logs"]:
        d.mkdir(parents=True, exist_ok=True)
    ok(f"Thư mục: {INSTALL_DIR}")

    s("Tìm Python tương thích (3.10–3.12)")
    py = ensure_compatible_python()
    if not py:
        return

    s("Tạo venv + cài gói")
    ml_dir = INSTALL_DIR / "ml_service"
    venv_path = ml_dir / ".venv"
    venv_python = create_venv(py, venv_path)
    if not venv_python:
        return

    gpu = detect_gpu()
    info(f"GPU: {gpu['label']}")

    pip_install(venv_python, ["install", "--upgrade", "pip", "--quiet"], PIP_CACHE)
    info(f"Đang cài PyTorch [{gpu['wheel']}]...")
    pip_install(venv_python, ["install", "torch", "--index-url", gpu["torch_idx"]], PIP_CACHE)

    req = ml_dir / "requirements.txt"
    if req.exists():
        info("Đang cài gói ML...")
        pkgs = []
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[>=<!;\[#\s]", line)[0].lower()
            if name in ("torch", "torchvision", "torchaudio"):
                continue
            pkgs.append(line)
        if pkgs:
            pip_install(venv_python, ["install"] + pkgs, PIP_CACHE)
    ok("Đã cài xong gói Python")

    s("Tạo .env cho máy chủ")
    env_file = INSTALL_DIR / ".env"
    if not env_file.exists():
        env_file.write_text(
            "EMBEDDING_MODEL=BAAI/bge-m3\n"
            f"EMBEDDING_DEVICE={'cuda' if gpu['has_gpu'] else 'cpu'}\n"
            "RERANKER_MODEL=BAAI/bge-reranker-v2-m3\n"
            "CHROMA_COLLECTION=documents\n"
            "ML_HOST=127.0.0.1\n"
            "ML_PORT=8001\n"
            "MODEL_IDLE_TTL=600\n"
            "BM25_WRITER_HEAP_SIZE=50000000\n",
            encoding="utf-8",
        )
        ok("Đã tạo .env")
    else:
        ok(".env đã tồn tại")
    shutil.copy2(env_file, ml_dir / ".env")

    s("Cấu hình .NET API")
    api_dir = INSTALL_DIR / "api"
    prod_config = api_dir / "appsettings.Production.json"
    if api_dir.exists() and not prod_config.exists():
        prod_config.write_text(json.dumps({
            "ConnectionStrings": {"Default": "Data Source=../data/openrag.db"},
            "MlService": {"BaseUrl": "http://127.0.0.1:8001"},
            "Urls": "http://127.0.0.1:8000",
            "Logging": {"LogLevel": {"Default": "Warning", "Microsoft.AspNetCore": "Warning"}},
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        ok("Đã tạo appsettings.Production.json")
    elif api_dir.exists():
        ok("appsettings.Production.json đã tồn tại")
    else:
        warn(f"{api_dir} chưa có — sao chép publish/api/ vào trước")

    s("Tải mô hình AI")
    info("Đang tải mô hình nhúng (lần đầu mất vài phút)...")
    result = subprocess.run(
        [venv_python, "-c",
         "from sentence_transformers import SentenceTransformer; "
         f"m=SentenceTransformer({EMBEDDING_MODEL!r}); "
         "print('dim:', m.get_sentence_embedding_dimension())"],
        env={**os.environ, "HF_HOME": str(HF_CACHE)},
    )
    if result.returncode == 0:
        ok("Mô hình nhúng sẵn sàng")
    else:
        warn("Tải mô hình thất bại — có thể thử lại sau")

    print(f"\n  {green('✓')} {bold('Triển khai hoàn tất!')}\n")
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  NSSM / DỊCH VỤ WINDOWS
# ══════════════════════════════════════════════════════════════════════════

def download_nssm():
    """Tải và giải nén NSSM."""
    nssm_dir = INSTALL_DIR / "nssm"
    nssm_dir.mkdir(parents=True, exist_ok=True)

    zip_path = Path(tempfile.gettempdir()) / "nssm-2.24.zip"
    if not zip_path.exists():
        info("Đang tải NSSM...")
        try:
            urllib.request.urlretrieve(NSSM_URL, str(zip_path))
        except Exception as e:
            fail(f"Tải NSSM thất bại: {e}")
            return False

    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for member in zf.namelist():
            if member.endswith("win64/nssm.exe"):
                data = zf.read(member)
                (nssm_dir / "nssm.exe").write_bytes(data)
                ok(f"Đã giải nén NSSM vào {nssm_dir}")
                return True

    fail("Không tìm thấy nssm.exe trong file zip!")
    return False

def nssm(args):
    return subprocess.run([str(NSSM_EXE)] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def cmd_install_services():
    """Cài đặt dịch vụ Windows."""
    banner()
    print(f"  {bold('Cài đặt dịch vụ Windows')}\n")

    if not is_admin():
        fail("Cần quyền Administrator! Chuột phải → Chạy với tư cách quản trị viên")
        pause()
        return

    if not NSSM_EXE.exists():
        warn("NSSM chưa có")
        if confirm("Tự động tải NSSM?", default=True):
            if not download_nssm():
                pause()
                return
        else:
            fail("Cần NSSM để cài dịch vụ Windows")
            pause()
            return

    venv_python = INSTALL_DIR / "ml_service" / ".venv" / "Scripts" / "python.exe"
    api_exe = INSTALL_DIR / "api" / "OpenRAG.Api.exe"

    if not venv_python.exists():
        fail(f"Chưa có venv: {venv_python}")
        info("Chạy mục [4] Triển khai trước")
        pause()
        return

    # -- Dịch vụ ML --
    step(1, 2, "OpenRAG-ML (Python FastAPI)")
    nssm(["stop", "OpenRAG-ML"])
    nssm(["remove", "OpenRAG-ML", "confirm"])

    nssm(["install", "OpenRAG-ML", str(venv_python)])
    nssm(["set", "OpenRAG-ML", "AppParameters", "main_ml.py"])
    nssm(["set", "OpenRAG-ML", "AppDirectory", str(INSTALL_DIR / "ml_service")])
    nssm(["set", "OpenRAG-ML", "AppEnvironmentExtra",
          f"DOTENV_PATH={INSTALL_DIR}\\.env", f"HF_HOME={HF_CACHE}"])
    nssm(["set", "OpenRAG-ML", "DisplayName", "OpenRAG ML Service"])
    nssm(["set", "OpenRAG-ML", "Description",
          "Dịch vụ ML Python FastAPI (nhúng văn bản, tìm kiếm, xếp hạng lại)"])
    nssm(["set", "OpenRAG-ML", "Start", "SERVICE_AUTO_START"])
    nssm(["set", "OpenRAG-ML", "AppStdout", str(INSTALL_DIR / "logs" / "ml-stdout.log")])
    nssm(["set", "OpenRAG-ML", "AppStderr", str(INSTALL_DIR / "logs" / "ml-stderr.log")])
    nssm(["set", "OpenRAG-ML", "AppStdoutCreationDisposition", "4"])
    nssm(["set", "OpenRAG-ML", "AppStderrCreationDisposition", "4"])
    nssm(["set", "OpenRAG-ML", "AppRotateFiles", "1"])
    nssm(["set", "OpenRAG-ML", "AppRotateBytes", "10485760"])
    nssm(["set", "OpenRAG-ML", "AppStopMethodSkip", "6"])
    nssm(["set", "OpenRAG-ML", "AppStopMethodConsole", "5000"])
    nssm(["set", "OpenRAG-ML", "AppStopMethodWindow", "5000"])
    nssm(["set", "OpenRAG-ML", "AppStopMethodThreads", "5000"])
    ok("Đã cài OpenRAG-ML")

    # -- Dịch vụ API --
    step(2, 2, "OpenRAG-API (.NET Core)")
    nssm(["stop", "OpenRAG-API"])
    nssm(["remove", "OpenRAG-API", "confirm"])

    if api_exe.exists():
        nssm(["install", "OpenRAG-API", str(api_exe)])
    else:
        warn(f"{api_exe} chưa có — dùng dotnet thay thế")
        dotnet = shutil.which("dotnet") or "dotnet"
        nssm(["install", "OpenRAG-API", dotnet])
        nssm(["set", "OpenRAG-API", "AppParameters",
              str(INSTALL_DIR / "api" / "OpenRAG.Api.dll")])

    nssm(["set", "OpenRAG-API", "AppDirectory", str(INSTALL_DIR / "api")])
    nssm(["set", "OpenRAG-API", "AppEnvironmentExtra",
          "ASPNETCORE_ENVIRONMENT=Production",
          "ASPNETCORE_URLS=http://127.0.0.1:8000"])
    nssm(["set", "OpenRAG-API", "DisplayName", "OpenRAG API Service"])
    nssm(["set", "OpenRAG-API", "Description", "Dịch vụ API ASP.NET Core cho OpenRAG"])
    nssm(["set", "OpenRAG-API", "Start", "SERVICE_AUTO_START"])
    nssm(["set", "OpenRAG-API", "DependOnService", "OpenRAG-ML"])
    nssm(["set", "OpenRAG-API", "AppStdout", str(INSTALL_DIR / "logs" / "api-stdout.log")])
    nssm(["set", "OpenRAG-API", "AppStderr", str(INSTALL_DIR / "logs" / "api-stderr.log")])
    nssm(["set", "OpenRAG-API", "AppStdoutCreationDisposition", "4"])
    nssm(["set", "OpenRAG-API", "AppStderrCreationDisposition", "4"])
    nssm(["set", "OpenRAG-API", "AppRotateFiles", "1"])
    nssm(["set", "OpenRAG-API", "AppRotateBytes", "10485760"])
    ok("Đã cài OpenRAG-API")

    print()
    if confirm("Khởi động dịch vụ ngay?", default=True):
        info("Đang khởi động OpenRAG-ML...")
        nssm(["start", "OpenRAG-ML"])
        info("Đợi 10 giây để ML sẵn sàng...")
        time.sleep(10)
        info("Đang khởi động OpenRAG-API...")
        nssm(["start", "OpenRAG-API"])
        print()
        _show_services_status()

    pause()

def cmd_uninstall_services():
    """Gỡ cài đặt dịch vụ Windows."""
    banner()
    print(f"  {bold('Gỡ cài đặt dịch vụ Windows')}\n")

    if not is_admin():
        fail("Cần quyền Administrator!")
        pause()
        return

    if not NSSM_EXE.exists():
        fail("NSSM không có")
        pause()
        return

    if not confirm("Xác nhận gỡ cài đặt dịch vụ OpenRAG?"):
        return

    nssm(["stop", "OpenRAG-API"])
    nssm(["stop", "OpenRAG-ML"])
    nssm(["remove", "OpenRAG-API", "confirm"])
    nssm(["remove", "OpenRAG-ML", "confirm"])
    ok("Đã gỡ cài đặt dịch vụ")
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  TRẠNG THÁI DỊCH VỤ
# ══════════════════════════════════════════════════════════════════════════

def _show_services_status():
    """Hiển thị trạng thái dịch vụ."""
    services = [
        ("OpenRAG-ML",  ML_PORT,  "Python FastAPI"),
        ("OpenRAG-API", API_PORT, ".NET Core API"),
    ]

    print(f"\n  {'Dịch vụ':<16} {'Cổng':<8} {'Trạng thái':<12} {'Sức khoẻ':<8}")
    print(f"  {'-'*16} {'-'*8} {'-'*12} {'-'*8}")

    for name, port, desc in services:
        if NSSM_EXE.exists():
            r = nssm(["status", name])
            status = r.stdout.strip() if r.returncode == 0 else "KHÔNG CÓ"
        else:
            r = subprocess.run(["sc", "query", name], capture_output=True, text=True)
            if r.returncode != 0:
                status = "KHÔNG CÓ"
            else:
                m = re.search(r"STATE\s+:\s+\d+\s+(\w+)", r.stdout)
                status = m.group(1) if m else "KHÔNG RÕ"

        if "RUNNING" in status:
            status_str = green("ĐANG CHẠY")
        elif "STOPPED" in status:
            status_str = yellow("ĐÃ DỪNG")
        elif "KHÔNG" in status or "NOT" in status:
            status_str = dim("N/A")
        else:
            status_str = red(status)

        if is_port_open(port):
            health = green("OK")
        elif "RUNNING" in status:
            health = yellow("CHỜ")
        else:
            health = dim("–")

        print(f"  {name:<16} {port:<8} {status_str:<24} {health}")

    iis_status = green("ĐANG NGHE") if is_port_open(IIS_PORT) else dim("N/A")
    print(f"  {'IIS':<16} {IIS_PORT:<8} {iis_status}")

    print()

def cmd_status():
    """Xem trạng thái dịch vụ."""
    banner()
    print(f"  {bold('Trạng thái dịch vụ')}")
    _show_services_status()

    log_dir = INSTALL_DIR / "logs"
    if log_dir.exists():
        print(f"  Nhật ký: {log_dir}")
        for f in sorted(log_dir.glob("*.log")):
            size = f.stat().st_size
            if size > 1024*1024:
                s = f"{size/1024/1024:.1f} MB"
            elif size > 1024:
                s = f"{size/1024:.0f} KB"
            else:
                s = f"{size} B"
            print(f"    {f.name:<25} {s}")
    print()
    pause()

def cmd_restart_services():
    """Khởi động lại dịch vụ."""
    banner()
    print(f"  {bold('Khởi động lại dịch vụ')}\n")

    if not NSSM_EXE.exists():
        fail("NSSM không có")
        pause()
        return

    if not is_admin():
        fail("Cần quyền Administrator!")
        pause()
        return

    info("Đang dừng dịch vụ...")
    nssm(["stop", "OpenRAG-API"])
    nssm(["stop", "OpenRAG-ML"])
    time.sleep(2)

    info("Đang khởi động OpenRAG-ML...")
    nssm(["start", "OpenRAG-ML"])
    info("Đợi 10 giây...")
    time.sleep(10)

    info("Đang khởi động OpenRAG-API...")
    nssm(["start", "OpenRAG-API"])
    time.sleep(2)

    _show_services_status()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  CẤU HÌNH IIS
# ══════════════════════════════════════════════════════════════════════════

def cmd_iis_setup():
    """Cấu hình IIS reverse proxy."""
    banner()
    print(f"  {bold('Cấu hình IIS Reverse Proxy')}\n")

    if not is_admin():
        fail("Cần quyền Administrator!")
        pause()
        return

    step(1, 3, "Kiểm tra module IIS")

    arr_dll = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "inetsrv" / "requestRouter.dll"
    if not arr_dll.exists():
        print()
        warn("Cần cài thủ công 2 module IIS:")
        print()
        print(f"  1) {bold('URL Rewrite 2.1')}")
        print(f"     https://www.iis.net/downloads/microsoft/url-rewrite")
        print()
        print(f"  2) {bold('Application Request Routing (ARR) 3.0')}")
        print(f"     https://www.iis.net/downloads/microsoft/application-request-routing")
        print()
        if not confirm("Đã cài đặt xong?"):
            return
    ok("Module IIS đã có")

    appcmd = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "inetsrv" / "appcmd.exe"
    if appcmd.exists():
        subprocess.run([str(appcmd), "set", "config",
                        "-section:system.webServer/proxy", "/enabled:True",
                        "/commit:apphost"], capture_output=True)
        ok("Đã bật ARR proxy")

    step(2, 3, "Tạo web.config")
    site_dir = INSTALL_DIR / "iis-site"
    site_dir.mkdir(parents=True, exist_ok=True)

    backend = "http://127.0.0.1:8000"
    web_config = textwrap.dedent(f"""\
    <?xml version="1.0" encoding="UTF-8"?>
    <configuration>
        <system.webServer>
            <rewrite>
                <rules>
                    <rule name="WebSocket" stopProcessing="true">
                        <match url="ws/(.*)" />
                        <action type="Rewrite" url="{backend}/ws/{{R:1}}" />
                        <serverVariables>
                            <set name="HTTP_SEC_WEBSOCKET_EXTENSIONS" value="" />
                        </serverVariables>
                    </rule>
                    <rule name="ReverseProxy" stopProcessing="true">
                        <match url="(.*)" />
                        <action type="Rewrite" url="{backend}/{{R:1}}" />
                        <serverVariables>
                            <set name="HTTP_X_FORWARDED_HOST" value="{{HTTP_HOST}}" />
                            <set name="HTTP_X_FORWARDED_PROTO" value="{{REQUEST_SCHEME}}" />
                        </serverVariables>
                    </rule>
                </rules>
                <outboundRules>
                    <rule name="RewriteLocationHeader" preCondition="IsRedirection">
                        <match serverVariable="RESPONSE_Location"
                               pattern="http://127\\.0\\.0\\.1:8000/(.*)" />
                        <action type="Rewrite" value="/{{R:1}}" />
                    </rule>
                    <preConditions>
                        <preCondition name="IsRedirection">
                            <add input="{{RESPONSE_STATUS}}" pattern="3\\d\\d" />
                        </preCondition>
                    </preConditions>
                </outboundRules>
            </rewrite>
            <security>
                <requestFiltering>
                    <requestLimits maxAllowedContentLength="524288000" />
                </requestFiltering>
            </security>
            <httpProtocol>
                <customHeaders>
                    <remove name="X-Powered-By" />
                </customHeaders>
            </httpProtocol>
        </system.webServer>
    </configuration>
    """)
    (site_dir / "web.config").write_text(web_config, encoding="utf-8")
    ok(f"web.config → {site_dir}")

    step(3, 3, "Tạo IIS Site")
    ps_script = textwrap.dedent(f"""\
        Import-Module WebAdministration -ErrorAction SilentlyContinue
        if (Test-Path 'IIS:\\Sites\\OpenRAG' -ErrorAction SilentlyContinue) {{
            Remove-WebSite -Name 'OpenRAG'
        }}
        New-WebSite -Name 'OpenRAG' -Port {IIS_PORT} -PhysicalPath '{site_dir}' -Force | Out-Null
        try {{ Stop-WebSite -Name 'Default Web Site' -ErrorAction SilentlyContinue }} catch {{}}
        Start-WebSite -Name 'OpenRAG'
        Write-Host 'IIS site created'
    """)
    r = subprocess.run(["powershell", "-Command", ps_script],
                       capture_output=True, text=True)
    if r.returncode == 0:
        ok(f"Đã tạo IIS site 'OpenRAG' trên cổng {IIS_PORT}")
    else:
        fail(f"Lỗi tạo IIS site: {r.stderr.strip()}")

    print()
    show_box("Cấu hình IIS hoàn tất!", [
        f"Site:     OpenRAG (cổng {IIS_PORT})",
        f"Backend:  {backend}",
        f"Thư mục:  {site_dir}",
        "",
        "Truy cập: http://localhost",
        "          http://<địa-chỉ-IP-server>",
    ])
    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  CHẾ ĐỘ PHÁT TRIỂN
# ══════════════════════════════════════════════════════════════════════════

def cmd_dev_start():
    """Khởi động dịch vụ (chế độ dev, mở cửa sổ cmd)."""
    banner()
    print(f"  {bold('Chế độ phát triển — Khởi động')}\n")

    venv_activate = ROOT / ".venv" / "Scripts" / "activate.bat"

    for name, port, directory, cmd_str in [
        ("ML Service", ML_PORT, ML_SERVICE,
         f'call "{venv_activate}" && python main_ml.py' if venv_activate.exists()
         else "python main_ml.py"),
        ("API", API_PORT, DOTNET_API, "dotnet run"),
        ("Giao diện", 5173, FRONTEND, "npm run dev"),
    ]:
        if is_port_open(port):
            ok(f"{name} đã chạy trên cổng {port}")
            continue
        if not directory.exists():
            warn(f"{name}: thư mục {directory} không tồn tại")
            continue
        subprocess.Popen(
            f'start "{name}" /D "{directory}" cmd /k "{cmd_str}"',
            shell=True,
        )
        info(f"{name} đang khởi động trên cổng {port}...")
        time.sleep(1)

    print()
    pause()

def cmd_dev_stop():
    """Dừng tất cả dịch vụ dev."""
    banner()
    print(f"  {bold('Dừng tất cả dịch vụ')}\n")

    for name, port in [("API", API_PORT), ("ML", ML_PORT), ("Giao diện", 5173)]:
        pids = get_pids_on_port(port)
        if not pids:
            info(f"{name}: không chạy")
            continue
        for pid in pids:
            try:
                subprocess.run(f"taskkill /F /PID {pid}", shell=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok(f"Đã dừng {name} PID {pid}")
            except Exception:
                fail(f"Không thể dừng PID {pid}")
    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  XEM NHẬT KÝ
# ══════════════════════════════════════════════════════════════════════════

def cmd_view_logs():
    """Xem nhật ký."""
    banner()
    print(f"  {bold('Xem nhật ký')}\n")

    log_dir = INSTALL_DIR / "logs"
    if not log_dir.exists():
        warn("Thư mục nhật ký chưa tồn tại")
        pause()
        return

    log_files = sorted(log_dir.glob("*.log"))
    if not log_files:
        info("Không có file nhật ký nào")
        pause()
        return

    print("  Chọn file nhật ký:\n")
    for i, f in enumerate(log_files, 1):
        size = f.stat().st_size
        if size > 1024*1024:
            s = f"{size/1024/1024:.1f} MB"
        elif size > 1024:
            s = f"{size/1024:.0f} KB"
        else:
            s = f"{size} B"
        print(f"    [{i}] {f.name:<30} {s}")
    print(f"    [0] Quay lại")
    print()

    choice = input("  Chọn: ").strip()
    if not choice or choice == "0":
        return

    try:
        idx = int(choice) - 1
        log_file = log_files[idx]
    except (ValueError, IndexError):
        return

    print(f"\n  === {log_file.name} (50 dòng cuối) ===\n")
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-50:]:
            print(f"  {line}")
    except Exception as e:
        fail(f"Không đọc được: {e}")

    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  WEB DEPLOY
# ══════════════════════════════════════════════════════════════════════════

WEB_DEPLOY_URL  = "https://deploy.giatocnguyenhuu.vn:8172/msdeploy.axd"
WEB_DEPLOY_SITE = "OpenRag"
WEB_DEPLOY_USER = r"INSTANCE-078585\Administrator"
PRODUCTION_URL  = "http://vanban.fasoft.vn"

def cmd_web_deploy():
    """Build frontend + Web Deploy lên IIS."""
    banner()
    print(f"  {bold('Web Deploy lên IIS')}\n")

    print(f"  Máy chủ:  {WEB_DEPLOY_URL}")
    print(f"  Site:     {WEB_DEPLOY_SITE}")
    print(f"  URL:      {PRODUCTION_URL}")
    print()

    if not confirm("Bắt đầu deploy?", default=True):
        return

    total = 3
    n = 0
    def s(msg): nonlocal n; n += 1; step(n, total, msg)

    # 1. Build frontend
    s("Build giao diện (Vue → wwwroot)")
    if FRONTEND.exists():
        if not run(["npm", "install", "--prefer-offline"], cwd=FRONTEND):
            return
        if not run(["npm", "run", "build"], cwd=FRONTEND):
            return
        ok("Giao diện đã build")
    else:
        warn("Thư mục frontend/ không có — bỏ qua")

    # 2. Chuẩn bị
    s("Chuẩn bị")
    logs_dir = DOTNET_API / "logs"
    data_dir = DOTNET_API / "data"
    logs_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)
    gitkeep = data_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("")
    ok("Thư mục data/ và logs/ sẵn sàng")

    # 3. Web Deploy
    s("Xuất bản qua Web Deploy")

    import getpass
    password = getpass.getpass(f"  Mật khẩu cho {WEB_DEPLOY_USER}: ")
    if not password:
        fail("Chưa nhập mật khẩu!")
        pause()
        return

    result = run([
        "dotnet", "publish", str(DOTNET_API / "OpenRAG.Api.csproj"),
        "/p:PublishProfile=IIS-WebDeploy",
        f"/p:Password={password}",
        "-c", "Release",
    ])

    if result:
        print()
        show_box("Deploy thành công!", [
            f"URL:      {PRODUCTION_URL}",
            f"Site:     {WEB_DEPLOY_SITE}",
            f"Máy chủ:  {WEB_DEPLOY_URL}",
            "",
            "Lưu ý: Dịch vụ ML (Python) cần chạy riêng",
            "       trên server qua NSSM hoặc thủ công.",
        ])
    else:
        print()
        fail("Web Deploy thất bại!")
        print()
        print(f"  Kiểm tra:")
        print(f"    1. Web Deploy đã cài trên server chưa?")
        print(f"    2. Port 8172 đã mở trong firewall?")
        print(f"    3. ASP.NET Core 8.0 Hosting Bundle đã cài?")
        print(f"    4. WebSocket Protocol đã bật trong IIS?")

    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  MENU CHÍNH
# ══════════════════════════════════════════════════════════════════════════

def main_menu():
    while True:
        banner()

        admin_tag = green(" [Admin]") if is_admin() else ""
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"

        print(f"  Python: {py_ver}   Thư mục: {ROOT}{admin_tag}")
        print()
        print(f"  {bold('─── Phát triển (máy local) ───')}")
        print(f"    [1]  Kiểm tra hệ thống")
        print(f"    [2]  Cài đặt dev         (venv + gói + mô hình)")
        print(f"    [3]  Build               (giao diện + .NET)")
        print()
        print(f"  {bold('─── Triển khai máy chủ ───')}")
        print(f"    [4]  {bold('Web Deploy')}        (build + deploy lên IIS)")
        print(f"    [5]  Triển khai ML       (venv + gói trên server)")
        print(f"    [6]  Cài dịch vụ ML      (đăng ký NSSM)")
        print(f"    [7]  Cấu hình IIS        (reverse proxy)")
        print(f"    [8]  Gỡ dịch vụ          (xoá dịch vụ)")
        print()
        print(f"  {bold('─── Quản lý ───')}")
        print(f"    [9]  Trạng thái          (dịch vụ + health check)")
        print(f"   [10]  Khởi động lại       (dừng + chạy lại)")
        print(f"   [11]  Xem nhật ký")
        print()
        print(f"  {bold('─── Chế độ phát triển ───')}")
        print(f"   [12]  Chạy dev            (mở cửa sổ cmd)")
        print(f"   [13]  Dừng dev            (tắt tiến trình)")
        print()
        print(f"    [0]  Thoát")
        print()

        choice = input("  Chọn [0-13]: ").strip()

        if   choice == "1":  check_system()
        elif choice == "2":  cmd_dev_setup()
        elif choice == "3":  cmd_build()
        elif choice == "4":  cmd_web_deploy()
        elif choice == "5":  cmd_server_deploy()
        elif choice == "6":  cmd_install_services()
        elif choice == "7":  cmd_iis_setup()
        elif choice == "8":  cmd_uninstall_services()
        elif choice == "9":  cmd_status()
        elif choice == "10": cmd_restart_services()
        elif choice == "11": cmd_view_logs()
        elif choice == "12": cmd_dev_start()
        elif choice == "13": cmd_dev_stop()
        elif choice == "0":
            print(f"\n  {dim('Tạm biệt!')}\n")
            break
        else:
            warn("Lựa chọn không hợp lệ")
            time.sleep(1)

# ══════════════════════════════════════════════════════════════════════════
#  ĐIỂM VÀO
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="OpenRAG — Trình quản lý triển khai")
    p.add_argument("command", nargs="?", default=None,
                   choices=["setup", "build", "deploy", "webdeploy", "services",
                            "iis", "status", "restart", "start", "stop", "check"])
    p.add_argument("--skip-model", action="store_true",
                   help="Bỏ qua tải mô hình AI")
    p.add_argument("--force", action="store_true",
                   help="Xoá venv cũ, cài lại từ đầu")
    args = p.parse_args()

    if args.command is None:
        main_menu()
    elif args.command == "check":    check_system()
    elif args.command == "setup":    cmd_dev_setup(skip_model=args.skip_model, force=args.force)
    elif args.command == "build":    cmd_build()
    elif args.command == "webdeploy": cmd_web_deploy()
    elif args.command == "deploy":   cmd_server_deploy()
    elif args.command == "services": cmd_install_services()
    elif args.command == "iis":      cmd_iis_setup()
    elif args.command == "status":   cmd_status()
    elif args.command == "restart":  cmd_restart_services()
    elif args.command == "start":    cmd_dev_start()
    elif args.command == "stop":     cmd_dev_stop()
