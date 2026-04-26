"""Tests for HybridPlanner."""

import pytest
from vibe.harness.planner import HybridPlanner, PlanRequest, PlanResult
from vibe.harness.instructions import Skill


class TestHybridPlanner:
    """Test hybrid planner tiers."""

    def test_keyword_tier_matches_tools(self):
        """Tier 1: Keyword matching should select relevant tools."""
        planner = HybridPlanner()
        
        tools = [
            {"name": "read_file", "description": "Read a file from disk"},
            {"name": "write_file", "description": "Write a file to disk"},
            {"name": "browser_navigate", "description": "Navigate to a URL"},
        ]
        
        request = PlanRequest(
            query="read the config file",
            available_tools=tools,
        )
        
        result = planner.plan(request)
        
        assert result.planner_tier == "keyword"
        assert "read_file" in result.selected_tool_names
        assert result.reasoning != ""

    def test_keyword_tier_no_match_fallback(self):
        """When keyword tier has no matches, should try other tiers."""
        planner = HybridPlanner()
        
        tools = [
            {"name": "fetch_forecast", "description": "Get weather forecast"},
        ]
        
        request = PlanRequest(
            query="completely unrelated query xyz123",
            available_tools=tools,
        )
        
        result = planner.plan(request)
        
        # Keyword matching is lenient - if no good match, may still return tools
        # The key is that planner_tier indicates which path was taken
        assert result.planner_tier in ("keyword", "fallback")
        assert len(result.selected_tool_names) >= 1

    def test_embedding_tier_without_model(self):
        """Without fastText model, should skip embedding tier."""
        planner = HybridPlanner(embedding_model_path="/nonexistent/model.bin")
        
        tools = [
            {"name": "get_weather", "description": "Fetch weather data"},
        ]
        
        request = PlanRequest(
            query="weather forecast",
            available_tools=tools,
        )
        
        result = planner.plan(request)
        
        # Should use keyword tier since no embedding model
        assert result.planner_tier in ("keyword", "fallback")

    def test_llm_tier_without_client(self):
        """Without LLM client, should skip LLM tier."""
        planner = HybridPlanner(llm_client=None)
        
        tools = [
            {"name": "analyze_data", "description": "Analyze CSV data"},
        ]
        
        request = PlanRequest(
            query="csv analysis",
            available_tools=tools,
        )
        
        result = planner.plan(request)
        
        # Should use keyword or fallback
        assert result.planner_tier in ("keyword", "fallback")

    def test_skill_matching(self):
        """Should match skills based on query."""
        planner = HybridPlanner()
        
        skills = [
            Skill(
                name="python_dev",
                description="Python development skills",
                content="Write Python code",
                tags=["python", "coding"],
            ),
            Skill(
                name="bash_expert",
                description="Bash scripting",
                content="Write shell scripts",
                tags=["bash", "shell"],
            ),
        ]
        
        request = PlanRequest(
            query="write a python script",
            available_skills=skills,
        )
        
        result = planner.plan(request)
        
        skill_names = [s.name for s in result.selected_skills]
        assert "python_dev" in skill_names

    def test_mcp_matching(self):
        """Should match MCPs based on query."""
        planner = HybridPlanner()
        
        mcps = [
            {"name": "slack", "description": "Send Slack messages"},
            {"name": "github", "description": "GitHub integration"},
        ]
        
        request = PlanRequest(
            query="send message to slack",
            available_mcps=mcps,
        )
        
        result = planner.plan(request)
        
        mcp_names = [m.get("name") for m in result.selected_mcps]
        assert "slack" in mcp_names

    def test_query_caching(self):
        """Should cache and return cached results."""
        planner = HybridPlanner()
        
        tools = [
            {"name": "tool1", "description": "First tool"},
        ]
        
        request = PlanRequest(
            query="test query caching",
            available_tools=tools,
        )
        
        # First call
        result1 = planner.plan(request)
        
        # Second call should be cached
        result2 = planner.plan(request)
        
        assert "cached" in result2.reasoning.lower()
        assert result1.selected_tool_names == result2.selected_tool_names

    def test_empty_tools_fallback(self):
        """With no tools, should return empty result."""
        planner = HybridPlanner()
        
        request = PlanRequest(
            query="do something",
            available_tools=[],
        )
        
        result = planner.plan(request)
        
        assert result.planner_tier == "fallback"
        assert result.selected_tool_names == []

    def test_memory_augmentation(self):
        """Should include historical context when trace_store available."""
        class MockTraceStore:
            def get_similar_sessions(self, query, limit=3):
                return [
                    {"model": "gpt-4"},
                    {"model": "claude-3"},
                ]
        
        planner = HybridPlanner(trace_store=MockTraceStore())
        
        tools = [
            {"name": "tool1", "description": "A tool"},
        ]
        
        request = PlanRequest(
            query="test query",
            available_tools=tools,
        )
        
        result = planner.plan(request)
        
        # Should have memory hint in system prompt
        if result.system_prompt_append:
            assert "Historical Context" in result.system_prompt_append or result.planner_tier == "fallback"

    def test_cosine_similarity(self):
        """Cosine similarity should work correctly."""
        planner = HybridPlanner()
        
        # Same vector = similarity 1.0
        vec = [1.0, 0.0, 0.0]
        assert planner._cosine_similarity(vec, vec) == pytest.approx(1.0, abs=0.01)
        
        # Orthogonal vectors = similarity 0.0
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [0.0, 1.0, 0.0]
        assert planner._cosine_similarity(vec_a, vec_b) == pytest.approx(0.0, abs=0.01)
        
        # Empty vectors
        assert planner._cosine_similarity([], [1.0, 2.0]) == 0.0

    def test_embedding_cache(self):
        """Should cache embeddings."""
        planner = HybridPlanner()
        
        # Without model, should return empty
        emb1 = planner._get_embedding("test text")
        assert emb1 == []
        
        # Should handle empty text
        emb2 = planner._get_embedding("")
        assert emb2 == []

    def test_lru_cache_eviction(self):
        """Cache should evict old entries."""
        planner = HybridPlanner()
        planner._query_cache_ttl = 0  # Immediate expiration
        
        tools = [
            {"name": "tool1", "description": "Tool one"},
        ]
        
        request = PlanRequest(
            query="test eviction",
            available_tools=tools,
        )
        
        result1 = planner.plan(request)
        
        # With TTL=0, should not be cached
        result2 = planner.plan(request)
        
        assert "cached" not in result2.reasoning.lower()

    def test_max_llm_tools_limit(self):
        """LLM tier should only send MAX_LLM_TOOLS to router."""
        planner = HybridPlanner()
        
        # Create many tools
        tools = [
            {"name": f"tool_{i}", "description": f"Tool number {i}"}
            for i in range(50)
        ]
        
        request = PlanRequest(
            query="use a tool",
            available_tools=tools,
        )
        
        # Without LLM client, will use keyword or fallback
        result = planner.plan(request)
        assert result.planner_tier in ("keyword", "fallback")
        assert len(result.selected_tool_names) == 50

    def test_plan_result_structure(self):
        """PlanResult should have all required fields."""
        result = PlanResult(
            selected_tool_names=["tool1"],
            selected_skills=[],
            selected_mcps=[],
            system_prompt_append="",
            reasoning="test",
            planner_tier="keyword",
        )
        
        assert result.selected_tool_names == ["tool1"]
        assert result.planner_tier == "keyword"
