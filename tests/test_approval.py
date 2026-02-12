"""Tests for AutoApproveEngine."""

import time

from agent_tether.approval import AutoApproveEngine


def test_check_no_timers():
    engine = AutoApproveEngine()
    assert engine.check("t1", "Bash") is None


def test_set_allow_all():
    engine = AutoApproveEngine(duration_s=60)
    engine.set_allow_all("t1")
    assert engine.check("t1", "Bash") == "Allow All"
    assert engine.check("t1", "Read") == "Allow All"
    assert engine.check("t2", "Bash") is None  # different thread


def test_set_allow_tool():
    engine = AutoApproveEngine(duration_s=60)
    engine.set_allow_tool("t1", "Bash")
    assert engine.check("t1", "Bash") == "Allow Bash"
    assert engine.check("t1", "Read") is None  # different tool


def test_allow_all_overrides_tool():
    engine = AutoApproveEngine(duration_s=60)
    engine.set_allow_tool("t1", "Bash")
    engine.set_allow_all("t1")
    # Allow All takes precedence
    assert engine.check("t1", "Bash") == "Allow All"


def test_never_auto_approve():
    engine = AutoApproveEngine(duration_s=60)
    engine.set_allow_all("t1")
    assert engine.check("t1", "task") is None  # never auto-approve
    assert engine.check("t1", "Task") is None  # case-insensitive prefix match


def test_custom_never_auto_approve():
    engine = AutoApproveEngine(duration_s=60, never_auto_approve={"dangerous"})
    engine.set_allow_all("t1")
    assert engine.check("t1", "dangerous_tool") is None
    assert engine.check("t1", "safe_tool") == "Allow All"


def test_directory_scoped():
    engine = AutoApproveEngine(duration_s=60)
    engine.associate_directory("t1", "/home/user/repo")
    engine.associate_directory("t2", "/home/user/repo")
    engine.associate_directory("t3", "/home/user/other")

    engine.set_allow_directory("/home/user/repo")

    assert engine.check("t1", "Bash") == "Allow dir repo"
    assert engine.check("t2", "Bash") == "Allow dir repo"
    assert engine.check("t3", "Bash") is None  # different directory


def test_directory_scoped_subdirectory():
    engine = AutoApproveEngine(duration_s=60)
    engine.associate_directory("t1", "/home/user/repo/subdir")
    engine.set_allow_directory("/home/user/repo")
    # Thread in a subdirectory should match
    assert "Allow dir repo" in engine.check("t1", "Bash")


def test_expiry():
    engine = AutoApproveEngine(duration_s=1)
    engine.set_allow_all("t1")
    assert engine.check("t1", "Bash") == "Allow All"
    time.sleep(1.1)
    assert engine.check("t1", "Bash") is None


def test_remove_thread():
    engine = AutoApproveEngine(duration_s=60)
    engine.set_allow_all("t1")
    engine.set_allow_tool("t1", "Bash")
    engine.associate_directory("t1", "/tmp")
    assert engine.check("t1", "Read") == "Allow All"
    engine.remove_thread("t1")
    assert engine.check("t1", "Read") is None
