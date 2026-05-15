# Gemini CLI Preset Integration Design

## Overview
Add built-in support for Gemini CLI to the `cli-agent-runner` supervisor. This enables operators to seamlessly bootstrap an autonomous loop for Gemini CLI with `agent-runner init --preset gemini` without manually configuring the agent command structure.

## Architecture & Components

### 1. Preset Configuration (`agent_runner/presets/gemini.toml`)
A new TOML preset file will be added to the package data.
- **Command:** `["gemini", "--yolo"]`
  - `--yolo` ensures that Gemini CLI does not pause to request confirmation for tools, allowing for truly autonomous background operation.
- **Prompt:** `["-p", "{prompt}"]`
  - Uses the non-interactive prompt mode designed for scriptable environments.
- **Monitor:**
  - `auth_fail_hint`: "Verify your API key or check your authentication status for Gemini CLI."

### 2. Scaffold Updates
- **`agent_runner/cli/init_cmd.py`**: Add `"gemini"` to the `--preset` argument's `choices` array.
- **`agent_runner/scaffold.py`**: Update the docstring to list `gemini` among the available presets.

### 3. Testing
- Ensure the `gemini` preset is covered by existing scaffolding tests (e.g., `tests/integration/test_scaffold_presets.py`). If tests dynamically iterate over available files in `presets/`, it should work automatically. If hardcoded, the test logic must be updated to include `gemini`.

## Non-Goals
- We are not adding complex custom log parsing or Gemini-specific runtime hooks, as the default supervisor mechanisms (stashing, detecting dirty states) are already robust and CLI-agnostic.

## Security & Safety
- Including `--yolo` is standard practice for this supervisor environment, but operators are still responsible for their sandbox/container environment since this allows the agent to execute tools freely.

