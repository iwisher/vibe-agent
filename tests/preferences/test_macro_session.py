import tempfile
from pathlib import Path

from vibe.preferences.macro_session import MacroSession, MacroSessionRunner, MacroStep


class TestMacroSessionRunner:
    def test_run_simple_sequence(self):
        runner = MacroSessionRunner()
        macro = MacroSession(
            name="test",
            steps=[
                MacroStep(name="step1", query="Hello {{name}}", store_result_as="greeting"),
                MacroStep(name="step2", query="Say {{greeting}} again"),
            ],
        )

        results = runner.run(macro, {"name": "World"})
        assert "greeting" in results
        assert "Hello World" in results["greeting"]

    def test_condition_skip(self):
        runner = MacroSessionRunner()
        macro = MacroSession(
            name="conditional",
            steps=[
                MacroStep(name="always", query="run", store_result_as="ran"),
                MacroStep(name="skip", query="skip me", condition="{{skip}}", store_result_as="skipped"),
            ],
        )

        results = runner.run(macro, {"skip": False})
        assert "ran" in results
        assert "skipped" not in results

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Temporarily override MACRO_DIR
            original = MacroSessionRunner.MACRO_DIR
            MacroSessionRunner.MACRO_DIR = Path(tmp)

            try:
                runner = MacroSessionRunner()
                macro = MacroSession(name="saved", steps=[MacroStep(name="s", query="test")])
                runner.save_macro(macro)

                loaded = runner.load_macro("saved")
                assert loaded is not None
                assert loaded.name == "saved"
                assert len(loaded.steps) == 1
            finally:
                MacroSessionRunner.MACRO_DIR = original

    def test_run_with_macro_variables(self):
        runner = MacroSessionRunner()
        macro = MacroSession(
            name="vars",
            variables={"prefix": "Hello"},
            steps=[
                MacroStep(name="greet", query="{{prefix}} {{name}}", store_result_as="greeting"),
            ],
        )

        results = runner.run(macro, {"name": "World"})
        assert "Hello World" in results["greeting"]

    def test_initial_vars_override_macro_variables(self):
        runner = MacroSessionRunner()
        macro = MacroSession(
            name="override",
            variables={"name": "Macro"},
            steps=[
                MacroStep(name="greet", query="Hello {{name}}", store_result_as="greeting"),
            ],
        )

        results = runner.run(macro, {"name": "World"})
        assert "Hello World" in results["greeting"]
