#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenRAG — Quản lý máy chủ
===========================
Script chạy trực tiếp trên server. Quản lý ML service, models, NSSM.

Cách dùng:
    python server.py              # menu tương tác
    python server.py setup        # cài ML service (venv + packages + models)
    python server.py services     # đăng ký NSSM services
    python server.py status       # kiểm tra trạng thái
    python server.py cleanup      # dọn dẹp cài đặt cũ
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
import time
import urllib.request
import zipfile
from pathlib import Path

# Force UTF-8 on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ══════════════════════════════════════════════════════════════════════════
#  ĐƯỜNG DẪN
# ══════════════════════════════════════════════════════════════════════════

# Script này nằm tại: <IIS_SITE>/scripts/server.py
SCRIPT_DIR = Path(__file__).parent.resolve()

# IIS site = thư mục cha của scripts/
IIS_SITE   = SCRIPT_DIR.parent

# ML service, Python, models, NSSM — tách khỏi IIS site
ML_ROOT    = Path(r"C:\OpenRAG")
ML_DIR     = ML_ROOT / "ml_service"
MODELS_DIR = ML_ROOT / "models"
PY312_DIR  = ML_ROOT / "python312"
NSSM_EXE   = ML_ROOT / "nssm" / "nssm.exe"
PIP_CACHE  = ML_ROOT / "pip-cache"

# Python 3.12 download
PY312_VER = "3.12.10"
PY312_URL = f"https://www.python.org/ftp/python/{PY312_VER}/python-{PY312_VER}-amd64.exe"
NSSM_URL  = "https://nssm.cc/release/nssm-2.24.zip"

# Models
EMBEDDING_MODEL = "BAAI/bge-m3"
RERANKER_MODEL  = "BAAI/bge-reranker-v2-m3"

# Python version
PYTHON_MIN = (3, 10)
PYTHON_MAX = (3, 12)

# Ports
API_PORT = 8000
ML_PORT  = 8001

# ══════════════════════════════════════════════════════════════════════════
#  GIAO DIỆN
# ══════════════════════════════════════════════════════════════════════════

_COLOR = sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR"))

def _c(code, t):  return f"\033[{code}m{t}\033[0m" if _COLOR else str(t)
def green(t):      return _c("32", t)
def yellow(t):     return _c("33", t)
def red(t):        return _c("31", t)
def cyan(t):       return _c("36", t)
def bold(t):       return _c("1",  t)
def dim(t):        return _c("2",  t)
def bg_blue(t):    return _c("44;97", t)

def ok(msg):   print(f"  {green('✓')} {msg}")
def warn(msg): print(f"  {yellow('⚠')} {msg}")
def info(msg): print(f"  {dim('›')} {msg}")
def fail(msg): print(f"  {red('✗')} {msg}")

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
    print(f"  {bg_blue('   OpenRAG — Quản lý máy chủ' + ' ' * (w - 29))}")
    print(f"  {bg_blue(' ' * w)}")
    print()

# ══════════════════════════════════════════════════════════════════════════
#  HÀM HỖ TRỢ
# ══════════════════════════════════════════════════════════════════════════

def run(cmd, env=None, cwd=None):
    merged = {**os.environ, **(env or {})}
    resolved = shutil.which(cmd[0])
    if resolved:
        cmd = [resolved] + cmd[1:]
    try:
        subprocess.run(cmd, env=merged, cwd=str(cwd or ML_ROOT), check=True)
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

def pip_install(python_exe, args):
    return run([str(python_exe), "-m", "pip"] + args + ["--cache-dir", str(PIP_CACHE)])

def nssm(args):
    return subprocess.run([str(NSSM_EXE)] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

# ══════════════════════════════════════════════════════════════════════════
#  GPU
# ══════════════════════════════════════════════════════════════════════════

def detect_gpu():
    smi = next((p for p in [
        shutil.which("nvidia-smi"),
        r"C:\Windows\System32\nvidia-smi.exe",
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
    ] if p and Path(p).exists()), None)

    if not smi:
        return {"has_gpu": False, "wheel": "cpu",
                "torch_idx": "https://download.pytorch.org/whl/cpu", "label": "Chỉ CPU"}

    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", capture([smi]) or "")
    if not m:
        return {"has_gpu": False, "wheel": "cpu",
                "torch_idx": "https://download.pytorch.org/whl/cpu", "label": "Chỉ CPU"}

    major, minor = int(m.group(1)), int(m.group(2))
    if major >= 13 or (major == 12 and minor >= 4):   wheel = "cu124"
    elif major == 12 and minor >= 1:                   wheel = "cu121"
    else:                                              wheel = "cu118"

    name = capture([smi, "--query-gpu=name", "--format=csv,noheader"])
    name = name.splitlines()[0].strip() if name else "NVIDIA GPU"
    return {"has_gpu": True, "wheel": wheel,
            "torch_idx": f"https://download.pytorch.org/whl/{wheel}",
            "label": f"{name} (CUDA {major}.{minor})"}

# ══════════════════════════════════════════════════════════════════════════
#  PYTHON 3.12
# ══════════════════════════════════════════════════════════════════════════

def _download_progress(block_num, block_size, total_size):
    if total_size > 0:
        pct = min(100, block_num * block_size * 100 // total_size)
        mb = block_num * block_size / 1024 / 1024
        print(f"\r  › Đang tải: {mb:.0f}/{total_size/1024/1024:.0f} MB ({pct}%)", end="", flush=True)

def find_compatible_python():
    """Tìm Python 3.10-3.12."""
    # 1) Python 3.12 đã cài trong C:\OpenRAG\python312
    exe = PY312_DIR / "python.exe"
    if exe.exists():
        return str(exe)

    # 2) py launcher
    for minor in (12, 11, 10):
        if capture(["py", f"-3.{minor}", "--version"]):
            return f"py|-3.{minor}"

    # 3) python trong PATH
    ver = capture(["python", "--version"])
    if ver:
        m = re.match(r"Python (\d+)\.(\d+)", ver)
        if m and PYTHON_MIN <= (int(m.group(1)), int(m.group(2))) <= PYTHON_MAX:
            return "python"
    return None

def get_python_cmd(spec):
    if not spec: return None
    return spec.split("|") if "|" in spec else [spec]

def download_python312():
    """Tải và cài Python 3.12."""
    installer = Path(tempfile.gettempdir()) / f"python-{PY312_VER}-amd64.exe"

    if not installer.exists() or installer.stat().st_size < 1_000_000:
        info(f"Đang tải Python {PY312_VER} (~25 MB)...")
        try:
            urllib.request.urlretrieve(PY312_URL, str(installer), _download_progress)
            print()
            ok(f"Đã tải: {installer}")
        except Exception as e:
            print()
            fail(f"Tải thất bại: {e}")
            info(f"Tải thủ công: {PY312_URL}")
            return None
    else:
        ok(f"Installer đã có: {installer}")

    info(f"Cài đặt vào {PY312_DIR}...")
    PY312_DIR.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(installer), "/quiet", f"TargetDir={PY312_DIR}",
                        "InstallAllUsers=0", "Include_launcher=0", "Include_test=0",
                        "Include_doc=0", "Include_tcltk=0", "CompileAll=0",
                        "Shortcuts=0", "AssociateFiles=0"])
    if r.returncode != 0:
        fail(f"Cài đặt thất bại (exit {r.returncode})!")
        info("Thử chạy lại với quyền Administrator")
        return None

    exe = PY312_DIR / "python.exe"
    if exe.exists():
        ok(f"Đã cài: {capture([str(exe), '--version'])}")
        return str(exe)
    fail("Không tìm thấy python.exe sau khi cài!")
    return None

def ensure_python():
    """Tìm hoặc tải Python tương thích."""
    py = find_compatible_python()
    if py:
        cmd = get_python_cmd(py)
        ok(f"Python: {capture(cmd + ['--version'])}")
        return py

    v = sys.version_info
    warn(f"Python hiện tại: {v.major}.{v.minor} (cần 3.10–3.12 cho PyTorch)")

    if confirm("Tự động tải Python 3.12?", default=True):
        result = download_python312()
        if not result:
            pause()
        return result

    fail("Không có Python tương thích!")
    pause()
    return None

def create_venv(python_spec, venv_path, force=False):
    venv_python = venv_path / "Scripts" / "python.exe"

    if venv_path.exists() and force:
        info("Xoá venv cũ...")
        shutil.rmtree(venv_path)

    if venv_path.exists() and venv_python.exists():
        ok(f"venv đã có ({capture([str(venv_python), '--version'])})")
        return str(venv_python)

    cmd = get_python_cmd(python_spec)
    if not cmd:
        return None

    info(f"Tạo venv tại {venv_path}...")
    r = subprocess.run(cmd + ["-m", "venv", str(venv_path)])
    if r.returncode != 0:
        fail("Tạo venv thất bại!")
        return None
    ok("Đã tạo venv")
    return str(venv_python)

# ══════════════════════════════════════════════════════════════════════════
#  [1] KIỂM TRA HỆ THỐNG
# ══════════════════════════════════════════════════════════════════════════

def cmd_check():
    banner()
    print(f"  {bold('Kiểm tra máy chủ')}\n")

    print(f"  IIS site:   {IIS_SITE}")
    print(f"  ML root:    {ML_ROOT}")
    print()

    # Python
    v = sys.version_info
    print(f"  Python hệ thống: {v.major}.{v.minor}.{v.micro}")
    py = find_compatible_python()
    if py:
        ok(f"Python tương thích: {capture(get_python_cmd(py) + ['--version'])}")
    else:
        warn("Không có Python 3.10–3.12 (sẽ tự tải)")

    # .NET
    dotnet = capture(["dotnet", "--version"])
    ok(f".NET: {dotnet}") if dotnet else warn(".NET chưa cài")

    # GPU
    gpu = detect_gpu()
    ok(f"GPU: {gpu['label']}") if gpu["has_gpu"] else info(f"GPU: {gpu['label']}")

    # Disk + RAM
    free_gb = shutil.disk_usage(ML_ROOT if ML_ROOT.exists() else Path("C:\\")).free / 1e9
    (ok if free_gb >= 8 else warn)(f"Ổ đĩa: {free_gb:.1f} GB trống")

    try:
        out = capture(["wmic", "OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory", "/Value"])
        tm = re.search(r"TotalVisibleMemorySize=(\d+)", out)
        fm = re.search(r"FreePhysicalMemory=(\d+)", out)
        if tm and fm:
            (ok if int(fm.group(1))/1e6 >= 4 else warn)(
                f"RAM: {int(fm.group(1))/1e6:.1f} / {int(tm.group(1))/1e6:.1f} GB")
    except Exception:
        pass

    # Admin
    ok("Administrator: Có") if is_admin() else info("Administrator: Không")

    # NSSM
    ok(f"NSSM: {NSSM_EXE}") if NSSM_EXE.exists() else info("NSSM: Chưa cài")

    # Ports
    print()
    for name, port in [("API", API_PORT), ("ML", ML_PORT)]:
        status = green("ĐANG NGHE") if is_port_open(port) else dim("tắt")
        print(f"  Cổng {port} ({name}): {status}")

    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  [2] CÀI ĐẶT ML SERVICE
# ══════════════════════════════════════════════════════════════════════════

def cmd_setup():
    banner()
    print(f"  {bold('Cài đặt ML Service')}\n")

    if not is_admin():
        warn("Nên chạy với quyền Administrator!")
        if not confirm("Vẫn tiếp tục?"):
            return

    total = 5
    n = 0
    def s(msg): nonlocal n; n += 1; step(n, total, msg)

    s("Tạo thư mục")
    for d in [ML_ROOT, ML_DIR, MODELS_DIR, ML_ROOT / "logs", PIP_CACHE]:
        d.mkdir(parents=True, exist_ok=True)
    ok(f"Thư mục: {ML_ROOT}")

    s("Tìm Python tương thích (3.10–3.12)")
    py = ensure_python()
    if not py:
        return

    s("Tạo venv + cài gói")
    venv_path = ML_DIR / ".venv"
    venv_python = create_venv(py, venv_path)
    if not venv_python:
        return

    gpu = detect_gpu()
    info(f"GPU: {gpu['label']}")

    pip_install(venv_python, ["install", "--upgrade", "pip", "--quiet"])
    info(f"Cài PyTorch [{gpu['wheel']}]...")
    pip_install(venv_python, ["install", "torch", "--index-url", gpu["torch_idx"]])

    # Copy ML files từ IIS site nếu có
    ml_src = IIS_SITE / "ml_service"
    req = ML_DIR / "requirements.txt"
    if ml_src.exists() and ml_src != ML_DIR:
        info(f"Sao chép ML files từ {ml_src}...")
        for f in ml_src.glob("*.py"):
            shutil.copy2(f, ML_DIR)
        if (ml_src / "rag").exists():
            (ML_DIR / "rag").mkdir(exist_ok=True)
            shutil.copytree(ml_src / "rag", ML_DIR / "rag", dirs_exist_ok=True)
        if (ml_src / "requirements.txt").exists():
            shutil.copy2(ml_src / "requirements.txt", ML_DIR)
        ok("Đã sao chép ML files")

    if req.exists():
        info("Cài gói ML...")
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
            pip_install(venv_python, ["install"] + pkgs)
    ok("Đã cài gói Python")

    s("Tạo .env")
    env_file = ML_ROOT / ".env"
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
    shutil.copy2(env_file, ML_DIR / ".env")

    s("Tải mô hình AI")
    info(f"Thư mục mô hình: {MODELS_DIR}")
    info("Đang tải mô hình nhúng (lần đầu mất vài phút)...")
    result = subprocess.run(
        [venv_python, "-c",
         "from sentence_transformers import SentenceTransformer; "
         f"m=SentenceTransformer({EMBEDDING_MODEL!r}); "
         "print('dim:', m.get_sentence_embedding_dimension())"],
        env={**os.environ, "HF_HOME": str(MODELS_DIR)},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode == 0:
        ok("Mô hình nhúng sẵn sàng")
    else:
        warn("Tải mô hình thất bại!")
        if result.stderr:
            for line in result.stderr.strip().splitlines()[-10:]:
                print(f"    {line}")
        info("Thử chạy lại hoặc kiểm tra kết nối mạng")

    print(f"\n  {green('✓')} {bold('Cài đặt ML hoàn tất!')}")
    print(f"\n  Tiếp theo: chạy mục [3] Cài dịch vụ NSSM\n")
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  [3] CÀI DỊCH VỤ NSSM
# ══════════════════════════════════════════════════════════════════════════

def download_nssm():
    nssm_dir = ML_ROOT / "nssm"
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
                (nssm_dir / "nssm.exe").write_bytes(zf.read(member))
                ok(f"NSSM → {nssm_dir}")
                return True
    fail("Không tìm thấy nssm.exe trong zip!")
    return False

def cmd_services():
    banner()
    print(f"  {bold('Cài dịch vụ Windows (NSSM)')}\n")

    if not is_admin():
        fail("Cần quyền Administrator!")
        pause()
        return

    if not NSSM_EXE.exists():
        if confirm("Tự động tải NSSM?", default=True):
            if not download_nssm():
                pause()
                return
        else:
            pause()
            return

    venv_python = ML_DIR / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        fail(f"Chưa có venv: {venv_python}")
        info("Chạy mục [2] Cài đặt ML trước")
        pause()
        return

    # -- ML Service --
    step(1, 2, "OpenRAG-ML (Python FastAPI)")
    nssm(["stop", "OpenRAG-ML"])
    nssm(["remove", "OpenRAG-ML", "confirm"])

    nssm(["install", "OpenRAG-ML", str(venv_python)])
    nssm(["set", "OpenRAG-ML", "AppParameters", "main_ml.py"])
    nssm(["set", "OpenRAG-ML", "AppDirectory", str(ML_DIR)])
    nssm(["set", "OpenRAG-ML", "AppEnvironmentExtra",
          f"DOTENV_PATH={ML_ROOT}\\.env", f"HF_HOME={MODELS_DIR}"])
    nssm(["set", "OpenRAG-ML", "DisplayName", "OpenRAG ML Service"])
    nssm(["set", "OpenRAG-ML", "Description",
          "Python FastAPI ML service (embeddings, search, reranker)"])
    nssm(["set", "OpenRAG-ML", "Start", "SERVICE_AUTO_START"])
    nssm(["set", "OpenRAG-ML", "AppStdout", str(ML_ROOT / "logs" / "ml-stdout.log")])
    nssm(["set", "OpenRAG-ML", "AppStderr", str(ML_ROOT / "logs" / "ml-stderr.log")])
    nssm(["set", "OpenRAG-ML", "AppStdoutCreationDisposition", "4"])
    nssm(["set", "OpenRAG-ML", "AppStderrCreationDisposition", "4"])
    nssm(["set", "OpenRAG-ML", "AppRotateFiles", "1"])
    nssm(["set", "OpenRAG-ML", "AppRotateBytes", "10485760"])
    nssm(["set", "OpenRAG-ML", "AppStopMethodSkip", "6"])
    nssm(["set", "OpenRAG-ML", "AppStopMethodConsole", "5000"])
    nssm(["set", "OpenRAG-ML", "AppStopMethodWindow", "5000"])
    nssm(["set", "OpenRAG-ML", "AppStopMethodThreads", "5000"])
    ok("Đã cài OpenRAG-ML")

    # -- API Service (chỉ nếu không dùng IIS in-process) --
    step(2, 2, "Kiểm tra API")
    api_dll = IIS_SITE / "OpenRAG.Api.dll"
    web_config = IIS_SITE / "web.config"
    if api_dll.exists() and web_config.exists():
        ok(f"API chạy qua IIS (in-process) tại {IIS_SITE}")
        info("Không cần NSSM cho API — IIS quản lý trực tiếp")
    else:
        warn(f"API chưa deploy tại {IIS_SITE}")
        info("Chạy Web Deploy từ máy dev để đẩy API lên")

    # Start ML
    print()
    if confirm("Khởi động ML service ngay?", default=True):
        info("Đang khởi động OpenRAG-ML...")
        nssm(["start", "OpenRAG-ML"])
        time.sleep(5)
        _show_status()

    pause()

# ══════════════════════════════════════════════════════════════════════════
#  [4] TRẠNG THÁI
# ══════════════════════════════════════════════════════════════════════════

def _show_status():
    print(f"\n  {'Dịch vụ':<16} {'Cổng':<8} {'Trạng thái':<15} {'Sức khoẻ'}")
    print(f"  {'-'*16} {'-'*8} {'-'*15} {'-'*8}")

    # ML Service (NSSM)
    if NSSM_EXE.exists():
        r = nssm(["status", "OpenRAG-ML"])
        status = r.stdout.strip() if r.returncode == 0 else "KHÔNG CÓ"
    else:
        status = "NSSM N/A"

    if "RUNNING" in status:    st = green("ĐANG CHẠY")
    elif "STOPPED" in status:  st = yellow("ĐÃ DỪNG")
    else:                      st = dim(status)

    health = green("OK") if is_port_open(ML_PORT) else (yellow("CHỜ") if "RUNNING" in status else dim("–"))
    print(f"  {'OpenRAG-ML':<16} {ML_PORT:<8} {st:<27} {health}")

    # API (IIS)
    api_status = green("ĐANG NGHE") if is_port_open(API_PORT) else dim("tắt")
    print(f"  {'API (IIS)':<16} {API_PORT:<8} {api_status}")
    print()

def cmd_status():
    banner()
    print(f"  {bold('Trạng thái')}")
    _show_status()

    # Logs
    log_dir = ML_ROOT / "logs"
    if log_dir.exists():
        logs = sorted(log_dir.glob("*.log"))
        if logs:
            print(f"  Nhật ký: {log_dir}")
            for f in logs:
                sz = f.stat().st_size
                s = f"{sz/1024/1024:.1f} MB" if sz > 1024*1024 else f"{sz/1024:.0f} KB" if sz > 1024 else f"{sz} B"
                print(f"    {f.name:<25} {s}")
    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  [5] RESTART
# ══════════════════════════════════════════════════════════════════════════

def cmd_restart():
    banner()
    print(f"  {bold('Khởi động lại ML Service')}\n")

    if not NSSM_EXE.exists():
        fail("NSSM không có")
        pause()
        return
    if not is_admin():
        fail("Cần quyền Administrator!")
        pause()
        return

    info("Đang dừng...")
    nssm(["stop", "OpenRAG-ML"])
    time.sleep(2)
    info("Đang khởi động...")
    nssm(["start", "OpenRAG-ML"])
    time.sleep(5)
    _show_status()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  [6] GỠ DỊCH VỤ
# ══════════════════════════════════════════════════════════════════════════

def cmd_uninstall():
    banner()
    print(f"  {bold('Gỡ dịch vụ')}\n")

    if not is_admin():
        fail("Cần quyền Administrator!")
        pause()
        return
    if not NSSM_EXE.exists():
        fail("NSSM không có")
        pause()
        return
    if not confirm("Xác nhận gỡ dịch vụ OpenRAG-ML?"):
        return

    nssm(["stop", "OpenRAG-ML"])
    nssm(["remove", "OpenRAG-ML", "confirm"])
    ok("Đã gỡ dịch vụ")
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  [7] XEM NHẬT KÝ
# ══════════════════════════════════════════════════════════════════════════

def cmd_logs():
    banner()
    print(f"  {bold('Xem nhật ký')}\n")

    log_dir = ML_ROOT / "logs"
    if not log_dir.exists():
        warn("Chưa có thư mục logs")
        pause()
        return

    log_files = sorted(log_dir.glob("*.log"))
    if not log_files:
        info("Không có file nhật ký")
        pause()
        return

    for i, f in enumerate(log_files, 1):
        sz = f.stat().st_size
        s = f"{sz/1024/1024:.1f} MB" if sz > 1024*1024 else f"{sz/1024:.0f} KB"
        print(f"    [{i}] {f.name:<30} {s}")
    print(f"    [0] Quay lại")
    print()

    choice = input("  Chọn: ").strip()
    if not choice or choice == "0":
        return
    try:
        log_file = log_files[int(choice) - 1]
    except (ValueError, IndexError):
        return

    print(f"\n  === {log_file.name} (50 dòng cuối) ===\n")
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-50:]:
        print(f"  {line}")
    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  [8] DỌN DẸP
# ══════════════════════════════════════════════════════════════════════════

def cmd_cleanup():
    banner()
    print(f"  {bold('Dọn dẹp cài đặt cũ')}\n")

    if not is_admin():
        fail("Cần quyền Administrator!")
        pause()
        return

    _local = Path(os.environ.get("LOCALAPPDATA", ""))
    old_paths = [
        (ML_ROOT / "api",      "Thư mục api/ cũ"),
        (ML_ROOT / "iis-site", "Thư mục iis-site/ cũ"),
        (ML_ROOT / "data",     "Thư mục data/ cũ (giờ dùng AppData/ trong IIS site)"),
        (_local / "openrag",   "Cache cũ trong %LOCALAPPDATA%"),
    ]

    found = [(p, d) for p, d in old_paths if p.exists()]
    if not found:
        ok("Không có gì cần dọn dẹp")
        pause()
        return

    print("  Tìm thấy:\n")
    for i, (path, desc) in enumerate(found, 1):
        try:
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            s = f"{size/1024/1024:.0f} MB" if size > 1024*1024 else f"{size/1024:.0f} KB"
        except Exception:
            s = "?"
        print(f"    [{i}] {path}  ({s})")
        print(f"        {desc}")
        print()

    print(f"    [A] Xoá tất cả")
    print(f"    [0] Huỷ\n")

    choice = input("  Chọn: ").strip()
    if not choice or choice == "0":
        return

    targets = [p for p, _ in found] if choice.lower() == "a" else []
    if not targets:
        try:
            targets = [found[int(choice) - 1][0]]
        except (ValueError, IndexError):
            return

    for path in targets:
        if confirm(f"Xoá {path}?"):
            try:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
                ok(f"Đã xoá: {path}")
            except Exception as e:
                fail(f"Lỗi: {e}")
    print()
    pause()

# ══════════════════════════════════════════════════════════════════════════
#  MENU
# ══════════════════════════════════════════════════════════════════════════

def main_menu():
    while True:
        banner()

        admin_tag = green(" [Admin]") if is_admin() else ""
        print(f"  IIS site: {IIS_SITE}")
        print(f"  ML root:  {ML_ROOT}{admin_tag}")
        print()
        print(f"  {bold('─── Cài đặt ───')}")
        print(f"    [1]  Kiểm tra hệ thống")
        print(f"    [2]  Cài đặt ML          (Python + venv + models)")
        print(f"    [3]  Cài dịch vụ NSSM    (đăng ký Windows Service)")
        print()
        print(f"  {bold('─── Quản lý ───')}")
        print(f"    [4]  Trạng thái          (services + health check)")
        print(f"    [5]  Khởi động lại ML")
        print(f"    [6]  Gỡ dịch vụ")
        print(f"    [7]  Xem nhật ký")
        print(f"    [8]  Dọn dẹp cũ")
        print()
        print(f"    [0]  Thoát")
        print()

        choice = input("  Chọn [0-8]: ").strip()

        if   choice == "1": cmd_check()
        elif choice == "2": cmd_setup()
        elif choice == "3": cmd_services()
        elif choice == "4": cmd_status()
        elif choice == "5": cmd_restart()
        elif choice == "6": cmd_uninstall()
        elif choice == "7": cmd_logs()
        elif choice == "8": cmd_cleanup()
        elif choice == "0":
            print(f"\n  {dim('Tạm biệt!')}\n")
            break
        else:
            time.sleep(0.5)

# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="OpenRAG — Quản lý máy chủ")
    p.add_argument("command", nargs="?", default=None,
                   choices=["check", "setup", "services", "status",
                            "restart", "uninstall", "logs", "cleanup"])
    args = p.parse_args()

    if   args.command is None:       main_menu()
    elif args.command == "check":    cmd_check()
    elif args.command == "setup":    cmd_setup()
    elif args.command == "services": cmd_services()
    elif args.command == "status":   cmd_status()
    elif args.command == "restart":  cmd_restart()
    elif args.command == "uninstall":cmd_uninstall()
    elif args.command == "logs":     cmd_logs()
    elif args.command == "cleanup":  cmd_cleanup()
