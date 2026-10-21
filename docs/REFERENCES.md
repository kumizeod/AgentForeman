# References and attribution

The following primary sources informed the design. The Python experiment engine
is independently implemented. The v0.6 web panel vendors MIT-licensed codex-tools
styles and ports selected UI components; see [notices](../NOTICE.md) and [panel guide](WEB_UI.md).

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
- [Python subprocess](https://docs.python.org/3/library/subprocess.html)
  — native creation flags, session/process-group differences, and Windows batch-file parsing.
- [psutil documentation](https://psutil.readthedocs.io/stable/)
  — native process creation time, descendant discovery and process termination.
- [Codex native Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)
  — native Windows workflow and sandbox configuration; WSL is optional.

References were reviewed on 2026-10-19. CLI interfaces can change. A live Codex
compatibility check is explicitly pending in this release's validation notes.

- [170-carry/codex-tools](https://github.com/170-carry/codex-tools/tree/b8f2dbb829288ce7b1c978925062bef58fd3291e)
  — reviewed on 2026-10-21; MIT (copyright 2026 170-carry). Quota window visibility,
  cache freshness, token analytics and operational CLI ideas informed v0.5.
  Implementation is independent; no source copied or new dependency.
  [Adoption decisions and exact source files](OBSERVABILITY.md#reference-scope).
- [Official Codex app-server](https://learn.chatgpt.com/docs/app-server)
  — reviewed 2026-10-21: initialize/initialized and account/rateLimits/read,
  multi-bucket selection, usedPercent/windowDurationMins/resetsAt. Metadata reads
  are separate from model turns.

The v0.6 UI directly vendors seven stylesheets and LICENSE from the same
codex-tools revision and ports ClassicTopBar, ClassicUsageMeter and WorkspaceIcon.
This supersedes the v0.5 statement that no code was copied for the operations CLI.
The full source-path/hash mapping and MIT notice ship with the web assets.
