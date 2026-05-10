import pytest

from vibe.preferences.macro_session import MacroSession, MacroSessionRunner, MacroStep


class TestMacroSessionRunner:
    @pytest.fixture
    def runner(self, tmp_path, monkeypatch):
        runner = MacroSessionRunner()
        # Redirect macro dir into tmp_path so tests are hermetic
        monkeypatch.setattr(runner, "MACRO_DIR", tmp_path / "macros")
        runner.MACRO_DIR.mkdir(parents=True, exist_ok=True)
        return runner

    @pytest.mark.asyncio
    async def test_run_simple_sequence(self, runner):
        macro = MacroSession(
            name="greet",
            steps=[
                MacroStep(name="step1", query="Hello {{ name }}", store_result_as="greeting"),
                MacroStep(name="step2", query="Say: {{ greeting }}"),
            ],
        )
        result = await runner.run(macro, initial_vars={"name": "Alice"})
        assert result["greeting"] == "Hello Alice"
        assert result["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_condition_skip(self, runner):
        macro = MacroSession(
            name="conditional",
            steps=[
                MacroStep(name="always", query="first", store_result_as="a"),
                MacroStep(
                    name="skip_me",
                    query="second",
                    condition="false",
                    store_result_as="b",
                ),
                MacroStep(
                    name="run_me",
                    query="third",
                    condition="true",
                    store_result_as="c",
                ),
                MacroStep(
                    name="zero_skip",
                    query="fourth",
                    condition="0",
                    store_result_as="d",
                ),
                MacroStep(
                    name="empty_skip",
                    query="fifth",
                    condition="''",
                    store_result_as="e",
                ),
            ],
        )
        result = await runner.run(macro)
        assert result["a"] == "first"
        assert "b" not in result
        assert result["c"] == "third"
        assert "d" not in result
        assert "e" not in result

    @pytest.mark.asyncio
    async def test_save_and_load(self, runner):
        macro = MacroSession(
            name="deploy",
            description="Deploy to staging",
            trigger="on_command:deploy",
            steps=[
                MacroStep(
                    name="build",
                    query="build {{ target }}",
                    store_result_as="build_output",
                    condition="target != ''",
                    timeout=60.0,
                ),
            ],
            variables={"target": "app"},
        )
        runner.save_macro(macro)

        assert "deploy" in runner.list_macros()

        loaded = runner.load_macro("deploy")
        assert loaded.name == "deploy"
        assert loaded.description == "Deploy to staging"
        assert loaded.trigger == "on_command:deploy"
        assert loaded.variables == {"target": "app"}
        assert len(loaded.steps) == 1
        step = loaded.steps[0]
        assert step.name == "build"
        assert step.query == "build {{ target }}"
        assert step.store_result_as == "build_output"
        assert step.condition == "target != ''"
        assert step.timeout == 60.0

    @pytest.mark.asyncio
    async def test_run_with_macro_variables(self, runner):
        macro = MacroSession(
            name="vars",
            variables={"env": "prod"},
            steps=[
                MacroStep(name="s1", query="deploy to {{ env }}", store_result_as="out"),
            ],
        )
        result = await runner.run(macro)
        assert result["out"] == "deploy to prod"
        assert result["env"] == "prod"

    @pytest.mark.asyncio
    async def test_initial_vars_override_macro_variables(self, runner):
        macro = MacroSession(
            name="override",
            variables={"env": "prod"},
            steps=[
                MacroStep(name="s1", query="deploy to {{ env }}", store_result_as="out"),
            ],
        )
        result = await runner.run(macro, initial_vars={"env": "staging"})
        assert result["out"] == "deploy to staging"
        assert result["env"] == "staging"
