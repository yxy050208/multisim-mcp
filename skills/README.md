# Skills

- `multisim-workflow/SKILL.md`: end-to-end agent workflow for Multisim circuit generation, simulation, data capture, and reporting.

The skill assumes the `multisim` MCP server from `../mcp_server` is registered in the client.

DeepSeek Harness users should install the five packaged, task-specific skills
from their project root instead:

```powershell
multisim-mcp harness-skills --output .dsh/skills
```

The installer refuses to replace existing project skills unless `--force` is
explicit. See [`docs/DEEPSEEK_HARNESS.md`](../docs/DEEPSEEK_HARNESS.md).
