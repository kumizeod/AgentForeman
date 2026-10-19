# References and attribution

The following primary sources informed the design. No third-party source code
was copied or vendored into this implementation.

- [OpenAI: Codex as a platform](https://developers.openai.com/blog/codex-as-a-platform)
  — programmatic integration surfaces and the boundary between harness and product.
- [OpenAI: Build iterative repair loops with Codex](https://developers.openai.com/cookbook/examples/codex/build_iterative_repair_loops_with_codex)
  — headless Codex, structured handoffs, independent execution evidence and bounded loops.
- [karpathy/autoresearch](https://github.com/karpathy/autoresearch)
  — bounded editable scope, real experiment execution and measured outcomes.
- [Agent Orchestrator](https://github.com/OrchestratorInc/agent-orchestrator)
  — task/workspace ownership and coding-agent lifecycle visibility.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
  — durable progress and explicit recovery boundaries. AgentForeman does not
  depend on LangGraph.

References were reviewed on 2026-10-19. CLI interfaces can change. A live Codex
compatibility check is explicitly pending in this release's validation notes.
