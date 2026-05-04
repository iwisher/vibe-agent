import pytest
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch
from vibe.tools.security.human_approval import HumanApprover, ApprovalMode, ApprovalChoice
from vibe.tools.security.approval_store import ApprovalStore

@pytest.fixture
def clean_vibe_dir(tmp_path):
    vibe_dir = tmp_path / ".vibe"
    vibe_dir.mkdir()
    store_path = vibe_dir / "approvals.json"
    return store_path

def test_smart_approval_flow(clean_vibe_dir, tmp_path):
    # Setup
    with patch("vibe.tools.security.approval_store.DEFAULT_STORE_PATH", clean_vibe_dir), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("sys.stdin.fileno", return_value=0), \
         patch("vibe.tools.security.human_approval.termios"), \
         patch("vibe.tools.security.human_approval.tty"), \
         patch("select.select", return_value=([0], [], [])), \
         patch("sys.stdin.read", side_effect=["a", "\n"]):
        
        approver = HumanApprover(mode=ApprovalMode.INTERACTIVE)
        approver.timeout_seconds = 5 # Short timeout for test
        
        work_dir = str(tmp_path / "project")
        os.makedirs(work_dir, exist_ok=True)
        
        # 1. First request - simulation user choosing 'always' (a)
        res1 = approver.request_approval("ls -la", cwd=work_dir)
        assert res1.approved
        assert res1.choice == ApprovalChoice.ALWAYS
        
        # Verify it was added to store (scoped because 'ls' is safe)
        assert approver.store.check_approval("ls", work_dir)
            
        # 2. Second request - same base cmd, different flags, same dir -> auto-approved
        res2 = approver.request_approval("ls -R", cwd=work_dir)
        assert res2.approved
        assert res2.choice == ApprovalChoice.ALWAYS
        assert "permanently" in res2.reason
        
        # 3. Third request - same cmd, different dir -> prompted again
        other_dir = str(tmp_path / "other")
        os.makedirs(other_dir, exist_ok=True)
        # Mock sys.stdin again for third request to 'deny' (d)
        with patch("sys.stdin.read", side_effect=["d", "\n"]):
            res3 = approver.request_approval("ls", cwd=other_dir)
            assert not res3.approved
            assert res3.choice == ApprovalChoice.DENY

def test_unsafe_command_always(clean_vibe_dir, tmp_path):
    with patch("vibe.tools.security.approval_store.DEFAULT_STORE_PATH", clean_vibe_dir), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("sys.stdin.fileno", return_value=0), \
         patch("vibe.tools.security.human_approval.termios"), \
         patch("vibe.tools.security.human_approval.tty"), \
         patch("select.select", return_value=([0], [], [])), \
         patch("sys.stdin.read", side_effect=["a", "\n"]):
        
        approver = HumanApprover(mode=ApprovalMode.INTERACTIVE)
        approver.timeout_seconds = 5
        
        work_dir = str(tmp_path / "project")
        os.makedirs(work_dir, exist_ok=True)
        
        # Approve unsafe command 'rm' with 'always'
        approver.request_approval("rm temp.txt", cwd=work_dir)
            
        # Should be exact match ONLY
        assert approver.store.check_approval("rm temp.txt", work_dir)
        assert not approver.store.check_approval("rm other.txt", work_dir)

def test_session_approval_exact_match(tmp_path):
    approver = HumanApprover(mode=ApprovalMode.INTERACTIVE)
    approver.timeout_seconds = 5
    
    # 1. First request - simulation user choosing 'session' (s)
    with patch("sys.stdin.isatty", return_value=True), \
         patch("sys.stdin.fileno", return_value=0), \
         patch("vibe.tools.security.human_approval.termios"), \
         patch("vibe.tools.security.human_approval.tty"), \
         patch("select.select", return_value=([0], [], [])), \
         patch("sys.stdin.read", side_effect=["s", "\n"]):
        
        res1 = approver.request_approval("ls -la")
        assert res1.approved
        assert res1.choice == ApprovalChoice.SESSION
        
    # 2. Second request - same exact command -> auto-approved in session
    res2 = approver.request_approval("ls -la")
    assert res2.approved
    assert res2.choice == ApprovalChoice.SESSION
    
    # 3. Third request - different flags -> NOT approved
    with patch("sys.stdin.read", side_effect=["d", "\n"]):
        res3 = approver.request_approval("ls -F")
        assert not res3.approved

    # 4. Reset session -> Second request should now prompt again
    approver.reset_session()
    with patch("sys.stdin.read", side_effect=["d", "\n"]):
        res4 = approver.request_approval("ls -la")
        assert not res4.approved

def test_shell_pipe_auto_approval(clean_vibe_dir, tmp_path):
    with patch("vibe.tools.security.approval_store.DEFAULT_STORE_PATH", clean_vibe_dir), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("sys.stdin.fileno", return_value=0), \
         patch("vibe.tools.security.human_approval.termios"), \
         patch("vibe.tools.security.human_approval.tty"), \
         patch("select.select", return_value=([0], [], [])), \
         patch("sys.stdin.read", side_effect=["a", "\n"]):
        
        approver = HumanApprover(mode=ApprovalMode.INTERACTIVE)
        work_dir = str(tmp_path / "project")
        os.makedirs(work_dir, exist_ok=True)
        
        # Pre-approve ls and grep individually
        approver.store.add_scoped_approval("ls", work_dir)
        approver.store.add_scoped_approval("grep", work_dir)
        
        # This piped command should be auto-approved because both units are safe/approved
        res = approver.request_approval("ls -la | grep vibe", cwd=work_dir)
        assert res.approved
        assert res.choice == ApprovalChoice.ALWAYS
        assert "permanently" in res.reason

def test_shell_redirect_safety(clean_vibe_dir, tmp_path):
    with patch("vibe.tools.security.approval_store.DEFAULT_STORE_PATH", clean_vibe_dir):
        approver = HumanApprover(mode=ApprovalMode.INTERACTIVE)
        work_dir = str(tmp_path / "project")
        os.makedirs(work_dir, exist_ok=True)
        
        # Approve cat
        approver.store.add_scoped_approval("cat", work_dir)
        
        # Redirect inside hierarchy -> OK
        safe_path = str(Path(work_dir) / "out.txt")
        assert approver.store.check_approval(f"cat file.txt > {safe_path}", work_dir)
        
        # Redirect OUTSIDE hierarchy -> FAIL
        unsafe_path = "/tmp/hacked.txt"
        assert not approver.store.check_approval(f"cat file.txt > {unsafe_path}", work_dir)
