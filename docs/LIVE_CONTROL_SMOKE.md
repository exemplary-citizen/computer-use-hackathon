# Minimal live desktop-control smoke test

This is the first live gate for the demo. It intentionally does not involve the dashboard, voice, authored bundles,
CRM fixtures, or the execution state machine. Passing it proves only that H's public SDK can see and control this Mac.

The agent controls the real mouse, keyboard, and screen. Close sensitive applications and use a dedicated macOS user
or demo machine when possible. The default task opens TextEdit, types one sentence into an untitled document, and does
not save it.

## 1. Install and inspect

```bash
uv sync
uv run foundry-desktop-smoke doctor
```

The credential-free doctor must pass Python, macOS, SDK, and desktop-driver checks. `HAI_API_KEY` is expected to show
`SKIP`; the command never prints its value.

## 2. Export a rotated key without shell history

Do not reuse a key that has been pasted into chat. Create a replacement key, then enter it through a hidden prompt:

```bash
read -r -s "HAI_API_KEY?H API key: "
printf '\n'
export HAI_API_KEY
uv run foundry-desktop-smoke doctor --require-api-key
```

Do not put the key in the repository or pass it as a command-line argument.

## 3. Grant macOS permissions

The first live attempt may stop while macOS requests Accessibility and Screen Recording access for the terminal or
Python process. Grant both under **System Settings → Privacy & Security**, quit and reopen the terminal, re-export the
key, and rerun the doctor.

## 4. Run the bounded task

Close sensitive applications, leave TextEdit available, and run:

```bash
uv run foundry-desktop-smoke run --confirm-control
```

The command tries the US Computer-Use Agents endpoint first and automatically retries EU only when authentication is
rejected. Use `--region us` or `--region eu` only when you intentionally need to pin one region.

Press `Control-C` to cancel the remote session. A pass requires all of the following:

- TextEdit opens with a new untitled document.
- The document contains exactly `Automation Foundry desktop control works.`
- The document remains unsaved.
- No other application is touched.
- The command ends with `outcome: success` and `desktop-smoke PASSED` within twenty steps and three minutes.

Repeat the run three times before integrating this client with the CRM or execution state machine.
