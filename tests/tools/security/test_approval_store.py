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

def test_split_command_chain():
    store = ApprovalStore(store_path=None)
    
    # Simple pipe
    units = store._split_command_chain("ls -la | grep py")
    assert len(units) == 2
    assert units[0] == {"type": "cmd", "content": "ls -la"}
    assert units[1] == {"type": "cmd", "content": "grep py"}
    
    # Redirect
    units = store._split_command_chain("cat file.txt > output.txt")
    assert len(units) == 2
    assert units[0] == {"type": "cmd", "content": "cat file.txt"}
    assert units[1] == {"type": "redirect", "content": "output.txt"}

    # Complex chain
    units = store._split_command_chain("find . -name '*.py' | xargs grep TODO > results.txt 2> error.log")
    assert len(units) == 4
    assert units[0]["content"] == "find . -name *.py"
    assert units[1]["content"] == "xargs grep TODO"
    assert units[2] == {"type": "redirect", "content": "results.txt"}
    assert units[3] == {"type": "redirect", "content": "error.log"}

def test_chain_verification(tmp_path):
    store = ApprovalStore(store_path=tmp_path / "approvals.json")
    work_dir = str(tmp_path / "work")
    os.makedirs(work_dir, exist_ok=True)
    
    # Pre-approve ls and grep for the work_dir
    store.add_scoped_approval("ls", work_dir)
    store.add_scoped_approval("grep", work_dir)
    store.add_scoped_approval("cat", work_dir)
    
    # 1. Piped safe commands -> should pass
    assert store.check_approval("ls -la | grep main", work_dir)
    
    # 2. Redirect to file in hierarchy -> should pass
    assert store.check_approval("cat file.txt > " + str(Path(work_dir) / "out.txt"), work_dir)
    
    # 3. Piped with UNSAFE command -> should fail
    assert not store.check_approval("ls | rm", work_dir)
    
    # 4. Redirect to file OUTSIDE hierarchy -> should fail
    assert not store.check_approval("cat file.txt > /tmp/hacked.txt", work_dir)
