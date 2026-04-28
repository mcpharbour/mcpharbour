"""
Windows Service support for the Harbour Daemon.

Uses ctypes for the service control handler. No pywin32 dependency required.

Used by harbour-service.exe (entry_service.py):
  harbour-service install   — register as a Windows service
  harbour-service remove    — unregister the service
  (SCM launch)              — run the service dispatcher
"""

import sys
import os


SERVICE_NAME = "MCPHarbour"
SERVICE_DISPLAY = "MCP Harbour Daemon"
SERVICE_DESC = "Port authority for MCP servers."


def install_service():
    """Register the service with Windows SCM using sc.exe."""
    import subprocess

    service_bin = sys.executable

    from .config import CONFIG_DIR
    config_dir = str(CONFIG_DIR)

    cmd = f'sc.exe create {SERVICE_NAME} binPath= "\"{service_bin}\" --config-dir \"{config_dir}\"" DisplayName= "{SERVICE_DISPLAY}" start= auto'

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Failed to create service: {result.stderr.strip()}")
        sys.exit(1)

    subprocess.run(
        ["sc.exe", "description", SERVICE_NAME, SERVICE_DESC],
        capture_output=True
    )

    print(f"Service '{SERVICE_NAME}' installed.")


def remove_service():
    """Unregister the service using sc.exe."""
    import subprocess

    subprocess.run(
        ["sc.exe", "stop", SERVICE_NAME],
        capture_output=True
    )
    result = subprocess.run(
        ["sc.exe", "delete", SERVICE_NAME],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Failed to remove service: {result.stderr.strip()}")
        sys.exit(1)

    print(f"Service '{SERVICE_NAME}' removed.")


def run_service_dispatcher():
    """Run as a Windows service. Called by SCM via harbour-service.exe."""
    if sys.platform != "win32":
        print("Windows services are only supported on Windows.")
        sys.exit(1)

    import ctypes
    import ctypes.wintypes
    import threading
    import asyncio
    import logging

    config_dir = os.environ.get("MCP_HARBOUR_CONFIG_DIR", "")
    if config_dir:
        log_file = os.path.join(config_dir, "service.log")
    else:
        log_file = os.path.join(os.environ.get("APPDATA", ""), "mcp-harbour", "service.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logging.basicConfig(filename=log_file, level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("run_service_dispatcher called")

    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32

    HANDLER_FUNC = ctypes.WINFUNCTYPE(ctypes.wintypes.DWORD, ctypes.wintypes.DWORD)

    advapi32.RegisterServiceCtrlHandlerW.argtypes = [ctypes.c_wchar_p, HANDLER_FUNC]
    advapi32.RegisterServiceCtrlHandlerW.restype = ctypes.c_void_p

    advapi32.SetServiceStatus.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    advapi32.SetServiceStatus.restype = ctypes.wintypes.BOOL

    advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.c_void_p]
    advapi32.StartServiceCtrlDispatcherW.restype = ctypes.wintypes.BOOL

    SERVICE_WIN32_OWN_PROCESS = 0x10
    SERVICE_RUNNING = 0x04
    SERVICE_STOPPED = 0x01
    SERVICE_STOP_PENDING = 0x03
    SERVICE_START_PENDING = 0x02
    SERVICE_CONTROL_STOP = 0x01
    SERVICE_ACCEPT_STOP = 0x01

    class SERVICE_STATUS(ctypes.Structure):
        _fields_ = [
            ("dwServiceType", ctypes.wintypes.DWORD),
            ("dwCurrentState", ctypes.wintypes.DWORD),
            ("dwControlsAccepted", ctypes.wintypes.DWORD),
            ("dwWin32ExitCode", ctypes.wintypes.DWORD),
            ("dwServiceSpecificExitCode", ctypes.wintypes.DWORD),
            ("dwCheckPoint", ctypes.wintypes.DWORD),
            ("dwWaitHint", ctypes.wintypes.DWORD),
        ]

    status_handle = None
    stop_event = threading.Event()
    svc_status = SERVICE_STATUS()

    def set_service_status(state, accepted=0, wait_hint=0):
        svc_status.dwServiceType = SERVICE_WIN32_OWN_PROCESS
        svc_status.dwCurrentState = state
        svc_status.dwControlsAccepted = accepted
        svc_status.dwWaitHint = wait_hint
        advapi32.SetServiceStatus(status_handle, ctypes.byref(svc_status))

    @HANDLER_FUNC
    def service_ctrl_handler(control):
        if control == SERVICE_CONTROL_STOP:
            set_service_status(SERVICE_STOP_PENDING)
            stop_event.set()
        return 0

    SERVICE_MAIN_FUNC = ctypes.WINFUNCTYPE(None, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.LPWSTR))

    @SERVICE_MAIN_FUNC
    def service_main(argc, argv):
        nonlocal status_handle

        status_handle = advapi32.RegisterServiceCtrlHandlerW(
            SERVICE_NAME, service_ctrl_handler
        )
        if not status_handle:
            logging.error("RegisterServiceCtrlHandler failed")
            return

        set_service_status(SERVICE_START_PENDING, wait_hint=30000)
        logging.info("Service status set to START_PENDING")

        try:
            from .gateway import HarbourGateway
            from .config import DEFAULT_HOST, DEFAULT_PORT

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            gateway = HarbourGateway()

            def run_gateway():
                try:
                    loop.run_until_complete(gateway.serve(DEFAULT_HOST, DEFAULT_PORT))
                except Exception:
                    logging.exception("Gateway error")

            daemon_thread = threading.Thread(target=run_gateway, daemon=True)
            daemon_thread.start()

            set_service_status(SERVICE_RUNNING, SERVICE_ACCEPT_STOP)
            logging.info("Service status set to RUNNING")

            stop_event.wait()

            loop.call_soon_threadsafe(loop.stop)
            daemon_thread.join(timeout=10)
            loop.close()
        except Exception:
            logging.exception("service_main error")

        set_service_status(SERVICE_STOPPED)

    service_name_ptr = ctypes.c_wchar_p(SERVICE_NAME)
    _service_main_ref = service_main

    class SERVICE_TABLE_ENTRY(ctypes.Structure):
        _fields_ = [
            ("lpServiceName", ctypes.c_wchar_p),
            ("lpServiceProc", SERVICE_MAIN_FUNC),
        ]

    null_func = SERVICE_MAIN_FUNC(0)
    table = (SERVICE_TABLE_ENTRY * 2)(
        SERVICE_TABLE_ENTRY(service_name_ptr, service_main),
        SERVICE_TABLE_ENTRY(None, null_func),
    )

    logging.info("Calling StartServiceCtrlDispatcherW")
    result = advapi32.StartServiceCtrlDispatcherW(ctypes.byref(table))
    logging.info(f"StartServiceCtrlDispatcherW returned: {result}")
    if not result:
        error = kernel32.GetLastError()
        logging.error(f"StartServiceCtrlDispatcherW failed with error: {error}")
        if error == 1063:
            print("This command is called by Windows SCM. Use 'harbour serve' to run interactively.")
            sys.exit(1)
        sys.exit(1)
