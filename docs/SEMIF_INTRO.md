# SemIf（原 OpenJev）技术解析与实践指南：System 1 语义决策原语

> **文档定位**：系统化阐述 SemIf（Semantic If）的技术原理、落地使用方法以及与 TypeSafe Jev 的深度对比，为本地化低延迟决策门控提供理论与实践参考。
>
> **来源声明**：本文事实性内容以一手来源为准——[SemIf GitHub 仓库](https://github.com/TheoLeeCJ/SemIf)（README 及已提交的基准原始数据）、[openjev.com](https://openjev.com) 在线演示、Ollama 官方文档。所有基准数字均可在这两个来源中复核；推测与厂商宣称均已显式标注。
>
> 最近事实核查：2026-09-20。

---

## 1. 什么是 SemIf（Semantic If）

### 1.1 概念来源与正名

**SemIf**（Semantic If，语义条件分支）是独立开发者 **TheoLeeCJ** 的开源研究项目（MIT License），**前身为 OpenJev**，于 2026-09-18 更名。项目独立运作，**与 TypeSafe 无任何隶属或背书关系**——它复刻的是 Jev 的**接口形态**（typed options → probabilities），而非 Jev 未公开的模型与训练。

背景：2026 年 9 月 TypeSafe 发布了闭源托管服务 **Jev**——一个专为"类型化判断"（typed judgments）打造的 System 1 决策模型，宣称输出经过校准的概率。SemIf 要回答的问题是：**用冻结的开源模型，不做任何训练，能否在本地复现这一接口模式？** 答案是肯定的。

传统的代码控制流依赖布尔逻辑（如 `if user.age > 18`），但现实世界的大量决策场景（意图分类、安全防护拦截、文本相关性过滤、数据合规校验）需要的是"常识判断与语义理解"。SemIf 把这种语义判断变成一个可在运行时定义的、毫秒级的原语。

### 1.2 传统方案的痛点：结构化 JSON 输出的局限

在传统 LLM 应用中，实现语义判断的标准做法是让大模型生成结构化 JSON：

```json
{
  "is_dangerous": true,
  "confidence": 0.95,
  "reasoning": "The command attempts to delete the root directory."
}
```

这种方案在生产环境中面临四大核心瓶颈：

1. **高延迟（Autoregressive Decoding Latency）**：自回归生成逐 Token 进行。SemIf 实测：同一冻结 Qwen3.5-4B 回答 21 个二元判断，即使输出极度压缩的 JSON 数组（仅有序 `"yes"/"no"`，共 111 个输出 Token）也需 **5.332 s**，而直接读出仅需 **1.023 s**（5.21× 差距）。
2. **置信度幻觉（Uncalibrated Self-reported Confidence）**：模型在文本中写下的 `0.95` 只是自回归生成的文字符号，并非真实概率分布——语言模型生成的自报置信度普遍**校准不良（过度自信）**，不能当作统计量使用。
3. **解析脆弱性（Syntax & Parsing Fragility）**：模型可能输出 Markdown 代码块标记（` ```json `）、多余的前言解释、或者格式微损的 JSON，导致客户端解析崩溃或进入重试泥潭。
4. **高昂推理成本**：高频低熵的二元决策任务调用庞大的前沿大模型，算力与经济成本极不匹配。

### 1.3 SemIf 的核心机制

SemIf 的思想是：**把 LLM 当作"语义判断原语"（Programmable Common Sense），而非"文本生成器"**。

其关键观察是：当向指令微调模型提出一个选项编号化的选择题时，答案其实已经编码在**下一个 Token 的概率分布**里——根本不必把它采样出来。因此 SemIf 做一次**纯预填充前向传播（prefill-only，0 个输出 Token）**，直接在答案位置读取各选项标号 Token 的原始 Logits，只对声明的选项做归一化。没有解码循环，没有 JSON 修复，输出在构造上就是一个浮点数数组。

> **辨析：SemIf 原生读出 vs. OpenAI 兼容 API 变体**
> SemIf 官方实现直接驱动模型（HF Transformers / MLX / wllama），因此能做到真正的 **0 输出 Token**。若通过 OpenAI 兼容 HTTP 服务（llama-server、Ollama）使用同一模式，服务端只在**生成的 Token** 上暴露 logprobs，因此需要 `max_tokens=1` 采样一个 Token 再读其分布——数学相同，仅多一个采样 Token 的开销。本文 §2 会区分这两个层次；Vibe Agent 的 `FastGateClient` 属于后者。

---

## 2. 核心工作原理（How Does It Work?）

SemIf 的底层机制可以概括为：**选项 Logits 直接读出（Direct-Logit Readout）+ 条件 Softmax 归一化**。Vibe Agent 在落地时另加了严格的安全不变量防护（见 §2.4）。

```
┌─────────────────────────────────────────────────────────────┐
│                       输入提示词 (Prompt)                    │
│   状态 (State) + 运行时判据 (Criteria) + 类型化选项 (A…T)     │
│   "Does this pose a security risk? A: Safe, B: Dangerous"   │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│   本地模型单次前向传播（如 Qwen3.5-4B，冻结权重，不做训练）    │
│   SemIf 原生：prefill-only，0 输出 Token                     │
│   API 变体：max_tokens=1, logprobs=True, top_logprobs=5      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│              读取答案位置各选项 Token 的 Logprob             │
│             logprob(A) = -0.05, logprob(B) = -4.12          │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│            数值稳定两路 Softmax (Two-way Softmax)            │
│         P(B: Dangerous) = exp(l_B) / [exp(l_A) + exp(l_B)]  │
└──────────────────────────────┬──────────────────────────────┘
                               │
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
        P(B) ≥ reject_confidence       P(B) < threshold
        ──► 立即阻断 (Fast Veto)       ──► 升级至完整管线 (Escalate)
```

### 2.1 单 Token 对数概率读出机制

当调用兼容 OpenAI 协议的推理后端（如 `llama-server`）时，通过设定以下推理参数：

- `max_tokens = 1`：只产生一个答案 Token，不进入多步自回归解码。
- `logprobs = True, top_logprobs = 5`：要求服务端返回概率最高的前 5 个候选 Token 及其对数概率。
- `temperature = 0.0`：确定性评估。
- `chat_template_kwargs: {"enable_thinking": false}`：对默认开启思考链的模型（如 Qwen3.5）强制跳过 `<think>`，保证首 Token 即答案。

此时省掉的是**自回归解码循环**（逐 Token 采样 + 文本序列化），而非全部计算——Prefill 仍需完整处理 Prompt 并建立 KV Cache。响应延迟随之从秒级降至约 50~100 ms（Apple M4 / 消费级 GPU 量级；SemIf 在 RTX 3090 上实测 21 个二元判断合计 1.023 s，约合 49 ms/决策）。

### 2.2 候选 Token 集合与条件 Softmax 计算

在 Prompt 尾部固定格式并指定选择项，例如：

```text
A: Safe / Benign
B: Dangerous / Risky
Answer with a single letter (A or B):
```

从服务端返回的 `logprobs.content[0].top_logprobs` 中，提取候选集合 $\mathcal{C} = \{A, B\}$ 的值 $\ell_A$ 和 $\ell_B$，做数值稳定的归一化：

$$P(\text{Dangerous}) = \frac{e^{\ell_B}}{e^{\ell_A} + e^{\ell_B}} = \frac{e^{\ell_B - \max(\ell_A, \ell_B)}}{e^{\ell_A - \max(\ell_A, \ell_B)} + e^{\ell_B - \max(\ell_A, \ell_B)}}$$

> **术语澄清（Logits vs. Logprobs）**：OpenAI 兼容 API 返回的是 **logprob**（经全集归一化的对数概率，如 `-0.05`），而非原始未归一化 logit。但二者仅相差一个对全词表所有 Token 都相同的常数（`logsumexp`），在两路 Softmax 中该常数相互抵消——因此用 logprob 算出的条件概率与用原始 logit 完全一致。这正是 API 变体在数学上成立的原因。

> **重要数学性质（Conditional Two-way Score）**：
> 正如 SemIf 官方明确声明的：**该数值并不是全集空间下的校准置信度（calibrated confidence）**。它排除了 $\{A, B\}$ 之外所有其他 Token 的概率质量（包括模型本想回答的"都不是"），只是一个在 $A$ 与 $B$ 之间的**相对条件分值**。因此判定阈值（如 `reject_confidence = 0.85`）不能凭空假设，必须通过经验阈值扫描（Empirical Threshold Sweep）在目标工作负载上确定。

### 2.3 共享状态前缀复用（SemIf 的第二项核心技术）

当多个决策共享同一长状态（如对同一份代码库上下文逐条评估 21 个判据），SemIf 支持 **prefill 一次、分支多次**：

| 执行路径 | 决策/秒 | 完成 777 个决策 |
| :--- | ---: | ---: |
| 全新直接读出（Fresh direct） | 2.33 | 333.1 s |
| 串行前缀复用（Serial prefix reuse） | 10.75 | 72.3 s |
| 并行后缀（Parallel suffixes） | **20.03** | **38.8 s** |
| 原生 Reranker 对照 | 1.86 | 417.3 s |

（SemIf 自有 37 状态 × 21 判据基准，RTX 3090，Qwen3.5-4B。）前缀复用相比全新评分提速约 8.6×。**官方披露的注意事项**：BF16 下的前缀复用改变了 777 个 argmax 中的 5~6 个——数值接近但并非逐位一致，安全敏感场景应实测验证。

### 2.4 工程防护与不变量（Vibe Agent 落地增补）

SemIf 官方声明的范围仅覆盖 §2.2 的条件概率警示。以下 5 层防护是 **Vibe Agent 在生产落地时自行增加的**（详见 `docs/plans/2026-09-19-decision-gate-diffusiongemma.md` 及 `vibe/tools/security/fast_gate.py`），并非 SemIf 项目本身的内容：

1. **候选 Token 缺失防护（Missing Candidate Check）**：
   - 如果 `top_logprobs` 中包含 $B$ 但完全没有 $A$（或反之），不能随意人为赋予一个地板值（如 $-20.0$），否则人为制造出 $P(\text{Dangerous}) = 1.0$ 的虚假高置信度。
   - **防御策略**：只要 $\{A, B\}$ 任意一个不在 `top_logprobs` 中，判定为**不确定，立即升级（Escalate）**交由主 LLM 裁决。
2. **Argmax 逃逸防护（Argmax Guard）**：
   - 模型实际输出的最大概率 Token（即模型第一倾向）必须落在 $\{A, B\}$ 内。
   - 如果模型吐出的首个 Token 是换行符、标点、或者推理模型的思考标记 `<think>`，说明模型未遵循格式，两路 Softmax 将彻底失真。此时必须升级。
3. **推理模型思考抑制（Disable Thinking for Reasoning Models）**：
   - 诸如 Qwen3.5、DeepSeek-R1 等默认开启思考链的模型，其聊天模版会先吐出 `<think>` 标签，破坏首 Token 读取。
   - **防御策略**：在请求体中显式注入 `chat_template_kwargs: {"enable_thinking": false}`。
4. **提示词注入隔离（Fencing & Marker Munging）**：
   - 不信任的外部输入（如工具参数、上下文）可能蓄意伪造闭合标记或伪造指令。
   - **防御策略**：严格使用安全边界包裹（`UNTRUSTED_ARGS_BEGIN` / `UNTRUSTED_ARGS_END`），并在入模前对内容中出现的边界词实施字符串混淆（Munging）。
5. **纯否决门控模式（Veto-Only Pattern）**：
   - **核心安全定理**：小模型（4B 级别）的能力有限，**绝不能拥有"批准（Approve）"的权力**。如果小模型可以判定放行，攻击者只需绕过小模型即可瘫痪整个防御。
   - 门控只能做两件事：
     - **高置信度拦截（Veto / Early Reject）**：明确发现已知威胁，毫秒级快速阻断。
     - **不确定/判定安全时升级（Escalate）**：绝不擅自批准，而是交由后续的 Frontier LLM 或人工审批流裁决。

---

## 3. 如何使用 SemIf（How to Use It?）

### 3.1 本地推理后端选型与配置

Logit 读出模式依赖服务端返回完整的 `logprobs` 结构。按推荐顺序：

#### 方案 A：llama.cpp (`llama-server`) —— 基准参考实现

`llama.cpp` 对 OpenAI 协议的 `logprobs` 和 `top_logprobs` 支持完整、行为确定：

```bash
# 启动 Qwen3.5-4B GGUF 服务（Metal GPU 加速，绑定 8080 端口）
# 注意选择固定版本（pinned）的 GGUF 构建以便复现
llama-server \
  -hf <owner>/Qwen3.5-4B-GGUF \
  --port 8080 \
  --host 127.0.0.1 \
  -ngl 99 \
  --ctx-size 4096
```

#### 方案 B：Ollama

Ollama 自 **v0.12.11（2025-11）起已在原生 API 与 OpenAI 兼容接口同时支持 `logprobs` / `top_logprobs`**（见[官方兼容性文档](https://docs.ollama.com/api/openai-compatibility)）。两处注意：

- **Ollama Cloud**（ollama.com 托管）会接受 `logprobs` 参数但返回 `null`（[ollama#13638](https://github.com/ollama/ollama/issues/13638)）——仅本地实例可用。
- 历史上 OpenAI 兼容路由对 `logprobs` 的支持曾有缺口（[ollama#16117](https://github.com/ollama/ollama/issues/16117)），不同版本行为可能不一致。

因此客户端应具备**能力探针（Capability Probe）**：初次连接时自动探测服务端是否真实返回 `logprobs`，支持则启用 `logit` 模式，不支持则平滑退避至约束 JSON 模式。

```bash
ollama run qwen3.5:4b   # 模型名以 Ollama 模型库实际条目为准
```

#### 方案 C：SemIf 原生 MLX 后端（Apple Silicon）

SemIf 自带 **macOS arm64 原生 MLX 后端**，支持直接评分、串行前缀复用与并行共享状态决策，无需经过任何 HTTP 服务：

```bash
pip install -e '.[test,mlx]'
semif-score --backend mlx --mode direct --model Qwen/Qwen3.5-4B ...
```

#### 方案 D：浏览器 WebGPU 演示（零安装体验）

[openjev.com](https://openjev.com) 提供纯浏览器演示：权重以 GGUF 形式从 Hugging Face 拉取，经 wllama + WebGPU 在本地运行，输入不出浏览器页面。可并排对比"直接读出"与"JSON 生成"两条路径的实测耗时。

### 3.2 客户端核心实现（以 FastGateClient 为例）

以下为提取自生产实现的精简版门控客户端（完整实现参见 `vibe/tools/security/fast_gate.py`，含 fencing/munging 与 JSON 回退模式）：

```python
import math
import httpx
from dataclasses import dataclass
from typing import Any

@dataclass
class GateVerdict:
    reject: bool = False
    confidence: float = 0.0
    reasoning: str = ""

class FastGateClient:
    """Veto-only 门控：只能高置信拦截或升级，永不批准。"""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "qwen3.5:4b",
        timeout: float = 0.5,
        reject_confidence: float = 0.85,
        mode: str = "auto",  # auto | logit | json
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.reject_confidence = reject_confidence
        self.mode = mode
        self._resolved_mode: str | None = None if mode == "auto" else mode
        self._client = httpx.Client(timeout=timeout)

    def _probe_mode(self) -> str:
        """能力探针：发一条最小请求，确认服务端真实返回 logprobs。
        探测成功则缓存结果；失败不缓存（服务可能稍后恢复）。"""
        if self._resolved_mode is not None:
            return self._resolved_mode
        # 实际实现：POST 一条 "Reply with the single letter A." 探测请求，
        # 返回 logprobs -> "logit" 并缓存；否则 -> "json"。
        ...

    def check_risk(self, tool_name: str, tool_args: dict[str, Any]) -> GateVerdict:
        prompt = (
            "Assess the security risk of this tool execution:\n"
            f"Tool: {tool_name}\n"
            f"Arguments: {tool_args}\n\n"  # 生产实现：UNTRUSTED_ARGS 围栏 + munging
            "Does this pose a serious security risk?\n"
            "A: Safe / Benign\n"
            "B: Dangerous / Risky\n\n"
            "Answer with a single letter (A or B):"
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1,
            "logprobs": True,
            "top_logprobs": 5,
            "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            resp = self._client.post(f"{self.base_url}/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            # 失败安全（Fail-safe）：网络异常时绝不盲目阻断，降级升级
            return GateVerdict(reject=False, reasoning=f"gate_error: {e}")

        logprobs_content = (
            (data.get("choices", [{}])[0].get("logprobs") or {}).get("content", [])
        )
        if not logprobs_content:
            return GateVerdict(reject=False, reasoning="no logprobs supported, escalate")

        first_token = logprobs_content[0]
        argmax = first_token.get("token", "").strip().upper()
        # Guard 1: Argmax 必须在 {A, B} 中
        if argmax not in ("A", "B"):
            return GateVerdict(reject=False, reasoning=f"argmax outside candidates: {argmax}")

        logits = {}
        for item in first_token.get("top_logprobs", []):
            t = item.get("token", "").strip().upper()
            if t in ("A", "B") and item.get("logprob") is not None:
                logits[t] = item["logprob"]
        # Guard 2: 候选集合必须齐全
        if "A" not in logits or "B" not in logits:
            return GateVerdict(reject=False, reasoning="candidate missing from top_logprobs")

        l_a, l_b = logits["A"], logits["B"]
        max_l = max(l_a, l_b)
        p_dangerous = math.exp(l_b - max_l) / (math.exp(l_a - max_l) + math.exp(l_b - max_l))

        if p_dangerous >= self.reject_confidence:
            return GateVerdict(
                reject=True,
                confidence=round(p_dangerous, 4),
                reasoning=f"High risk veto: P(B)={p_dangerous:.2f}",
            )
        return GateVerdict(reject=False, confidence=round(p_dangerous, 4), reasoning="escalate")
```

### 3.3 典型应用模式

| 应用模式 | 运作机制 | 预期收益 |
| :--- | :--- | :--- |
| **安全预过滤门控 (Fast Veto Gate)** | 在执行高危 Bash / 文件写入等 Tool 前先行经过门控。高置信度恶意立即拦截。 | 设计目标：恶意调用拦截延迟 p50 < 200 ms（待 Phase 3 基准实测）；节约主模型 Token 成本。 |
| **意图与智能路由 (Semantic Routing)** | 判定用户请求应走"查数据库"、"文档搜索"还是"代码编辑"分支。 | 取代复杂的正则或昂贵的主模型分类 Prompt，毫秒级分流。 |
| **共享状态批量判定 (Shared-state Batch)** | 同一长状态 prefill 一次，并行评估多条判据（SemIf `--mode shared`）。 | SemIf 实测：相比逐条全新评分提速约 8.6×（2.33 → 20.03 决策/秒）。 |
| **级联抽取与验证 (Extraction Cascade)** | 小模型先对文本断言进行单 Token 布尔判定，不满足断言时才唤醒推理模型。 | 减少无谓的深度推理调用（比例取决于工作负载，需实测）。 |

---

## 4. SemIf 与 TypeSafe Jev 的深度对比

在 System 1 决策领域，**TypeSafe Jev** 是开创性的先驱（闭源托管服务），而 **SemIf** 是基于冻结开源模型的**接口模式复刻**——两者不是同一层级的产物：一个是专门训练的模型，一个是不做训练的推理时模式。

### 4.1 背景与设计定位对比

- **TypeSafe Jev**：
  - 由 TypeSafe 专门训练的**专用 System 1 决策模型**，闭源、纯托管（官方 API 及 OpenRouter 等网关转售），未发布权重。
  - 以"类型化判断（Typed Judgments）与校准概率（Calibrated Probabilities）"为目标构建，提供专有 SDK（Python/JS）。架构、训练方法与延迟指标均未公开。
- **SemIf**：
  - 一种**开源模式与参考实现**（MIT），复刻 Jev 的输入/输出形态。
  - 基于通用开源模型（`Qwen3.5-4B`、`MiniCPM5-2B`、`Qwen3-0.6B`），通过单步前向传播读取 Logits 模拟 System 1。完全本地部署，无需任何厂商服务。

### 4.2 核心维度对比矩阵

| 对比维度 | TypeSafe Jev | SemIf (基于 Qwen3.5-4B) |
| :--- | :--- | :--- |
| **开源属性** | 闭源商业模型 (Hosted API) | 100% 开源模式 + 开放权重 (HF/GGUF) |
| **底层架构** | 未公开（闭源专用决策模型） | 标准自回归 Transformer，prefill-only 读出（API 变体 `max_tokens=1`） |
| **概率性质** | **校准概率**（厂商宣称，经专门校准训练） | **条件分值**（仅候选间相对 Softmax，官方明确声明非校准置信度，需经验调阈值） |
| **端到端延迟** | 未公开；云端 API 含网络 RTT | 实测约 49 ms/决策（RTX 3090）；本地无网络开销 |
| **硬件与算力需求** | 无需本地硬件 | 约 3 GB 内存/显存（Q4_K_M 量化） |
| **离线可用性** | ❌ 依赖外网与厂商服务 SLA | ✅ **完全离线运行** |
| **数据隐私安全** | 敏感状态/代码需传输至云端 | ✅ **代码与状态零泄露** |
| **使用成本** | 按调用量计费（SaaS/API） | ✅ **零 Token 费用**（仅本地算力） |
| **输出丰富度** | 官方文档列出 Choice / Score 等多种判断类型 | 主要用于二元/有限类别 Select 判断 |
| **决策质量（官方子集）** | 88.3%（厂商发布值，见 §4.3 注） | 84.5%（同一 102 行公开子集的一致率） |

### 4.3 准确率与一致性实测数据（SemIf 基准）

以下来自 SemIf 仓库已提交的基准原始数据（原生 BF16 评分；浏览器演示使用量化 GGUF，量化可能改变精度与速度）：

| 模型 | 规模 / 浏览器构件 | Authored 平衡准确率 | Perturbation 平衡准确率 | TypeSafe 子集一致率 |
| :--- | :--- | ---: | ---: | ---: |
| Qwen3 0.6B | Q8_0，639 MB | 44.0% | 52.8% | 40.7% |
| MiniCPM5 2B | Q4_K_M，1.56 GB | 68.6% | 69.3% | 63.7% |
| **Qwen3.5 4B** | Q4_K_M，3.01 GB | **81.3%** | 76.6% | **84.5%** |
| Published Jev | 闭源托管 | —（未测） | —（未测） | 88.3%（厂商发布值） |

**数据解读须知**：

- TypeSafe 列为对同一 **102 行公开子集**（20 个案例）的等例一致率；SemIf 并未运行实时 Jev 端点，88.3% 读自 TypeSafe 已发布的记录，且该子集并非 TypeSafe 报告的 711 行总集。
- **模型规模至关重要**：0.6B 接近掷硬币（44%），2B 在安全门控场景误差过大（约 31% 漂移），4B 是当前质量/体积的甜点。
- 另一组对照：在通用决策任务上，直接 Logits 读出（81.3%）显著优于同规模**原生 Reranker**（62.5%）；Reranker 仅在检索排序类任务上保持竞争力。外部 NLI  sanity check（WANLI）：直接读出 63.7% vs Reranker 52.2%。

*结论*：Qwen3.5 4B 在开源模型中对 Jev 达到 84.5% 的一致率，且在 M4 Mac 上仅约 3 GB 内存，是当前本地离线 System 1 门控的最现实载体。但注意：一致率是**选型启发式**，不等同于在特定攻击分布上的拦截准确率——后者必须在目标工作负载上实测。

---

## 5. 在 Vibe Agent 中的落地实践（Fast Veto Gate）

在 Vibe Agent 架构中，SemIf 模式被具象化为 `FastGateClient`，与已有的 5 层安全体系协同工作：

```
tool call ──► Layer 1: PatternEngine (正则黑名单)
                 │
                 ▼ Layer 2: FileSafetyGuard (路径沙箱)
                 │
                 ▼ Layer 3: HumanApprover (人工/自动审批模式)
                 │
                 ▼ Layer 4: SmartApprover (语义智能风险评估)
                     │
                     ├── 4a: FastGateClient (SemIf 模式 / Qwen3.5 4B)
                     │       ├── 极高置信危险 ──► 立即阻断（设计目标 p50 < 200 ms，省去主模型调用）
                     │       └── 不确定 / 安全 ──► 平滑穿透
                     │
                     └── 4b: 主 Frontier LLM 风险深审 (详查上下文与复杂意图)
```

- **零安全倒退（Zero Security Regression）**：由于坚持了纯否决不变量（Veto-only），即使门控出现漏判，系统只是退回到原有的主 LLM 审批路径，安全水位绝不下降。
- **能力探针与容错（`mode = "auto"`）**：启动时自动探测端点是否支持 `logprobs`，支持则用 logit 模式，否则退避约束 JSON 模式；探测失败不缓存（服务恢复后自动重试）。本地服务不可达时静默升级，永不因门控故障导致 Agent 瘫痪。
- **验证状态**：门控有效性以 Phase 3 基准为准（验收标准：拦截率不低于基线、误报率不高于基线、门控自身延迟 p50 < 200 ms）。`scripts/eval_fast_gate.py` 为测量入口。在基准跑通之前，本文所有性能数字均为设计目标而非实测结论。

---

## 6. 总结与选型指南

1. **选 TypeSafe Jev 的场景**：
   - 需要厂商宣称的校准概率，并依据概率值做精细化排序与数值阈值判定（如金融风控）。
   - 纯云端 SaaS 架构，可接受 API 成本、网络延迟与数据出境，且不愿维护本地模型。
2. **选 SemIf（Qwen3.5 4B 本地读出）的场景**：
   - **本地离线 Agent 与开发终端**（如 Vibe Agent、IDE 插件、私有代码审计）。
   - 涉及机密代码、Token 密钥、敏感内部系统状态，数据**坚决不能上云**。
   - 二元快速门控、意图分支分流、故障重试剪枝等高频任务，追求**零调用费用与自给自足**。
   - 愿意接受"条件分值 + 经验阈值"的工程纪律：阈值必须在自己的工作负载上扫出来，而不能照搬别人的。

---

## 7. 参考来源

| 来源 | 内容 |
| :--- | :--- |
| [github.com/TheoLeeCJ/SemIf](https://github.com/TheoLeeCJ/SemIf) | 项目仓库：方法文档、冻结 Prompt、基准 fixture 与逐行原始结果（MIT） |
| [openjev.com](https://openjev.com) | 浏览器 WebGPU 演示与模型阶梯数据 |
| [TypeSafe: Introducing System One Models and Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) | Jev 官方发布博文 |
| [Ollama OpenAI 兼容性文档](https://docs.ollama.com/api/openai-compatibility) | `logprobs` / `top_logprobs` 支持范围 |
| [ollama#13638](https://github.com/ollama/ollama/issues/13638) / [ollama#16117](https://github.com/ollama/ollama/issues/16117) | Cloud 端 logprobs 返回 null；兼容路由历史缺口 |
| `docs/plans/2026-09-19-decision-gate-diffusiongemma.md` | 本仓库 Fast Veto Gate 的决策记录与审阅历史 |
