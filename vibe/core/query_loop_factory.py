"""Factory for creating wired QueryLoop instances."""

from typing import Any, Dict, List, Optional

from vibe.core.model_gateway import LLMClient
from vibe.core.query_loop import QueryLoop
from vibe.core.context_compactor import ContextCompactor
from vibe.core.error_recovery import ErrorRecovery, RetryPolicy
from vibe.harness.constraints import HookPipeline
from vibe.tools.tool_system import ToolSystem
from vibe.tools.bash import BashTool, BashSandbox
from vibe.tools.file import ReadFileTool, WriteFileTool


class QueryLoopFactory:
    """Centralized factory for creating QueryLoop instances with consistent wiring."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: Optional[str] = None,
        working_dir: str = ".",
        fallback_chain: Optional[List[str]] = None,
        timeout: Optional[float] = None,
        max_iterations: int = 10,
        max_context_tokens: Optional[int] = None,
        with_compactor: bool = False,
        with_error_recovery: bool = False,
        with_hooks: bool = False,
        config: Optional[Any] = None,
    ):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.working_dir = working_dir
        self.fallback_chain = fallback_chain or []
        self.timeout = timeout
        self.max_iterations = max_iterations
        self.max_context_tokens = max_context_tokens
        self.with_compactor = with_compactor
        self.with_error_recovery = with_error_recovery
        self.with_hooks = with_hooks
        self.config = config

    def create_llm(self) -> LLMClient:
        kwargs: Dict[str, Any] = {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": self.api_key,
            "fallback_chain": self.fallback_chain,
            "auto_fallback": bool(self.fallback_chain),
        }
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        return LLMClient(**kwargs)

    def create_tool_system(self) -> ToolSystem:
        tool_system = ToolSystem()
        bash_timeout = 120
        if self.config is not None:
            llm_cfg = getattr(self.config, "llm", None)
            if llm_cfg is not None:
                bash_timeout = getattr(llm_cfg, "timeout", 120)
        tool_system.register_tool(
            BashTool(sandbox=BashSandbox(working_dir=self.working_dir, timeout=bash_timeout))
        )
        tool_system.register_tool(ReadFileTool())
        tool_system.register_tool(WriteFileTool())
        return tool_system

    def create(self, max_iterations: Optional[int] = None) -> QueryLoop:
        llm = self.create_llm()
        tools = self.create_tool_system()
        kwargs: Dict[str, Any] = {
            "llm_client": llm,
            "tool_system": tools,
            "max_iterations": max_iterations if max_iterations is not None else self.max_iterations,
        }
        if self.max_context_tokens is not None:
            kwargs["max_context_tokens"] = self.max_context_tokens
        if self.with_compactor:
            kwargs["context_compactor"] = ContextCompactor(
                max_tokens=self.max_context_tokens or 12000,
                config=self.config,
            )
        if self.with_error_recovery:
            retry_cfg = getattr(self.config, "retry", None) if self.config else None
            if retry_cfg is not None:
                kwargs["error_recovery"] = ErrorRecovery(
                    RetryPolicy(
                        max_retries=getattr(retry_cfg, "max_retries", 2),
                        initial_delay=getattr(retry_cfg, "initial_delay", 1.0),
                    )
                )
            else:
                kwargs["error_recovery"] = ErrorRecovery(
                    RetryPolicy(max_retries=2, initial_delay=1.0)
                )
        if self.with_hooks:
            kwargs["hook_pipeline"] = HookPipeline()
        if self.config is not None:
            kwargs["config"] = self.config
        return QueryLoop(**kwargs)

    @classmethod
    def from_profile(cls, profile, working_dir: str = "/tmp", config: Optional[Any] = None) -> "QueryLoopFactory":
        """Create a factory from a ModelProfile (used by multi-model runner)."""
        max_iterations = 15
        max_context_tokens = 16000
        if config is not None:
            ql_cfg = getattr(config, "query_loop", None)
            if ql_cfg is not None:
                max_iterations = getattr(ql_cfg, "max_iterations", max_iterations)
                max_context_tokens = getattr(ql_cfg, "max_context_tokens", max_context_tokens)
        return cls(
            base_url=profile.base_url,
            model=profile.model_id,
            api_key=profile.resolve_api_key(),
            working_dir=working_dir,
            timeout=profile.timeout,
            max_iterations=max_iterations,
            max_context_tokens=max_context_tokens,
            with_compactor=True,
            with_error_recovery=True,
            with_hooks=True,
            config=config,
        )
