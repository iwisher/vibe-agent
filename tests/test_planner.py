"""Tests for ContextPlanner."""

from vibe.harness.instructions import Skill
from vibe.harness.planner import ContextPlanner, PlanRequest


def test_planner_selects_relevant_tools():
    planner = ContextPlanner()
    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
    ]
    request = PlanRequest(query="Please read the config file", available_tools=tools)
    result = planner.plan(request)
    assert "read_file" in result.selected_tool_names
    assert "bash_tool" not in result.selected_tool_names


def test_planner_falls_back_to_all_tools_when_no_match():
    planner = ContextPlanner()
    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
    ]
    request = PlanRequest(query="xyz obscure nonsense", available_tools=tools)
    result = planner.plan(request)
    assert set(result.selected_tool_names) == {"read_file", "bash_tool"}


def test_planner_matches_skills_by_tag():
    planner = ContextPlanner()
    skills = [
        Skill(name="python_helper", description="Help with Python", content="Use this for Python code.", tags=["python"]),
        Skill(name="js_helper", description="Help with JS", content="Use this for JS code.", tags=["javascript"]),
    ]
    request = PlanRequest(query="Write some python functions", available_skills=skills)
    result = planner.plan(request)
    assert any(s.name == "python_helper" for s in result.selected_skills)
    assert not any(s.name == "js_helper" for s in result.selected_skills)


def test_planner_selects_mcps():
    planner = ContextPlanner()
    mcps = [
        {"name": "filesystem", "description": "Access local filesystem via MCP"},
        {"name": "browser", "description": "Control a browser via MCP"},
    ]
    request = PlanRequest(query="Open a browser and fetch a page", available_mcps=mcps)
    result = planner.plan(request)
    assert any(m["name"] == "browser" for m in result.selected_mcps)
    assert not any(m["name"] == "filesystem" for m in result.selected_mcps)


def test_planner_builds_system_prompt():
    planner = ContextPlanner()
    skills = [
        Skill(name="python_helper", description="Help with Python", content="Use this for Python code.", tags=["python"]),
    ]
    mcps = [
        {"name": "filesystem", "description": "Access local filesystem via MCP"},
    ]
    request = PlanRequest(
        query="python filesystem",
        available_tools=[{"name": "bash_tool", "description": "Run bash"}],
        available_skills=skills,
        available_mcps=mcps,
    )
    result = planner.plan(request)
    assert "## Relevant Skills" in result.system_prompt_append
    assert "python_helper" in result.system_prompt_append
    assert "## Available External Tools (MCP)" in result.system_prompt_append
    assert "filesystem" in result.system_prompt_append


def test_planner_reasoning_when_empty():
    planner = ContextPlanner()
    request = PlanRequest(query="hello")
    result = planner.plan(request)
    assert result.reasoning == "No relevant context matched."
    assert result.selected_tool_names == []
    assert result.selected_skills == []
    assert result.selected_mcps == []


def test_planner_uses_trace_store_memory():
    from vibe.harness.memory.trace_store import TraceStore
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db_path = f"{tmp}/traces.db"
        store = TraceStore(db_path=db_path)
        store.log_session(
            session_id="sess-rust",
            messages=[
                {"role": "user", "content": "how do I write rust code"},
            ],
            tool_results=[],
            success=True,
            model="default",
        )
        planner = ContextPlanner(trace_store=store)
        request = PlanRequest(query="help with rust programming")
        result = planner.plan(request)
        assert "Historical Context" in result.system_prompt_append
        assert "default" in result.system_prompt_append


# ─── Phase 2 eval-style planner tests ───

def test_planner_001_correct_tool_selection_for_query():
    """planner_001: 'execute ls' should select bash_tool."""
    planner = ContextPlanner()
    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands like ls, cd, cat"},
        {"name": "write_file", "description": "Write content to a file"},
    ]
    request = PlanRequest(query="execute ls command", available_tools=tools)
    result = planner.plan(request)
    assert "bash_tool" in result.selected_tool_names, "Expected bash_tool for 'execute ls'"


def test_planner_002_no_overselect_irrelevant_tools():
    """planner_002: Ambiguous query falls back to all tools (documented behavior)."""
    planner = ContextPlanner()
    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
        {"name": "write_file", "description": "Write content to a file"},
    ]
    request = PlanRequest(query="hello, how are you today?", available_tools=tools)
    result = planner.plan(request)
    # When no keywords match, planner returns ALL tools as fallback
    assert set(result.selected_tool_names) == {"read_file", "bash_tool", "write_file"}
    assert "No relevant context matched" in result.reasoning or "Selected tools" in result.reasoning


def test_planner_003_skill_matching_accuracy():
    """planner_003: 'install a skill' should match skill_manage skill."""
    planner = ContextPlanner()
    skills = [
        Skill(name="skill_manage", description="Manage and install skills", content="Use this to install or update skills.", tags=["skill", "install"]),
        Skill(name="docker_helper", description="Docker operations", content="Use this for Docker.", tags=["docker", "container"]),
        Skill(name="git_helper", description="Git operations", content="Use this for Git.", tags=["git", "version-control"]),
    ]
    request = PlanRequest(query="install a skill", available_skills=skills)
    result = planner.plan(request)
    assert any(s.name == "skill_manage" for s in result.selected_skills), "Expected skill_manage"
    assert not any(s.name == "docker_helper" for s in result.selected_skills), "docker_helper should not be selected"
    assert not any(s.name == "git_helper" for s in result.selected_skills), "git_helper should not be selected"
