# Third-party notices

AgentForeman's local web UI includes material from **Codex Tools**:

- Project: https://github.com/170-carry/codex-tools
- Revision: `b8f2dbb829288ce7b1c978925062bef58fd3291e`
- Copyright (c) 2026 170-carry
- License: MIT, reproduced in full at
  `src/agentforeman/web/upstream/LICENSE` and included in the installed package.

Seven stylesheet files in `src/agentforeman/web/upstream/` are copied verbatim.
`PROVENANCE.json` maps each local file to its source path and upstream Git blob
hash. Top navigation, SVG icon paths and quota meter markup in `index.html` and
`app.js` are adapted from `ClassicTopBar.tsx`, `WorkspaceIcon.tsx` and
`ClassicUsageMeter.tsx`. The React/Tauri bindings were replaced with the local
AgentForeman API; the account list is adapted into an experiment task list.

AgentForeman branding, experiment forms, local HTTP API and durable execution
integration are provided by this project. No upstream account credentials,
account-management backend, API proxy or desktop binaries are included.
