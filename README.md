# HumanRail MCP Server

Route tasks requiring human judgment to a vetted worker pool — directly from any AI agent.

When your AI agent hits something it can't handle — content moderation, refund decisions, subjective quality assessments, data verification — HumanRail routes it to a human worker, verifies the result, pays the worker via Lightning Network, and returns structured output.

**Think "Stripe for human judgment."**

## Quick Start

### Install

```bash
pip install humanrail-mcp-server
```

Or run directly:

```bash
uvx humanrail-mcp-server
```

### Configure

Add to your Claude Code config (`~/.claude.json`):

```json
{
  "mcpServers": {
    "humanrail": {
      "command": "uvx",
      "args": ["humanrail-mcp-server"],
      "env": {
        "HUMANRAIL_API_KEY": "ek_live_your_key_here"
      }
    }
  }
}
```

Or for Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "humanrail": {
      "command": "uvx",
      "args": ["humanrail-mcp-server"],
      "env": {
        "HUMANRAIL_API_KEY": "ek_live_your_key_here"
      }
    }
  }
}
```

### Get an API Key

Sign up at [humanrail.dev](https://humanrail.dev) to get your API key.

## Available Tools

| Tool | Description |
|------|-------------|
| `create_task` | Route a task to a human worker for review/judgment |
| `get_task` | Check the status and result of a task |
| `wait_for_task` | Poll until a task completes (blocking) |
| `cancel_task` | Cancel a pending task |
| `list_tasks` | List tasks with filters (status, type, date range) |
| `get_usage` | View usage stats and billing summary |
| `health_check` | Check if the HumanRail API is reachable |

## Example Usage

Once connected, Claude can use HumanRail naturally:

> **User:** "Review this customer's refund request — order #12345, they say the item arrived damaged."
>
> **Claude:** I'll route this to a human reviewer for a refund eligibility decision.
> *(calls create_task with task_type="refund_eligibility")*
>
> The human reviewer has verified: **Refund approved.** The item shows visible damage in the photos and the customer's account is in good standing.

### Task Types

You can create any task type. Common examples:

- `content_moderation` — Is this content appropriate?
- `refund_eligibility` — Should we approve this refund?
- `data_verification` — Is this information accurate?
- `quality_assessment` — Rate this output 1-10
- `document_review` — Extract/verify information from a document
- `sentiment_analysis` — What's the tone/intent of this message?

### Output Schema

Define exactly what you need back using JSON Schema:

```python
# Boolean decision
{"type": "object", "required": ["approved"], "properties": {"approved": {"type": "boolean"}}}

# Rating with explanation
{"type": "object", "required": ["score", "reason"],
 "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 10},
                "reason": {"type": "string"}}}
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HUMANRAIL_API_KEY` | Yes | — | Your API key (`ek_live_...` or `ek_test_...`) |
| `HUMANRAIL_BASE_URL` | No | `https://api.humanrail.dev/v1` | API base URL |

## How It Works

```
AI Agent → create_task() → HumanRail API → Worker Pool
                                              ↓
AI Agent ← get_task()  ← Verified Result ← Verification Pipeline
```

1. **Create:** Agent sends task with context and output schema
2. **Route:** HumanRail's routing engine assigns the best-matched worker
3. **Execute:** Worker reviews the context and submits their judgment
4. **Verify:** 6-stage verification pipeline validates the result
5. **Pay:** Worker is paid via Lightning Network (instant)
6. **Return:** Verified result is available via `get_task` or `wait_for_task`

## Pricing

Pay per task. No subscriptions. Workers are paid from your task budget.

- **Low risk:** $0.10–$0.50 per task
- **Medium risk:** $0.25–$1.00 per task
- **High/Critical:** $1.00–$5.00 per task

Pricing depends on task complexity, SLA requirements, and risk tier.

## License

MIT — see [LICENSE](LICENSE).
