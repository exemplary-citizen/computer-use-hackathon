# Deploy the demos inside NemoClaw

[NemoClaw](https://github.com/NVIDIA/NemoClaw) (NVIDIA) isn't another MCP host like Claude Code,
Hermes, or Codex. It's a security runtime: it builds an **NVIDIA OpenShell sandbox** and runs an
agent harness inside it under an explicit network-egress policy. Its `nemohermes` variant runs
**Hermes**, so this is the [`hermes/`](../hermes/) integration deployed inside that sandbox. The
NemoClaw-specific work is the egress policy that lets the sandboxed agent reach our hosted server.

The hosted `hai-agent-platform` server fits best here: it's HTTP, so the sandbox only needs egress
plus the key. The stdio demo servers would need the repo and `uv` baked into the image, which fights
the sandbox model.

## Prerequisites

- Docker (NemoClaw builds a ~2.4 GB image and runs OpenShell + k3s).
- An inference-provider credential to run the Hermes model (NVIDIA, Anthropic, Nous, or local
  Ollama/vLLM). This is separate from `HAI_API_KEY`.
- This repo's `HAI_API_KEY` (the `hk-` key the server authenticates with).

## 1. Onboard from the custom image

The stock Hermes sandbox ships an `mcp` package without the streamable-HTTP client, so the hosted
server can't connect. [`image/Dockerfile`](image/Dockerfile) adds it at build time. Onboard from it:

```bash
export NEMOCLAW_AGENT=hermes
nemohermes onboard --from nemoclaw/image/Dockerfile --name hai-hermes
```

The wizard asks for an inference provider, model, and credential. The build runs a check that fails
if HTTP-MCP support didn't land, so a green build means the transport is in place.

## 2. Allow egress to H Company

The baseline Hermes policy permits Nous, PyPI, and NVIDIA endpoints, but not `agp.hcompany.ai`, so
the server is blocked until you apply [`policies/hai-agent-platform.yaml`](policies/hai-agent-platform.yaml):

```bash
nemohermes hai-hermes policy-add --from-file nemoclaw/policies/hai-agent-platform.yaml
nemohermes hai-hermes policy-list      # confirm agp.eu.hcompany.ai is listed
```

## 3. Register the server

Hermes config lives at `/sandbox/.hermes/config.yaml` inside the sandbox (the host `~/.hermes` does
not apply). Connect, then add the block with your real EU `hk-` key:

```bash
nemohermes hai-hermes connect
```
```yaml
mcp_servers:
  hai-agent-platform:
    url: https://agp.eu.hcompany.ai/mcp
    headers:
      Authorization: "Bearer hk-...your-key..."
    timeout: 420
```

## 4. Verify

Inside the sandbox, the one-shot check (expect the six tools `run_agent`, `list_agents`,
`wait_for_session`, `send_message`, `cancel_session`, `share_session`):

```bash
hermes mcp test hai-agent-platform
```

Then drive it through the agent: start the Hermes chat (the command `connect` prints) and ask it to
"list the available H agents". To prove the call leaves the sandbox, run `openshell term` on the host
while it runs; you'll see the request to `agp.eu.hcompany.ai:443` from the Hermes runtime. The browser
dashboard (port 18789) is forwarded only while a `connect` session is open.

## Notes

- **The egress policy is the integration.** Without it, OpenShell blocks the call to agp. The
  policy's `binaries` entry must match the runtime that makes the call, which in the NVIDIA Hermes
  image is `/opt/hermes/.venv/bin/python3`. If a call is denied, `openshell term` names the binary
  and host so you can adjust.
- **Why a custom image.** The stock sandbox's `mcp` lacks `mcp.client.streamable_http`, and a runtime
  install won't fix it: the venv is uv-managed (no `pip`), the egress policy blocks `uv` from PyPI,
  and `uv` doesn't trust the image's patched CA bundle. Build time avoids all three, so the transport
  goes in via [`image/Dockerfile`](image/Dockerfile).
- For full blueprint examples (model, agent, and policy together), see
  [NVIDIA/nemoclaw-community](https://github.com/NVIDIA/nemoclaw-community).
