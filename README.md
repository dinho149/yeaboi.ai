<div align="center">


<img src="docs/banner.jpg" alt="yeaboi.ai" width="800"/>

# 🤙 yeaboi.ai

**A team lead's best friend — plans, standups, retros, performance & reporting, right from your terminal. It decomposes projects into epics, stories, tasks, and sprint plans, then helps you run the team around them.**

[![PyPI](https://img.shields.io/pypi/v/yeaboi?style=for-the-badge&logo=pypi&logoColor=white&color=blue)](https://pypi.org/project/yeaboi/)
[![Python](https://img.shields.io/badge/Python-3.11+-green?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)
[![Powered by Claude](https://img.shields.io/badge/Powered%20by-Claude-ff6600?style=for-the-badge&logo=anthropic&logoColor=white)](https://anthropic.com)
[![Built with LangGraph](https://img.shields.io/badge/Built%20with-LangGraph-00CED1?style=for-the-badge)](https://langchain-ai.github.io/langgraph/)

[![Tests](https://img.shields.io/github/actions/workflow/status/omardin14/yeaboi.ai/ci.yml?style=for-the-badge&label=Tests&logo=github)](https://github.com/omardin14/yeaboi.ai/actions)
[![PyPI](https://img.shields.io/pypi/v/yeaboi?style=for-the-badge&logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/yeaboi/)

</div>

---

<div align="center">
<img src="docs/demo.gif" alt="yeaboi.ai demo — from project description to sprint plan" width="800"/>

*From project description to sprint plan in under a minute.*
</div>

---

## 🚀 Quick Start

### Recommended: uv or pipx

The most reliable way to install — pulls the full dependency tree from PyPI and isolates it in its own environment:

```bash
uv tool install yeaboi          # or: pipx install yeaboi
yeaboi --setup                  # configure your API key
yeaboi                          # launch the interactive TUI
```

> **Note on names:** the package was previously published as **`yeaboi`**. It is now **`yeaboi`** on PyPI (matching the command). A final `yeaboi` release remains as a thin redirect that installs `yeaboi`, and the legacy `yeaboi` command still works as an alias for this release — but new installs should use `yeaboi`.

Optional extras (voice input, extra LLM providers) can be requested at install time:

```bash
uv tool install "yeaboi[voice]"                # 🎤 dictate answers (double-tap Space) — offline, works with any LLM
uv tool install "yeaboi[all-providers]"        # OpenAI, Google, and Bedrock providers
pipx install "yeaboi[voice]"                   # equivalent with pipx
```

> **Voice input** transcribes on-device with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
> — **no API key**, works with every LLM provider (Anthropic, Bedrock, …). On **macOS/Windows** the
> `[voice]` extra is fully self-contained (the `sounddevice` wheel bundles PortAudio). On **Linux**, also
> install the system library: `sudo apt install libportaudio2`. A small Whisper model downloads on first
> use (~140 MB for the default `base`; set `VOICE_MODEL` to `tiny`/`small`/`medium`/`large-v3` to trade
> size for accuracy).

> **Homebrew is not supported.** A required dependency (`sqlite-vec`) ships no
> source distribution, which Homebrew's source-build model can't handle, so
> `brew install yeaboi` is intentionally disabled. Use `uv tool install`
> or `pipx install` above instead.

### From source

```bash
git clone https://github.com/omardin14/yeaboi.ai.git
cd yeaboi.ai
make install        # installs uv, creates venv, installs dependencies
make env            # creates .env from .env.example — add your API key
make run            # launch the CLI
```

### Headless / CI mode

```bash
yeaboi --non-interactive --description "Build a todo app" --output json
yeaboi --non-interactive --description @project-brief.txt --output html --team-size 5
```

---

## ✨ Features

🖥️ **Full-screen TUI** — Animated splash, mode selection, pipeline progress, dark/light themes
🧠 **Smart Intake** — Extracts answers from your project description, asks only what's missing — or feed it a whole quarterly roadmap with Roadmap Intake
🔄 **Six modes, one command** — Planning, Daily Standup, Retro, Performance, Reporting, Team Analysis
🔌 **35+ tools** — GitHub, Azure DevOps, Jira, Confluence, Notion, local codebase scanning, and more
📤 **5 export formats** — Markdown, HTML, JSON, Jira sync, Azure DevOps Boards sync
🤖 **5 LLM providers** — Claude (default), GPT, Gemini, AWS Bedrock, or fully local & keyless with Ollama
🧩 **Every surface** — TUI, CLI subcommands, MCP server, and a Claude Code plugin, with feature parity enforced in CI
💾 **Session persistence** — SQLite-backed sessions plus a saved-runs hub for past standups, retros, and reports
🛡️ **Guardrails** — Input/output validation, human-in-the-loop review at every stage

## 📖 Full Documentation

Getting started, the full CLI reference, every mode in depth, integrations, architecture,
and deployment guides: **[yeaboi.ai/docs](https://yeaboi.ai/docs/)**

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
