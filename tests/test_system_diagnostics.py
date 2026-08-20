"""Unit tests for SystemDiagnostics service."""

import pytest
from core.system_diagnostics import SystemDiagnostics


def test_telemetry_fetch():
    """Verify hardware telemetry metrics retrieval."""
    diag = SystemDiagnostics()
    data = diag.get_telemetry()

    assert "cpu" in data
    assert "percent" in data["cpu"]
    assert data["cpu"]["cores_logical"] >= 1

    assert "memory" in data
    assert data["memory"]["total"] > 0
    assert data["memory"]["percent"] >= 0

    assert "network" in data
    assert "uptime_seconds" in data
    assert data["uptime_seconds"] >= 0


def test_process_list_fetch():
    """Verify active processes listing."""
    diag = SystemDiagnostics()
    procs = diag.get_processes(limit=10, sort_by="cpu")

    assert len(procs) > 0
    first = procs[0]
    assert "pid" in first
    assert "name" in first
    assert "cpu_percent" in first
    assert "memory_mb" in first
