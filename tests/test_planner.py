"""Tests for HybridPlanner."""

from vibe.harness.instructions import Skill
from vibe.harness.planner import HybridPlanner, PlanRequest


def test_planner_selects_relevant_tools():
    planner = HybridPlanner()
    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
    ]
    request = PlanRequest(query="Please read the config file", available_tools=tools)
    result = planner.plan(request)
    assert "read_file" in result.selected_tool_names
    assert "bash_tool" not in result.selected_tool_names


def test_planner_falls_back_to_all_tools_when_no_match():
    planner = HybridPlanner()
    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
    ]
    # Use a query with ONLY 1-2 char words (filtered out by _score_text) or non-matching words
    request = PlanRequest(query="ab cd ef gh ij kl", available_tools=tools)
    result = planner.plan(request)
    assert set(result.selected_tool_names) == {"read_file", "bash_tool"}
    assert result.planner_tier == "fallback"


def test_planner_matches_skills_by_tag():
    planner = HybridPlanner()
    skills = [
        Skill(name="python_helper", description="Help with Python", content="Use this for Python code.", tags=["python"]),
        Skill(name="js_helper", description="Help with JS", content="Use this for JS code.", tags=["javascript"]),
    ]
    request = PlanRequest(query="Write some python functions", available_skills=skills)
    result = planner.plan(request)
    assert any(s.name == "python_helper" for s in result.selected_skills)
    assert not any(s.name == "js_helper" for s in result.selected_skills)


def test_planner_selects_mcps():
    planner = HybridPlanner()
    mcps = [
        {"name": "filesystem", "description": "Access local filesystem via MCP"},
        {"name": "browser", "description": "Control a browser via MCP"},
    ]
    request = PlanRequest(query="Open a browser and fetch a page", available_mcps=mcps)
    result = planner.plan(request)
    assert any(m["name"] == "browser" for m in result.selected_mcps)
    assert not any(m["name"] == "filesystem" for m in result.selected_mcps)


def test_planner_builds_system_prompt():
    planner = HybridPlanner()
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
    planner = HybridPlanner()
    request = PlanRequest(query="hello")
    result = planner.plan(request)
    assert result.reasoning == "Safety fallback: returning all tools (no planner match)"
    assert result.selected_tool_names == []
    assert result.selected_skills == []
    assert result.selected_mcps == []
    assert result.planner_tier == "fallback"


def test_planner_uses_trace_store_memory():
    import tempfile

    from vibe.harness.memory.trace_store import TraceStore

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
        planner = HybridPlanner(trace_store=store)
        # Include a skill so keyword plan triggers and includes memory hint
        skills = [
            Skill(name="rust_helper", description="Help with Rust", content="Use this for Rust code.", tags=["rust"]),
        ]
        request = PlanRequest(query="help with rust programming", available_skills=skills)
        result = planner.plan(request)
        assert "Historical Context" in result.system_prompt_append
        assert "default" in result.system_prompt_append


# ─── Phase 2 eval-style planner tests ───

def test_planner_001_correct_tool_selection_for_query():
    """planner_001: 'execute ls' should select bash_tool."""
    planner = HybridPlanner()
    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands like ls, cd, cat"},
        {"name": "write_file", "description": "Write content to a file"},
    ]
    request = PlanRequest(query="execute ls command", available_tools=tools)
    result = planner.plan(request)
    assert "bash_tool" in result.selected_tool_names, "Expected bash_tool for 'execute ls'"
    assert result.planner_tier == "keyword"


def test_planner_002_no_overselect_irrelevant_tools():
    """planner_002: Query with no keyword match falls back to all tools."""
    planner = HybridPlanner()
    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
        {"name": "write_file", "description": "Write content to a file"},
    ]
    # Query with ONLY 1-2 char words (filtered out by _score_text)
    request = PlanRequest(query="ab cd ef gh ij kl", available_tools=tools)
    result = planner.plan(request)
    # When no keywords match, planner returns ALL tools as fallback
    assert set(result.selected_tool_names) == {"read_file", "bash_tool", "write_file"}
    assert result.planner_tier == "fallback"


def test_planner_003_skill_matching_accuracy():
    """planner_003: 'install a skill' should match skill_manage skill."""
    planner = HybridPlanner()
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


# ─── Tier transition tests for HybridPlanner ───

def _mock_get_embedding(text: str) -> list[float]:
    """Return deterministic 384-dim vectors for testing."""
    h = hash(text) % 1000
    return [float(h + i) for i in range(384)]


def test_planner_tier_keyword_to_embedding_transition():
    """When keyword returns empty, embedding tier should activate with high confidence."""
    from vibe.harness.planner import HybridPlanner

    planner = HybridPlanner()
    # Mock embedding to return deterministic 384-dim vectors
    planner._get_embedding = _mock_get_embedding
    # Override cosine similarity to always return 0.85 (above HIGH_CONFIDENCE=0.8)
    planner._cosine_similarity = lambda a, b: 0.85

    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
    ]
    # Query with ONLY 1-2 char words (no keyword match)
    request = PlanRequest(query="ab cd ef gh", available_tools=tools)
    result = planner.plan(request)
    assert result.planner_tier == "embedding_high_confidence"
    assert len(result.selected_tool_names) > 0


def test_planner_tier_embedding_to_llm_transition():
    """When embedding similarity is below threshold, should fall through to LLM tier."""
    from vibe.harness.planner import HybridPlanner

    planner = HybridPlanner()
    planner._get_embedding = _mock_get_embedding
    # Low similarity - below EMBEDDING_MIN_SIMILARITY (0.75)
    planner._cosine_similarity = lambda a, b: 0.1

    # Mock LLM client to return a tool selection
    class MockLLM:
        def complete(self, prompt):
            return '{"selected_tools": ["read_file"]}'

    planner.llm_client = MockLLM()

    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
    ]
    # Query with ONLY 1-2 char words (no keyword match)
    request = PlanRequest(query="ab cd ef gh", available_tools=tools)
    result = planner.plan(request)
    assert result.planner_tier == "llm"
    assert "read_file" in result.selected_tool_names


def test_planner_tier_embedding_medium_confidence():
    """When embedding similarity is medium (0.75-0.8), use embedding result."""
    from vibe.harness.planner import HybridPlanner

    planner = HybridPlanner()
    planner._get_embedding = _mock_get_embedding
    # Medium similarity (above MIN 0.75, below HIGH 0.8)
    planner._cosine_similarity = lambda a, b: 0.77

    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
    ]
    # Query with ONLY 1-2 char words (no keyword match)
    request = PlanRequest(query="ab cd ef gh", available_tools=tools)
    result = planner.plan(request)
    assert result.planner_tier == "embedding"
    assert len(result.selected_tool_names) > 0


def test_planner_tier_fallback_when_all_fail():
    """When keyword, embedding, and LLM all fail, should use fallback tier."""
    from vibe.harness.planner import HybridPlanner

    planner = HybridPlanner()
    # No embedding model
    planner._embedding_model = None
    # No LLM client
    planner.llm_client = None

    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
    ]
    # Query with ONLY 1-2 char words (no keyword match)
    request = PlanRequest(query="ab cd ef gh ij kl", available_tools=tools)
    result = planner.plan(request)
    assert result.planner_tier == "fallback"
    assert set(result.selected_tool_names) == {"read_file", "bash_tool"}
    assert "Safety fallback" in result.reasoning


def test_planner_tier_keyword_fast_path():
    """When keywords match strongly, should use keyword tier directly."""
    from vibe.harness.planner import HybridPlanner

    planner = HybridPlanner()
    tools = [
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "bash_tool", "description": "Execute bash commands"},
    ]
    request = PlanRequest(query="read the config file", available_tools=tools)
    result = planner.plan(request)
    assert result.planner_tier == "keyword"
    assert "read_file" in result.selected_tool_names
    assert "bash_tool" not in result.selected_tool_names
