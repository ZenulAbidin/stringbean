# X/Twitter announcement draft

## Single-post version

🚀 I just shipped **stringbean** — a tiny local orchestrator that runs multi-agent coding workflows using your existing CLIs (Codex, Claude, Grok, and generic tools).

No giant UI. No API-key plumbing. Just filesystem-first state, resumable runs, and inspectable run artifacts.

It now includes a local slash-style launcher (`sbx`) plus Codex and Grok Build plugin wrappers.

Try it: github.com/<your-org>/stringbean
#codingagents #llm #python #openai #claude #grok

## 2-post thread starter

1/3
I built **stringbean** to orchestrate multiple coding agents locally.

Use one orchestrator flow:
- planning
- advisor review
- implementation
- reviewer checks

while keeping everything saved under `.stringbean/runs/`.

2/3
No API keys in this layer — it shells out to existing CLIs and uses local YAML config.

Built-in roles:
- orchestrator
- advisor
- implementer
- reviewer

3/3
GitHub release notes + docs are now release-ready.
If you're curious, check the setup in the repo and run a smoke test:
`stringbean init && stringbean doctor`

Link: github.com/<your-org>/stringbean

## Alternative short copy

🌱 Open-sourced a practical alternative to monolithic agent wrappers: **stringbean**.
Local-first, filesystem-audited, and easy to adapt to your preferred models/providers.

#opensource #devtools #AI
