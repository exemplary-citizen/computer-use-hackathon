# Run Automation Foundry through NemoClaw/Hermes

NemoClaw is the required agent sandbox for authoring Automation Foundry bundles. Hermes runs inside OpenShell and uses
Gemini 3.5 Flash through OpenRouter for its agent loop. The trusted macOS host remains the only process allowed to drive
HoloDesktop or publish approved skills.

## Prerequisites

- Docker with at least 4 CPUs and 10 GB of memory available;
- Docker Buildx available to the CLI (`brew install docker-buildx` for Homebrew Docker);
- `nemohermes` and OpenShell 0.0.72 or newer;
- an OpenRouter API key;
- an H Company `HAI_API_KEY` when the hosted agent-platform MCP server is enabled.

Provider credentials must be entered through NemoClaw/OpenShell or exported in a private terminal. Do not put them in
this directory or pass them to browser code.

## 1. Onboard the stock managed image

The stock NemoClaw Hermes image includes the managed entrypoint, gateway supervisor, credential boundary, and
streamable-HTTP MCP support. A custom `--from` Dockerfile replaces those managed layers and must not be used merely to
install MCP support.

```bash
export OPENROUTER_API_KEY=replace-in-your-private-shell
export NEMOCLAW_PROVIDER=openrouter
export NEMOCLAW_MODEL=google/gemini-3.5-flash

nemohermes onboard \
  --name hai-hermes \
  --agent hermes \
  --no-gpu \
  --tool-disclosure progressive \
  --yes-i-accept-third-party-software
```

Verify the runtime and restore both local forwards after a reboot or laptop sleep:

```bash
nemohermes hai-hermes doctor
nemohermes hai-hermes recover
curl -sf http://127.0.0.1:8642/health
```

With Colima, start Docker before recovery. If OpenShell resumes the sandbox with `sleep infinity` and reports
`SUPERVISOR_UNAVAILABLE`, rebuild from the recorded managed image; NemoClaw backs up and restores sandbox state:

```bash
colima start --cpu 4 --memory 10 --disk 40
nemohermes hai-hermes rebuild --yes
```

The Hermes OpenAI-compatible API is available at `http://127.0.0.1:8642/v1`. Retrieve its bearer token at runtime with
`nemohermes hai-hermes gateway-token --quiet`; never persist or print it.

## 2. Allow the H Company agent platform

Review and apply the repository-owned policy. It allows only GET and POST requests to the EU and US agent-platform
hosts from the Hermes Python runtime.

```bash
nemohermes hai-hermes policy-add --dry-run --from-file nemoclaw/policies/hai-agent-platform.yaml --yes
nemohermes hai-hermes policy-add --from-file nemoclaw/policies/hai-agent-platform.yaml --yes
nemohermes hai-hermes policy-list
```

## 3. Register the hosted MCP server

Prefer NemoClaw's managed MCP command so credentials stay in the OpenShell provider boundary. Do not edit
`/sandbox/.hermes/.env` or copy an H Company key into the image.

```bash
nemohermes hai-hermes mcp add hai-agent-platform \
  --url https://agp.eu.hcompany.ai/mcp \
  --env HAI_API_KEY
nemohermes hai-hermes mcp status hai-agent-platform
```

Use `https://agp.hcompany.ai/mcp` when the selected H Company region is US.

## 4. Shared authoring workspace

The preferred macOS path uses NemoClaw's authenticated upload transport and needs no kernel extension:

```bash
export FOUNDRY_WORKSPACE_MOUNT="$PWD/data/nemoclaw-workspace"
export FOUNDRY_WORKSPACE_REQUIRE_MOUNT=false
export FOUNDRY_NEMOCLAW_SANDBOX_NAME=hai-hermes
```

`WorkspaceBridge` creates a bounded local job directory and publishes only that job below `/sandbox/workspace` through
`nemohermes upload`. A verified SSHFS mount remains supported when macFUSE is already available:

```bash
brew install --cask macfuse
brew install gromgit/fuse/sshfs-mac
nemohermes hai-hermes share mount \
  /sandbox/workspace \
  "$HOME/.local/share/automation-foundry/nemoclaw-workspace"
```

Do not enable mount mode until `nemohermes hai-hermes share status` reports it mounted.

## Safety checks

- `nemohermes hai-hermes doctor` must report zero failures and zero warnings.
- `curl -sf http://127.0.0.1:8642/health` must return success before authoring is enabled.
- Failure to reach NemoClaw/Hermes is fail-closed; Automation Foundry does not fall back to a host model.
- The sandbox never receives macOS Accessibility permission or direct HoloDesktop control.
