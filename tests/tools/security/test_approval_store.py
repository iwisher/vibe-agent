import pytest
import os
from pathlib import Path
from vibe.tools.security.approval_store import ApprovalStore

def test_is_safe_command():
    store = ApprovalStore(store_path=None)
    assert store.is_safe_command("ls -la")
    assert store.is_safe_command("find . -name '*.py'")
    assert store.is_safe_command("git status")
    assert store.is_safe_command("git diff head")
    assert store.is_safe_command("python -m json.tool")
    
    # Unsafe commands
    assert not store.is_safe_command("rm -rf /")
    assert not store.is_safe_command("git push")
    assert not store.is_safe_command("chmod +x script.sh")
    assert not store.is_safe_command("python main.py")

def test_add_scoped_approval(tmp_path):
    store_file = tmp_path / "approvals.json"
    store = ApprovalStore(store_path=store_file)
    
    work_dir = str(tmp_path / "work")
    os.makedirs(work_dir, exist_ok=True)
    
    store.add_scoped_approval("ls", work_dir)
    
    assert store.check_approval("ls -la", work_dir)
    assert store.check_approval("ls sub/dir", str(Path(work_dir) / "sub" / "dir"))
    assert not store.check_approval("ls", str(tmp_path))

def test_add_exact_approval(tmp_path):
    store_file = tmp_path / "approvals.json"
    store = ApprovalStore(store_path=store_file)
    
    cmd = "rm temp.txt"
    store.add_exact_approval(cmd)
    
    assert store.check_approval(cmd, str(tmp_path))
    assert not store.check_approval("rm other.txt", str(tmp_path))

def test_persistence(tmp_path):
    store_file = tmp_path / "approvals.json"
    store1 = ApprovalStore(store_path=store_file)
    store1.add_exact_approval("test cmd")
    
    store2 = ApprovalStore(store_path=store_file)
    assert store2.check_approval("test cmd", str(tmp_path))
