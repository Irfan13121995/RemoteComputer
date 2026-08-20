"""System diagnostics, hardware telemetry, active process monitor, and power controls."""

import logging
import os
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional

import psutil

logger = logging.getLogger(__name__)


class SystemDiagnostics:
    """Provides system hardware metrics, process manager, and power management."""

    def __init__(self):
        self.boot_time = psutil.boot_time()
        self.is_windows = platform.system() == "Windows"
        self.is_mac = platform.system() == "Darwin"
        self._last_net_io = psutil.net_io_counters()
        self._last_net_time = time.time()

    def get_telemetry(self) -> Dict[str, Any]:
        """Fetch real-time CPU, RAM, Disk, Network, and Uptime metrics."""
        # CPU
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_count_logical = psutil.cpu_count(logical=True)
        cpu_count_physical = psutil.cpu_count(logical=False) or cpu_count_logical

        # Memory
        mem = psutil.virtual_memory()

        # Swap / Page file
        swap = psutil.swap_memory()

        # Network rates
        now = time.time()
        net_io = psutil.net_io_counters()
        dt = max(0.001, now - self._last_net_time)

        bytes_sent_per_sec = (net_io.bytes_sent - self._last_net_io.bytes_sent) / dt
        bytes_recv_per_sec = (net_io.bytes_recv - self._last_net_io.bytes_recv) / dt

        self._last_net_io = net_io
        self._last_net_time = now

        # Battery info
        battery_info = None
        try:
            battery = psutil.sensors_battery()
            if battery:
                battery_info = {
                    "percent": battery.percent,
                    "power_plugged": battery.power_plugged,
                    "secsleft": battery.secsleft,
                }
        except Exception:
            pass

        # Uptime
        uptime_seconds = int(time.time() - self.boot_time)

        return {
            "cpu": {
                "percent": cpu_percent,
                "cores_logical": cpu_count_logical,
                "cores_physical": cpu_count_physical,
            },
            "memory": {
                "total": mem.total,
                "used": mem.used,
                "available": mem.available,
                "percent": mem.percent,
            },
            "swap": {
                "total": swap.total,
                "used": swap.used,
                "percent": swap.percent,
            },
            "network": {
                "bytes_sent_sec": int(bytes_sent_per_sec),
                "bytes_recv_sec": int(bytes_recv_per_sec),
                "total_sent": net_io.bytes_sent,
                "total_recv": net_io.bytes_recv,
            },
            "battery": battery_info,
            "uptime_seconds": uptime_seconds,
            "os_name": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "hostname": platform.node(),
        }

    def get_processes(self, limit: int = 60, sort_by: str = "cpu") -> List[Dict[str, Any]]:
        """Retrieve list of active processes sorted by CPU or Memory usage."""
        processes = []
        for proc in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info", "status"]):
            try:
                info = proc.info
                mem_mb = round((info["memory_info"].rss / (1024 * 1024)), 1) if info.get("memory_info") else 0
                processes.append(
                    {
                        "pid": info["pid"],
                        "name": info["name"] or "Unknown",
                        "user": info.get("username") or "SYSTEM",
                        "cpu_percent": info.get("cpu_percent") or 0.0,
                        "memory_mb": mem_mb,
                        "status": info.get("status") or "running",
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # Sort
        if sort_by == "memory":
            processes.sort(key=lambda x: x["memory_mb"], reverse=True)
        else:
            processes.sort(key=lambda x: (x["cpu_percent"], x["memory_mb"]), reverse=True)

        return processes[:limit]

    def kill_process(self, pid: int) -> bool:
        """Terminate a running process by PID."""
        try:
            p = psutil.Process(pid)
            p.terminate()
            return True
        except Exception as e:
            logger.error(f"Failed to terminate process {pid}: {e}")
            return False

    def execute_power_action(self, action: str) -> bool:
        """Execute system power command (lock, sleep, restart, shutdown)."""
        action = action.lower().strip()
        try:
            if action == "lock":
                if self.is_windows:
                    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
                elif self.is_mac:
                    subprocess.Popen(["pmset", "displaysleepnow"])
                else:
                    subprocess.Popen(["xdg-screensaver", "lock"])
                return True

            elif action == "restart":
                if self.is_windows:
                    subprocess.Popen(["shutdown", "/r", "/t", "5"])
                else:
                    subprocess.Popen(["sudo", "reboot"])
                return True

            elif action == "shutdown":
                if self.is_windows:
                    subprocess.Popen(["shutdown", "/s", "/t", "5"])
                else:
                    subprocess.Popen(["sudo", "shutdown", "-h", "now"])
                return True

            elif action == "sleep":
                if self.is_windows:
                    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
                elif self.is_mac:
                    subprocess.Popen(["pmset", "sleepnow"])
                return True

        except Exception as e:
            logger.error(f"Power action {action} failed: {e}")
            return False

        return False
