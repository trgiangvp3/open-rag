"""OpenRAG Service Manager"""

import subprocess
import os
import sys
import signal
import socket
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
API_DIR = os.path.join(ROOT, "OpenRAG.Api")
ML_DIR = os.path.join(ROOT, "ml_service")
FE_DIR = os.path.join(ROOT, "frontend")
VENV_ACTIVATE = os.path.join(ML_DIR, ".venv", "Scripts", "activate.bat")

API_PORT = 8000
ML_PORT = 8001
FE_PORT = 5173

SERVICES = {
    "API":      {"port": API_PORT, "dir": API_DIR},
    "ML":       {"port": ML_PORT, "dir": ML_DIR},
    "Frontend": {"port": FE_PORT, "dir": FE_DIR},
}


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def get_pids_on_port(port: int) -> list[int]:
    """Get PIDs listening on a given port using netstat."""
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


def view_status():
    print("\n  -- Service Status ------------------------------------\n")
    for name, info in SERVICES.items():
        pids = get_pids_on_port(info["port"])
        if pids:
            pid_str = ", ".join(str(p) for p in pids)
            print(f"   [OK]  {name:<10} http://localhost:{info['port']}   PID: {pid_str}")
        else:
            print(f"   [--]  {name:<10} http://localhost:{info['port']}   Not running")
    print("\n  ------------------------------------------------------\n")


def start_service(name: str):
    info = SERVICES[name]
    if is_port_open(info["port"]):
        print(f"   {name} is already running on port {info['port']}")
        return

    if name == "API":
        subprocess.Popen(
            f'start "{name}" /D "{info["dir"]}" cmd /k dotnet run',
            shell=True,
        )
    elif name == "ML":
        if os.path.exists(VENV_ACTIVATE):
            cmd = f'start "{name}" /D "{info["dir"]}" cmd /k "call .venv\\Scripts\\activate.bat && python main_ml.py"'
        else:
            cmd = f'start "{name}" /D "{info["dir"]}" cmd /k python main_ml.py'
        subprocess.Popen(cmd, shell=True)
    elif name == "Frontend":
        subprocess.Popen(
            f'start "{name}" /D "{info["dir"]}" cmd /k npm run dev',
            shell=True,
        )
    print(f"   {name} starting on http://localhost:{info['port']}")


def kill_service(name: str):
    info = SERVICES[name]
    pids = get_pids_on_port(info["port"])
    if not pids:
        print(f"   {name}: no process on port {info['port']}")
        return
    for pid in pids:
        try:
            subprocess.run(f"taskkill /F /PID {pid}", shell=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"   Killed {name} PID {pid}")
        except Exception:
            print(f"   Failed to kill PID {pid}")


def build_api():
    print("\n  Building API (dotnet publish)...")
    print("  " + "-" * 50)
    result = subprocess.run(
        "dotnet publish -c Release -o publish\\api",
        shell=True, cwd=API_DIR,
    )
    if result.returncode == 0:
        print("\n  Build succeeded: publish\\api\\")
    else:
        print("\n  Build FAILED.")


def build_frontend():
    print("\n  Building Frontend (npm run build)...")
    print("  " + "-" * 50)
    result = subprocess.run("npm run build", shell=True, cwd=FE_DIR)
    if result.returncode == 0:
        print("\n  Build succeeded.")
    else:
        print("\n  Build FAILED.")


def show_menu():
    os.system("cls" if os.name == "nt" else "clear")
    print("""
  +==================================================+
  |          OpenRAG Service Manager                  |
  +==================================================+
  |                                                   |
  |   [1]  View status      (all services)            |
  |   [2]  Start API        (.NET  :8000)             |
  |   [3]  Start ML         (Python :8001)            |
  |   [4]  Start Frontend   (Vite  :5173)             |
  |   [5]  Start ALL                                  |
  |                                                   |
  |   [6]  Kill API                                   |
  |   [7]  Kill ML                                    |
  |   [8]  Kill Frontend                              |
  |   [9]  Kill ALL                                   |
  |                                                   |
  |  [10]  Build API        (dotnet publish)          |
  |  [11]  Build Frontend   (npm run build)           |
  |  [12]  Build ALL                                  |
  |                                                   |
  |  [13]  Restart ALL      (kill + start)            |
  |                                                   |
  |   [0]  Exit                                       |
  |                                                   |
  +==================================================+
""")


def main():
    while True:
        show_menu()
        choice = input("  Select [0-13]: ").strip()

        if choice == "1":
            view_status()
            input("\n  Press Enter to continue...")

        elif choice == "2":
            start_service("API")
            time.sleep(1)
        elif choice == "3":
            start_service("ML")
            time.sleep(1)
        elif choice == "4":
            start_service("Frontend")
            time.sleep(1)
        elif choice == "5":
            print()
            for svc in ["ML", "API", "Frontend"]:
                start_service(svc)
                time.sleep(1)

        elif choice == "6":
            kill_service("API")
            time.sleep(1)
        elif choice == "7":
            kill_service("ML")
            time.sleep(1)
        elif choice == "8":
            kill_service("Frontend")
            time.sleep(1)
        elif choice == "9":
            print()
            for svc in ["API", "ML", "Frontend"]:
                kill_service(svc)
            time.sleep(1)

        elif choice == "10":
            build_api()
            input("\n  Press Enter to continue...")
        elif choice == "11":
            build_frontend()
            input("\n  Press Enter to continue...")
        elif choice == "12":
            build_frontend()
            build_api()
            input("\n  Press Enter to continue...")

        elif choice == "13":
            print("\n  Stopping all...")
            for svc in ["API", "ML", "Frontend"]:
                kill_service(svc)
            time.sleep(2)
            print("\n  Starting all...")
            for svc in ["ML", "API", "Frontend"]:
                start_service(svc)
                time.sleep(1)
            time.sleep(1)

        elif choice == "0":
            print("\n  Bye!\n")
            break
        else:
            print("  Invalid choice.")
            time.sleep(1)


if __name__ == "__main__":
    main()
