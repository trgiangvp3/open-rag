#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenRAG — Deploy Manager
=========================
All-in-one: setup, build, deploy, services, IIS — single file, interactive menu.

Usage:
    python scripts/deploy.py              # interactive menu
    python scripts/deploy.py setup        # dev setup (venv + packages + models)
    python scripts/deploy.py build        # build frontend + publish .NET
    python scripts/deploy.py deploy       # full server deploy
    python scripts/deploy.py status       # check services status
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
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).parent.parent.resolve()

# Dev paths
ML_SERVICE = ROOT / "ml_service"
FRONTEND   = ROOT / "frontend"
DOTNET_API = ROOT / "OpenRAG.Api"
PUBLISH    = ROOT / "publish"

# Server paths
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

# Python version limits
PYTHON_MIN = (3, 10)
PYTHON_MAX = (3, 12)

# Ports
API_PORT = 8000
ML_PORT  = 8001
IIS_PORT = 80

# ══════════════════════════════════════════════════════════════════════════
#  TERMINAL UI
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

def ok(msg):    print(f"  {green('V')} {msg}")
def warn(msg):  print(f"  {yellow('!')} {msg}")
def info(msg):  print(f"  {dim('>')} {msg}")
def fail(msg):  print(f"  {red('X')} {msg}")
def skip(msg):  print(f"  {dim('-')} {msg}")

def step(n, total, msg):
    print(f"\n  {bold(cyan(f'[{n}/{total}]'))} {bold(msg)}")

def clear():
    os.system("cls" if os.name == "nt" else "clear")

def pause(msg="  Nhan Enter de tiep tuc..."):
    input(msg)

def confirm(msg, default=False):
    suffix = " (Y/n): " if default else " (y/N): "
    ans = input(f"  {msg}{suffix}").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes")

def banner():
    clear()
    w = 56
    print()
    print(f"  {bg_blue(' ' * w)}")
    print(f"  {bg_blue('   OpenRAG Deploy Manager' + ' ' * (w - 25))}")
    print(f"  {bg_blue(' ' * w)}")
    print()

def show_box(title, lines):
    """In box dep."""
    w = max(len(l) for l in lines) + 4
    w = max(w, len(title) + 4, 50)
    print(f"\n  +{'=' * w}+")
    print(f"  | {bold(title)}{' ' * (w - len(title) - 1)}|")
    print(f"  +{'-' * w}+")
    for l in lines:
        print(f"  | {l}{' ' * (w - len(l) - 1)}|")
    print(f"  +{'=' * w}+")

# ══════════════════════════════════════════════════════════════════════════
#  SHELL HELPERS
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
        fail(f"Exit {e.returncode}: {' '.join(str(c) for c in cmd[:5])}")
        return False
    except FileNotFoundError:
        fail(f"Khong tim thay: {cmd[0]}")
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
#  GPU DETECTION
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
                "label": "CPU only", "gpu_name": ""}

    output = capture([smi])
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", output or "")
    if not m:
        return {"has_gpu": False, "wheel": "cpu", "torch_idx": "https://download.pytorch.org/whl/cpu",
                "label": "CPU only", "gpu_name": ""}

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
#  PYTHON VERSION MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════

def find_compatible_python():
    """Tim Python 3.10-3.12. Tra ve path hoac None."""
    # 1) Python 3.12 da cai rieng
    if PY312_DIR.exists() and (PY312_DIR / "python.exe").exists():
        return str(PY312_DIR / "python.exe")

    # 2) py launcher
    for minor in (12, 11, 10):
        ver = capture(["py", f"-3.{minor}", "--version"])
        if ver:
            return f"py|-3.{minor}"  # special format, split later

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
    """Chuyen python spec thanh list cmd."""
    if not python_spec:
        return None
    if "|" in python_spec:
        return python_spec.split("|")
    return [python_spec]

def download_python312():
    """Tai va cai Python 3.12 vao INSTALL_DIR/python312."""
    installer = Path(tempfile.gettempdir()) / f"python-{PY312_VER}-amd64.exe"

    if not installer.exists():
        info(f"Dang tai Python {PY312_VER}...")
        try:
            urllib.request.urlretrieve(PY312_URL, str(installer))
        except Exception as e:
            fail(f"Tai Python that bai: {e}")
            return None

    info(f"Cai dat Python {PY312_VER} vao {PY312_DIR}...")
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
        fail("Cai dat Python that bai!")
        return None

    exe = PY312_DIR / "python.exe"
    if exe.exists():
        ok(f"Python 3.12 da cai: {exe}")
        return str(exe)
    fail("Khong tim thay python.exe sau khi cai!")
    return None

def ensure_compatible_python():
    """Tim hoac tai Python tuong thich. Tra ve path."""
    py = find_compatible_python()
    if py:
        cmd = get_python_cmd(py)
        ver = capture(cmd + ["--version"])
        ok(f"Python tuong thich: {ver}")
        return py

    v = sys.version_info
    warn(f"Python hien tai: {v.major}.{v.minor}.{v.micro} (can 3.10-3.12)")
    info("PyTorch chua ho tro Python 3.13+")

    if confirm("Tu dong tai Python 3.12?", default=True):
        return download_python312()

    fail("Khong co Python tuong thich!")
    return None

def create_venv(python_spec, venv_path, force=False):
    """Tao venv tu Python tuong thich."""
    venv_python = venv_path / "Scripts" / "python.exe"

    if venv_path.exists() and force:
        info("Xoa venv cu...")
        shutil.rmtree(venv_path)

    if venv_path.exists() and venv_python.exists():
        ver = capture([str(venv_python), "--version"])
        ok(f"venv da ton tai ({ver})")
        return str(venv_python)

    cmd = get_python_cmd(python_spec)
    if not cmd:
        fail("Khong co Python tuong thich!")
        return None

    info(f"Tao venv tai {venv_path}...")
    result = subprocess.run(cmd + ["-m", "venv", str(venv_path)])
    if result.returncode != 0:
        fail("Tao venv that bai!")
        return None

    ok("venv da tao")
    return str(venv_python)

# ══════════════════════════════════════════════════════════════════════════
#  CHECK SYSTEM
# ══════════════════════════════════════════════════════════════════════════

def check_system():
    """Kiem tra toan bo he thong."""
    banner()
    print(f"  {bold('Kiem tra cau hinh may')}\n")

    # Python
    v = sys.version_info
    print(f"  Python hien tai:  {v.major}.{v.minor}.{v.micro}")
    py = find_compatible_python()
    if py:
        cmd = get_python_cmd(py)
        ver = capture(cmd + ["--version"])
        ok(f"Python tuong thich: {ver}")
    else:
        warn(f"Khong co Python 3.10-3.12 (se tu tai khi deploy)")

    # .NET
    dotnet = capture(["dotnet", "--version"])
    if dotnet:
        ok(f".NET SDK: {dotnet}")
    else:
        warn(".NET SDK chua cai")

    # Node.js
    node = capture(["node", "--version"])
    npm = capture(["npm", "--version"])
    if node:
        ok(f"Node.js: {node}  /  npm: {npm}")
    else:
        warn("Node.js chua cai")

    # GPU
    gpu = detect_gpu()
    if gpu["has_gpu"]:
        ok(f"GPU: {gpu['label']}")
    else:
        info("GPU: Khong co (se dung CPU)")

    # Disk
    free_gb = shutil.disk_usage(ROOT).free / 1e9
    fn = ok if free_gb >= 8 else warn
    fn(f"Dia: {free_gb:.1f} GB trong (can ~8 GB)")

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
        ok("Quyen Administrator: Co")
    else:
        info("Quyen Administrator: Khong (can cho cai service/IIS)")

    # Network
    try:
        urllib.request.urlopen("https://pypi.org", timeout=5)
        ok("Ket noi mang: OK")
    except Exception:
        warn("Khong co internet")

    # NSSM
    if NSSM_EXE.exists():
        ok(f"NSSM: {NSSM_EXE}")
    else:
        info("NSSM: Chua cai (can cho Windows Services)")

    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  DEV SETUP
# ══════════════════════════════════════════════════════════════════════════

def cmd_dev_setup(skip_model=False, force=False):
    """Setup moi truong dev: venv + packages + models."""
    banner()
    print(f"  {bold('Dev Setup')}\n")

    total = 7
    n = 0
    def s(msg): nonlocal n; n += 1; step(n, total, msg)

    s("Kiem tra he thong")
    gpu = detect_gpu()
    info(f"GPU: {gpu['label']}")

    s("Tim Python tuong thich")
    py = ensure_compatible_python()
    if not py:
        return

    s("Tao virtual environment")
    for d in [PIP_CACHE, HF_CACHE, NPM_CACHE]:
        d.mkdir(parents=True, exist_ok=True)

    venv_path = ROOT / ".venv"
    venv_python = create_venv(py, venv_path, force=force)
    if not venv_python:
        return

    s(f"Cai PyTorch [{gpu['wheel']}]")
    # Check existing
    existing = capture([venv_python, "-c", "import torch; print(torch.__version__)"])
    if existing and not force:
        ok(f"PyTorch {existing} (da co)")
    else:
        pip_install(venv_python, ["install", "--upgrade", "pip", "--quiet"], PIP_CACHE)
        pip_install(venv_python, ["install", "torch", "--index-url", gpu["torch_idx"]], PIP_CACHE)

    s("Cai Python packages (ml_service)")
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

    s(".NET restore + npm install")
    dotnet_ok = bool(capture(["dotnet", "--version"]))
    node_ok = bool(capture(["node", "--version"]))
    if dotnet_ok and DOTNET_API.exists():
        run(["dotnet", "restore", "--verbosity", "minimal"], cwd=DOTNET_API)
    if node_ok and FRONTEND.exists():
        run(["npm", "install", "--prefer-offline"], env={"npm_config_cache": str(NPM_CACHE)}, cwd=FRONTEND)

    s("Tai AI models")
    if skip_model:
        skip("--skip-model")
    else:
        env = {"HF_HOME": str(HF_CACHE)}
        # Embedding
        slug = EMBEDDING_MODEL.replace("/", "--")
        if (HF_CACHE / "hub").exists() and any((HF_CACHE / "hub").glob(f"models--{slug}")):
            ok(f"Embedding model da co ({EMBEDDING_MODEL})")
        else:
            info(f"Tai {EMBEDDING_MODEL} (~2.3 GB)...")
            run([venv_python, "-c",
                 f"import os; os.environ['HF_HOME']={str(HF_CACHE)!r}; "
                 f"from sentence_transformers import SentenceTransformer; "
                 f"m=SentenceTransformer({EMBEDDING_MODEL!r}); "
                 f"print('dim:', m.get_sentence_embedding_dimension())"], env=env)

        # Reranker
        slug2 = RERANKER_MODEL.replace("/", "--")
        if (HF_CACHE / "hub").exists() and any((HF_CACHE / "hub").glob(f"models--{slug2}")):
            ok(f"Reranker model da co ({RERANKER_MODEL})")
        else:
            info(f"Tai {RERANKER_MODEL} (~560 MB)...")
            run([venv_python, "-c",
                 f"import os; os.environ['HF_HOME']={str(HF_CACHE)!r}; "
                 f"from FlagEmbedding import FlagReranker; "
                 f"r=FlagReranker({RERANKER_MODEL!r}, use_fp16=False); "
                 f"print('ok')"], env=env)

    print(f"\n  {green('V')} {bold('Dev setup hoan tat!')}\n")
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  BUILD
# ══════════════════════════════════════════════════════════════════════════

def cmd_build():
    """Build frontend + publish .NET API."""
    banner()
    print(f"  {bold('Production Build')}\n")

    total = 3
    n = 0
    def s(msg): nonlocal n; n += 1; step(n, total, msg)

    s("Build Frontend (Vue -> wwwroot)")
    if not FRONTEND.exists():
        fail("Thu muc frontend/ khong ton tai!")
        return False
    if not run(["npm", "install", "--prefer-offline"], cwd=FRONTEND):
        return False
    if not run(["npm", "run", "build"], cwd=FRONTEND):
        return False
    ok("Frontend -> OpenRAG.Api/wwwroot")

    s("Publish .NET API")
    api_publish = PUBLISH / "api"
    if not run(["dotnet", "publish", "-c", "Release", "-o", str(api_publish), "--self-contained", "false"],
               cwd=DOTNET_API):
        return False
    ok(f"API -> {api_publish}")

    s("Copy ML Service")
    ml_publish = PUBLISH / "ml_service"
    ml_publish.mkdir(parents=True, exist_ok=True)
    (ml_publish / "rag").mkdir(exist_ok=True)

    # Copy Python files
    for f in ML_SERVICE.glob("*.py"):
        shutil.copy2(f, ml_publish)
    # Copy rag/
    if (ML_SERVICE / "rag").exists():
        shutil.copytree(ML_SERVICE / "rag", ml_publish / "rag", dirs_exist_ok=True)
    # Copy requirements.txt
    shutil.copy2(ML_SERVICE / "requirements.txt", ml_publish)
    # Copy .env
    env_file = ROOT / ".env"
    if env_file.exists():
        shutil.copy2(env_file, ml_publish)
        shutil.copy2(env_file, api_publish)

    # Data dir
    data_pub = PUBLISH / "data"
    data_pub.mkdir(exist_ok=True)
    for d in ["chroma", "uploads", "bm25"]:
        (data_pub / d).mkdir(exist_ok=True)
    db = ROOT / "data" / "openrag.db"
    if db.exists():
        shutil.copy2(db, data_pub)
        ok("Database copied")

    ok("ML service files copied")

    show_box("Build hoan tat!", [
        f"publish/api/           .NET API + wwwroot",
        f"publish/ml_service/    Python ML service",
        f"publish/data/          SQLite + ChromaDB",
        "",
        "Tiep theo: Copy 'publish/' len server",
        "           va chay menu [4] Server Deploy",
    ])
    print()
    pause()
    return True

# ══════════════════════════════════════════════════════════════════════════
#  SERVER DEPLOY
# ══════════════════════════════════════════════════════════════════════════

def cmd_server_deploy():
    """Deploy tren server: tao venv, cai packages, cau hinh."""
    banner()
    print(f"  {bold('Server Deploy')}\n")

    if not is_admin():
        warn("Nen chay voi quyen Administrator!")
        if not confirm("Van tiep tuc?"):
            return

    total = 6
    n = 0
    def s(msg): nonlocal n; n += 1; step(n, total, msg)

    s("Tao thu muc")
    for d in [INSTALL_DIR, INSTALL_DIR / "data", INSTALL_DIR / "data" / "chroma",
              INSTALL_DIR / "data" / "uploads", INSTALL_DIR / "data" / "bm25",
              INSTALL_DIR / "logs"]:
        d.mkdir(parents=True, exist_ok=True)
    ok(f"Thu muc: {INSTALL_DIR}")

    s("Tim Python tuong thich (3.10-3.12)")
    py = ensure_compatible_python()
    if not py:
        return

    s("Tao venv + cai packages")
    ml_dir = INSTALL_DIR / "ml_service"
    venv_path = ml_dir / ".venv"
    venv_python = create_venv(py, venv_path)
    if not venv_python:
        return

    gpu = detect_gpu()
    info(f"GPU: {gpu['label']}")

    pip_install(venv_python, ["install", "--upgrade", "pip", "--quiet"], PIP_CACHE)
    info(f"Cai PyTorch [{gpu['wheel']}]...")
    pip_install(venv_python, ["install", "torch", "--index-url", gpu["torch_idx"]], PIP_CACHE)

    req = ml_dir / "requirements.txt"
    if req.exists():
        info("Cai ML packages...")
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
    ok("Python packages installed")

    s("Tao .env production")
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
        ok(".env created")
    else:
        ok(".env da ton tai")
    shutil.copy2(env_file, ml_dir / ".env")

    s("Cau hinh .NET API")
    api_dir = INSTALL_DIR / "api"
    prod_config = api_dir / "appsettings.Production.json"
    if api_dir.exists() and not prod_config.exists():
        prod_config.write_text(json.dumps({
            "ConnectionStrings": {"Default": "Data Source=../data/openrag.db"},
            "MlService": {"BaseUrl": "http://127.0.0.1:8001"},
            "Urls": "http://127.0.0.1:8000",
            "Logging": {"LogLevel": {"Default": "Warning", "Microsoft.AspNetCore": "Warning"}},
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        ok("appsettings.Production.json created")
    elif api_dir.exists():
        ok("appsettings.Production.json da ton tai")
    else:
        warn(f"{api_dir} chua co - copy publish/api/ vao truoc")

    s("Tai AI models")
    info("Tai embedding model (lan dau mat vai phut)...")
    result = subprocess.run(
        [venv_python, "-c",
         "from sentence_transformers import SentenceTransformer; "
         f"m=SentenceTransformer({EMBEDDING_MODEL!r}); "
         "print('dim:', m.get_sentence_embedding_dimension())"],
        env={**os.environ, "HF_HOME": str(HF_CACHE)},
    )
    if result.returncode == 0:
        ok("Embedding model ready")
    else:
        warn("Tai model that bai - co the thu lai sau")

    print(f"\n  {green('V')} {bold('Server deploy hoan tat!')}\n")
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  NSSM / WINDOWS SERVICES
# ══════════════════════════════════════════════════════════════════════════

def download_nssm():
    """Tai va giai nen NSSM."""
    nssm_dir = INSTALL_DIR / "nssm"
    nssm_dir.mkdir(parents=True, exist_ok=True)

    zip_path = Path(tempfile.gettempdir()) / "nssm-2.24.zip"
    if not zip_path.exists():
        info("Dang tai NSSM...")
        try:
            urllib.request.urlretrieve(NSSM_URL, str(zip_path))
        except Exception as e:
            fail(f"Tai NSSM that bai: {e}")
            return False

    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for member in zf.namelist():
            if member.endswith("win64/nssm.exe"):
                data = zf.read(member)
                (nssm_dir / "nssm.exe").write_bytes(data)
                ok(f"NSSM da giai nen vao {nssm_dir}")
                return True

    fail("Khong tim thay nssm.exe trong file zip!")
    return False

def nssm(args):
    return subprocess.run([str(NSSM_EXE)] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def cmd_install_services():
    """Cai dat Windows Services."""
    banner()
    print(f"  {bold('Cai dat Windows Services')}\n")

    if not is_admin():
        fail("Can quyen Administrator! Right-click > Run as administrator")
        pause()
        return

    # Check NSSM
    if not NSSM_EXE.exists():
        warn("NSSM chua co")
        if confirm("Tu dong tai NSSM?", default=True):
            if not download_nssm():
                pause()
                return
        else:
            fail("Can NSSM de cai Windows Services")
            pause()
            return

    venv_python = INSTALL_DIR / "ml_service" / ".venv" / "Scripts" / "python.exe"
    api_exe = INSTALL_DIR / "api" / "OpenRAG.Api.exe"

    if not venv_python.exists():
        fail(f"Chua co venv: {venv_python}")
        info("Chay menu [4] Server Deploy truoc")
        pause()
        return

    # -- ML Service --
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
          "Python FastAPI ML service (embeddings, search, reranker)"])
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
    ok("OpenRAG-ML installed")

    # -- API Service --
    step(2, 2, "OpenRAG-API (.NET Core)")
    nssm(["stop", "OpenRAG-API"])
    nssm(["remove", "OpenRAG-API", "confirm"])

    if api_exe.exists():
        nssm(["install", "OpenRAG-API", str(api_exe)])
    else:
        warn(f"{api_exe} chua co - dung dotnet thay the")
        dotnet = shutil.which("dotnet") or "dotnet"
        nssm(["install", "OpenRAG-API", dotnet])
        nssm(["set", "OpenRAG-API", "AppParameters",
              str(INSTALL_DIR / "api" / "OpenRAG.Api.dll")])

    nssm(["set", "OpenRAG-API", "AppDirectory", str(INSTALL_DIR / "api")])
    nssm(["set", "OpenRAG-API", "AppEnvironmentExtra",
          "ASPNETCORE_ENVIRONMENT=Production",
          "ASPNETCORE_URLS=http://127.0.0.1:8000"])
    nssm(["set", "OpenRAG-API", "DisplayName", "OpenRAG API Service"])
    nssm(["set", "OpenRAG-API", "Description", "ASP.NET Core API for OpenRAG"])
    nssm(["set", "OpenRAG-API", "Start", "SERVICE_AUTO_START"])
    nssm(["set", "OpenRAG-API", "DependOnService", "OpenRAG-ML"])
    nssm(["set", "OpenRAG-API", "AppStdout", str(INSTALL_DIR / "logs" / "api-stdout.log")])
    nssm(["set", "OpenRAG-API", "AppStderr", str(INSTALL_DIR / "logs" / "api-stderr.log")])
    nssm(["set", "OpenRAG-API", "AppStdoutCreationDisposition", "4"])
    nssm(["set", "OpenRAG-API", "AppStderrCreationDisposition", "4"])
    nssm(["set", "OpenRAG-API", "AppRotateFiles", "1"])
    nssm(["set", "OpenRAG-API", "AppRotateBytes", "10485760"])
    ok("OpenRAG-API installed")

    # Start
    print()
    if confirm("Khoi dong services ngay?", default=True):
        info("Khoi dong OpenRAG-ML...")
        nssm(["start", "OpenRAG-ML"])
        info("Doi 10s de ML service san sang...")
        time.sleep(10)
        info("Khoi dong OpenRAG-API...")
        nssm(["start", "OpenRAG-API"])
        print()
        _show_services_status()

    pause()

def cmd_uninstall_services():
    """Go cai dat Windows Services."""
    banner()
    print(f"  {bold('Go cai dat Windows Services')}\n")

    if not is_admin():
        fail("Can quyen Administrator!")
        pause()
        return

    if not NSSM_EXE.exists():
        fail("NSSM khong co")
        pause()
        return

    if not confirm("Xac nhan go cai dat OpenRAG services?"):
        return

    nssm(["stop", "OpenRAG-API"])
    nssm(["stop", "OpenRAG-ML"])
    nssm(["remove", "OpenRAG-API", "confirm"])
    nssm(["remove", "OpenRAG-ML", "confirm"])
    ok("Services da go cai dat")
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  SERVICE STATUS & MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════

def _show_services_status():
    """Hien thi trang thai services."""
    services = [
        ("OpenRAG-ML",  ML_PORT,  "Python FastAPI"),
        ("OpenRAG-API", API_PORT, ".NET Core API"),
    ]

    print(f"\n  {'Service':<16} {'Port':<8} {'Status':<12} {'Health':<8}")
    print(f"  {'-'*16} {'-'*8} {'-'*12} {'-'*8}")

    for name, port, desc in services:
        # Service status
        if NSSM_EXE.exists():
            r = nssm(["status", name])
            status = r.stdout.strip() if r.returncode == 0 else "NOT FOUND"
        else:
            r = subprocess.run(["sc", "query", name], capture_output=True, text=True)
            if r.returncode != 0:
                status = "NOT FOUND"
            else:
                m = re.search(r"STATE\s+:\s+\d+\s+(\w+)", r.stdout)
                status = m.group(1) if m else "UNKNOWN"

        # Color
        if "RUNNING" in status:
            status_str = green("RUNNING")
        elif "STOPPED" in status:
            status_str = yellow("STOPPED")
        elif "NOT" in status:
            status_str = dim("N/A")
        else:
            status_str = red(status)

        # Health check
        health = ""
        if is_port_open(port):
            health = green("OK")
        elif "RUNNING" in status:
            health = yellow("WAIT")
        else:
            health = dim("-")

        print(f"  {name:<16} {port:<8} {status_str:<24} {health}")

    # IIS
    iis_status = green("LISTENING") if is_port_open(IIS_PORT) else dim("N/A")
    print(f"  {'IIS':<16} {IIS_PORT:<8} {iis_status}")

    print()

def cmd_status():
    """Xem trang thai services."""
    banner()
    print(f"  {bold('Trang thai Services')}")
    _show_services_status()

    # Logs
    log_dir = INSTALL_DIR / "logs"
    if log_dir.exists():
        print(f"  Logs: {log_dir}")
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
    """Restart services."""
    banner()
    print(f"  {bold('Restart Services')}\n")

    if not NSSM_EXE.exists():
        fail("NSSM khong co")
        pause()
        return

    if not is_admin():
        fail("Can quyen Administrator!")
        pause()
        return

    info("Dung services...")
    nssm(["stop", "OpenRAG-API"])
    nssm(["stop", "OpenRAG-ML"])
    time.sleep(2)

    info("Khoi dong OpenRAG-ML...")
    nssm(["start", "OpenRAG-ML"])
    info("Doi 10s...")
    time.sleep(10)

    info("Khoi dong OpenRAG-API...")
    nssm(["start", "OpenRAG-API"])
    time.sleep(2)

    _show_services_status()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  IIS SETUP
# ══════════════════════════════════════════════════════════════════════════

def cmd_iis_setup():
    """Cau hinh IIS reverse proxy."""
    banner()
    print(f"  {bold('Cau hinh IIS Reverse Proxy')}\n")

    if not is_admin():
        fail("Can quyen Administrator!")
        pause()
        return

    step(1, 3, "Kiem tra IIS modules")

    arr_dll = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "inetsrv" / "requestRouter.dll"
    if not arr_dll.exists():
        print()
        warn("Can cai thu cong 2 module IIS:")
        print()
        print(f"  1) {bold('URL Rewrite 2.1')}")
        print(f"     https://www.iis.net/downloads/microsoft/url-rewrite")
        print()
        print(f"  2) {bold('Application Request Routing (ARR) 3.0')}")
        print(f"     https://www.iis.net/downloads/microsoft/application-request-routing")
        print()
        if not confirm("Da cai dat xong?"):
            return
    ok("IIS modules")

    # Enable ARR
    appcmd = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "inetsrv" / "appcmd.exe"
    if appcmd.exists():
        subprocess.run([str(appcmd), "set", "config",
                        "-section:system.webServer/proxy", "/enabled:True",
                        "/commit:apphost"], capture_output=True)
        ok("ARR proxy enabled")

    step(2, 3, "Tao web.config")
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
    ok(f"web.config -> {site_dir}")

    step(3, 3, "Tao IIS Site")
    # Use PowerShell to create site
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
        ok(f"IIS site 'OpenRAG' tren port {IIS_PORT}")
    else:
        fail(f"Loi tao IIS site: {r.stderr.strip()}")

    print()
    show_box("IIS Setup hoan tat!", [
        f"Site:     OpenRAG (port {IIS_PORT})",
        f"Backend:  {backend}",
        f"Dir:      {site_dir}",
        "",
        "Truy cap: http://localhost",
        "          http://<server-ip>",
    ])
    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  DEV SERVICE MANAGER (khoi dong thu cong)
# ══════════════════════════════════════════════════════════════════════════

def cmd_dev_start():
    """Khoi dong services (dev mode, mo cua so cmd)."""
    banner()
    print(f"  {bold('Dev Mode - Khoi dong Services')}\n")

    venv_activate = ROOT / ".venv" / "Scripts" / "activate.bat"

    for name, port, directory, cmd_str in [
        ("ML Service", ML_PORT, ML_SERVICE,
         f'call "{venv_activate}" && python main_ml.py' if venv_activate.exists()
         else "python main_ml.py"),
        ("API", API_PORT, DOTNET_API, "dotnet run"),
        ("Frontend", 5173, FRONTEND, "npm run dev"),
    ]:
        if is_port_open(port):
            ok(f"{name} da chay tren port {port}")
            continue
        if not directory.exists():
            warn(f"{name}: thu muc {directory} khong ton tai")
            continue
        subprocess.Popen(
            f'start "{name}" /D "{directory}" cmd /k "{cmd_str}"',
            shell=True,
        )
        info(f"{name} dang khoi dong tren port {port}...")
        time.sleep(1)

    print()
    pause()

def cmd_dev_stop():
    """Dung tat ca services dev."""
    banner()
    print(f"  {bold('Dung tat ca Services')}\n")

    for name, port in [("API", API_PORT), ("ML", ML_PORT), ("Frontend", 5173)]:
        pids = get_pids_on_port(port)
        if not pids:
            info(f"{name}: khong chay")
            continue
        for pid in pids:
            try:
                subprocess.run(f"taskkill /F /PID {pid}", shell=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok(f"Killed {name} PID {pid}")
            except Exception:
                fail(f"Khong the kill PID {pid}")
    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  VIEW LOGS
# ══════════════════════════════════════════════════════════════════════════

def cmd_view_logs():
    """Xem logs."""
    banner()
    print(f"  {bold('Xem Logs')}\n")

    log_dir = INSTALL_DIR / "logs"
    if not log_dir.exists():
        warn("Thu muc logs chua ton tai")
        pause()
        return

    log_files = sorted(log_dir.glob("*.log"))
    if not log_files:
        info("Khong co file log nao")
        pause()
        return

    print("  Chon file log:\n")
    for i, f in enumerate(log_files, 1):
        size = f.stat().st_size
        if size > 1024*1024:
            s = f"{size/1024/1024:.1f} MB"
        elif size > 1024:
            s = f"{size/1024:.0f} KB"
        else:
            s = f"{size} B"
        print(f"    [{i}] {f.name:<30} {s}")
    print(f"    [0] Quay lai")
    print()

    choice = input("  Chon: ").strip()
    if not choice or choice == "0":
        return

    try:
        idx = int(choice) - 1
        log_file = log_files[idx]
    except (ValueError, IndexError):
        return

    # Show last 50 lines
    print(f"\n  === {log_file.name} (50 dong cuoi) ===\n")
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-50:]:
            print(f"  {line}")
    except Exception as e:
        fail(f"Khong doc duoc: {e}")

    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  MAIN MENU
# ══════════════════════════════════════════════════════════════════════════

def main_menu():
    while True:
        banner()

        admin_tag = green(" [Admin]") if is_admin() else ""
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"

        print(f"  Python: {py_ver}   Root: {ROOT}{admin_tag}")
        print()
        print(f"  {bold('--- Dev (may local) ---')}")
        print(f"    [1] Kiem tra he thong")
        print(f"    [2] Dev Setup          (venv + packages + models)")
        print(f"    [3] Build              (frontend + .NET publish)")
        print()
        print(f"  {bold('--- Server Deploy ---')}")
        print(f"    [4] Server Deploy      (venv + packages tren server)")
        print(f"    [5] Cai Windows Svc    (dang ky NSSM services)")
        print(f"    [6] Cau hinh IIS       (reverse proxy)")
        print(f"    [7] Go Windows Svc     (xoa services)")
        print()
        print(f"  {bold('--- Quan ly ---')}")
        print(f"    [8] Trang thai         (services + health check)")
        print(f"    [9] Restart Services   (stop + start)")
        print(f"   [10] Xem Logs")
        print()
        print(f"  {bold('--- Dev Mode ---')}")
        print(f"   [11] Start dev          (mo cua so cmd)")
        print(f"   [12] Stop dev           (kill processes)")
        print()
        print(f"    [0] Thoat")
        print()

        choice = input("  Chon [0-12]: ").strip()

        if   choice == "1":  check_system()
        elif choice == "2":  cmd_dev_setup()
        elif choice == "3":  cmd_build()
        elif choice == "4":  cmd_server_deploy()
        elif choice == "5":  cmd_install_services()
        elif choice == "6":  cmd_iis_setup()
        elif choice == "7":  cmd_uninstall_services()
        elif choice == "8":  cmd_status()
        elif choice == "9":  cmd_restart_services()
        elif choice == "10": cmd_view_logs()
        elif choice == "11": cmd_dev_start()
        elif choice == "12": cmd_dev_stop()
        elif choice == "0":
            print(f"\n  {dim('Bye!')}\n")
            break
        else:
            warn("Lua chon khong hop le")
            time.sleep(1)

# ══════════════════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="OpenRAG Deploy Manager")
    p.add_argument("command", nargs="?", default=None,
                   choices=["setup", "build", "deploy", "services",
                            "iis", "status", "restart", "start", "stop", "check"])
    p.add_argument("--skip-model", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    if args.command is None:
        main_menu()
    elif args.command == "check":    check_system()
    elif args.command == "setup":    cmd_dev_setup(skip_model=args.skip_model, force=args.force)
    elif args.command == "build":    cmd_build()
    elif args.command == "deploy":   cmd_server_deploy()
    elif args.command == "services": cmd_install_services()
    elif args.command == "iis":      cmd_iis_setup()
    elif args.command == "status":   cmd_status()
    elif args.command == "restart":  cmd_restart_services()
    elif args.command == "start":    cmd_dev_start()
    elif args.command == "stop":     cmd_dev_stop()
