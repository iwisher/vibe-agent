import asyncio
from vibe.core.model_gateway import LLMClient
from vibe.core.query_loop import QueryLoop
from vibe.tools.tool_system import ToolSystem
from vibe.tools.bash import BashTool, BashSandbox
from vibe.tools.file import ReadFileTool, WriteFileTool


async def run_drift_agent():
    """
    Demonstrates a Documentation Drift Agent that finds Python files,
    inspects them for docstring mismatches, and writes a report.
    """
    llm = LLMClient(
        base_url="http://ai-api.applesay.cn",
        model="qwen3.5-plus",
    )

    ts = ToolSystem()
    ts.register_tool(BashTool(sandbox=BashSandbox()))
    ts.register_tool(ReadFileTool())
    ts.register_tool(WriteFileTool())

    agent = QueryLoop(llm_client=llm, tool_system=ts)

    task_prompt = """
    Perform a documentation drift audit on the current directory:
    1. Use bash 'find . -name "*.py"' to locate Python source files.
    2. Read each file to inspect function/class signatures and docstrings.
    3. Identify drift where docstrings don't match code.
    4. Write a 'drift_report.md' summarizing findings.
    """

    print("--- Starting Documentation Drift Agent ---")
    async for result in agent.run(initial_query=task_prompt):
        if result.error:
            print(f"Error: {result.error}")
        else:
            print(result.response)
        for tr in result.tool_results:
            status = "OK" if tr.success else "FAIL"
            print(f"  [{status}] {tr.content or tr.error}")
    print("\nAudit complete. Check drift_report.md for results.")


if __name__ == "__main__":
    asyncio.run(run_drift_agent())
