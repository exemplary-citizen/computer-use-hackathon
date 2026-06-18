# hai-agents-demos

Build with H's Computer Use autonomous agents, powered by our own harness and our [own VLM](https://hcompany.ai/holo3.1). When the target system exposes no API, our agents operate it directly through the UI.

The `hai-agents` SDK gives you programmatic access to our agents from a few lines of [Python](https://pypi.org/project/hai-agents/) or [TypeScript](https://npmjs.com/package/hai-agents).

This repo is a tour of three ways to build with the SDK, from a one-line prompt to production integrations:

1. **Discover.** Describe a task in plain language and watch the agent run it.
2. **Scale via the skill.** Let the `/hai-agents` skill scaffold reusable SDK code for you.
3. **Integrate.** Call the agent as an MCP tool from your coding workflow (Claude Code, Hermes Agent, Codex, NemoClaw).

## Quickstart

```bash
git clone <this-repo>
cd hai-agents-demos
uv sync
cp .env.example .env   # add your HAI_API_KEY from https://platform.hcompany.ai/settings/api-keys
```

## 1. Discover

One prompt, one Python file. You write 10 lines; the agent does the work. Now you've seen our CU agent in action.

> Just describe the task in plain language and let the agent run it:

```text
"Searches for "Random Access Memories" by Daft Punk on Amazon, adds it to the shopping cart."
```

https://github.com/user-attachments/assets/aa7473f8-9666-4640-ac34-6255ab67aa6d

→ Runnable code: [`examples/add_to_cart/add_to_cart.py`](examples/add_to_cart/add_to_cart.py) (Python)

## 2. Scale via the skill

The `/hai-agents` skill plugs into your favorite coding agent and writes the SDK code for you, in Python or TypeScript. You get a packaged expert writing your code, and an artifact you can deploy anywhere Python or Node runs, independent of the coding agent that scaffolded it. Iterate on prompts to grow your CU agent library.

> Ask the skill to write the SDK code, and it scaffolds a ready-to-run script:

```text
"/hai-agents:hai-agents Generate TypeScript code that navigates to jacquemus.com, searches for the France Jacquemus × Nike football jersey, checks its availability in sizes S and XXL, and reports the results clearly."
```

https://github.com/user-attachments/assets/d7d82573-22bb-4e2a-a261-bb603bc576f3

→ Generated code: [`examples/product_availability/src/index.ts`](examples/product_availability/src/index.ts) (TypeScript)

For deeper SDK patterns beyond what the skill scaffolds (custom tools, `max_steps` / `max_time_s` budgets, exhaustive sweeps), see [`examples/counterfeit_detection`](examples/counterfeit_detection/README.md).

## 3. Integrate

Call the agent from your coding workflow over MCP. Same SDK, different host wiring.

Example with Claude Code:

```bash
claude                 # opens Claude Code in the repo; the MCP server is auto-registered
```

> *"Use `review_web_ui` to check https://news.ycombinator.com and verify the top story link works and the page has reasonable accessibility."*

https://github.com/user-attachments/assets/f0097089-033b-458b-8e20-2b59cc48b3a0

### Other hosts

- **Hermes Agent** (Nous Research). Consumes the same MCP servers, skills, and CLIs. See [`hermes/`](hermes/) for the one-time setup.
- **Codex** (OpenAI). Same servers in `config.toml` form; the hosted platform server uses `bearer_token_env_var`. Watch Codex's 60 s default tool timeout. See [`codex/`](codex/).
- **NemoClaw** (HAI x NVIDIA x Hermes). A security runtime for regulated, sandboxed deployments, not another MCP host. It builds an NVIDIA OpenShell sandbox and runs Hermes inside it under a network-egress policy. The wiring is the Hermes integration plus an egress policy that lets the agent reach H's hosted server. See [`nemoclaw/`](nemoclaw/).
- **Any other MCP-speaking client.** Same SDK, different wiring.

### Recipes

| Example | What it shows | Interface |
| --- | --- | --- |
| [`qa/mcp`](examples/qa/mcp/README.md) | Autonomous browser agent QAs a remote URL and returns structured `{verdict, summary, findings}` | MCP server (`review_web_ui`, `visual_check`) |
| [`qa/cli`](examples/qa/cli/README.md) | Same QA agent exposed as a shell command, surfaced to coding agents via the `hai-qa-via-cli` skill | CLI (`qa-cli review / visual`) |
| [`extract_anything`](examples/extract_anything/README.md) | Wrap an agent call as a typed Python function (generic `extract(url, task, schema)` or curated `get_*` tools), exposed as both MCP and CLI | MCP server (`extract`) + CLI (`extract-cli`) |

See [`examples/`](examples/README.md) for the full index and shared architecture.

## Configuration

| Env var | Required | Source |
| --- | --- | --- |
| `HAI_API_KEY` | yes | auto-setup via `python3 skills/hai-agents/scripts/h_login.py`, or manually from https://platform.hcompany.ai/settings/api-keys |

The hosted `hai-agents-platform` server in [`.mcp.json`](.mcp.json) (the generic platform MCP, for any H agent) is HTTP, not stdio, so it can't read `.env` like the others: Claude Code expands `${HAI_API_KEY}` in its auth header from the environment. Export the key before launching (`export HAI_API_KEY=hk-...`). It uses the EU endpoint (the demos' default); swap to `agp.hcompany.ai` for US.

## Project layout

```
hai-agents-demos/
├── examples/    qa · extract_anything · counterfeit_detection · add_to_cart · product_availability (+ _shared.py helpers)
├── skills/      /hai-agents · /hai-qa-via-cli skills for your coding agent
├── hermes/      run the same demos in Hermes Agent (config + setup guide)
├── codex/       run the same demos in OpenAI Codex (config.toml + setup guide)
├── nemoclaw/    deploy the Hermes integration inside NVIDIA NemoClaw's sandbox (egress policy + image + guide)
├── .mcp.json    registers the MCP servers with Claude Code
├── .claude-plugin/marketplace.json
└── pyproject.toml · AGENTS.md
```

## Installation alternatives

Quickstart uses `uv sync`. If you prefer pip, the same packages are in `pyproject.toml`:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                   # editable install of this repo's deps
qa-cli review --url ...            # console scripts are on PATH inside the venv
```

The `.mcp.json` registration assumes `uv run` is available. If you go the pip route, edit `.mcp.json` to invoke the console scripts directly (drop the `uv run --env-file .env` prefix and `source .venv/bin/activate` beforehand, or pass the venv's interpreter explicitly).

## Links

- [hai-agents on PyPI](https://pypi.org/project/hai-agents/)
- [hai-agents on Npm](https://npmjs.com/package/hai-agents)
- [H Company Platform](https://platform.hcompany.ai)
- [Model Context Protocol](https://modelcontextprotocol.io)

## License

[MIT](LICENSE.txt) © H Company
