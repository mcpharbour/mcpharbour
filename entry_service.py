"""PyInstaller entry point for the Windows service binary (harbour-service.exe).

Subcommands:
  harbour-service install   — register as a Windows service (run elevated)
  harbour-service remove    — unregister the service
  (no subcommand / SCM)     — run as a service (called by Windows SCM)
"""
import sys
import os
import traceback

for i, arg in enumerate(sys.argv):
    if arg == "--config-dir" and i + 1 < len(sys.argv):
        os.environ["MCP_HARBOUR_CONFIG_DIR"] = sys.argv[i + 1]
        break

_log_dir = os.environ.get(
    "MCP_HARBOUR_CONFIG_DIR",
    os.path.join(os.environ.get("APPDATA", ""), "mcp-harbour")
)
os.makedirs(_log_dir, exist_ok=True)
_crash_log = os.path.join(_log_dir, "service-crash.log")

try:
    from mcp_harbour.service import install_service, remove_service, run_service_dispatcher

    if len(sys.argv) > 1 and sys.argv[1] == "install":
        install_service()
    elif len(sys.argv) > 1 and sys.argv[1] == "remove":
        remove_service()
    else:
        run_service_dispatcher()
except Exception:
    with open(_crash_log, "a") as f:
        f.write(f"argv={sys.argv}\n{traceback.format_exc()}\n")
    sys.exit(1)
