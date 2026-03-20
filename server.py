"""
HumanRail MCP Server — Route tasks to human workers from any AI agent.

When an AI agent hits a task it can't handle — content moderation, refund decisions,
subjective judgment calls — HumanRail routes it to a vetted human worker pool,
verifies the result, pays the worker, and returns structured output.

Think "Stripe for human judgment."

Environment variables:
    HUMANRAIL_API_KEY: Your HumanRail API key (ek_live_... or ek_test_...)
    HUMANRAIL_BASE_URL: API base URL (default: https://api.humanrail.dev/v1)
"""

import hashlib
import json
import os
import time
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# ── Config ───────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("HUMANRAIL_API_KEY", "")
BASE_URL = os.environ.get("HUMANRAIL_BASE_URL", "https://api.humanrail.dev/v1").rstrip("/")

mcp = FastMCP(
    "humanrail",
    instructions=(
        "HumanRail — Route tasks requiring human judgment to a vetted worker pool. "
        "Use this when an AI agent needs a human to make a subjective decision, "
        "verify information, moderate content, or handle anything requiring human expertise. "
        "Workers are paid via Lightning Network. Results are verified before delivery."
    ),
    host="0.0.0.0",
    port=8100,
)

# ── HTTP Client ──────────────────────────────────────────────────────────────


def _headers() -> dict[str, str]:
    if not API_KEY:
        raise ValueError(
            "HUMANRAIL_API_KEY environment variable is required. "
            "Get your key at https://humanrail.dev"
        )
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "humanrail-mcp-server/1.0.0",
    }


def _request(method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    """Make an authenticated request to the HumanRail API."""
    url = f"{BASE_URL}{path}"
    clean_query = {k: str(v) for k, v in (query or {}).items() if v is not None}
    with httpx.Client(timeout=30.0) as client:
        response = client.request(
            method,
            url,
            json=body,
            params=clean_query or None,
            headers=_headers(),
        )
    if not response.is_success:
        try:
            error = response.json()
        except Exception:
            error = {"message": response.text}
        raise Exception(
            f"HumanRail API error ({response.status_code}): "
            f"{error.get('error', {}).get('message', error.get('message', 'Unknown error'))}"
        )
    return response.json()


def _idempotency_key(namespace: str, *parts: str) -> str:
    """Generate a deterministic idempotency key."""
    input_str = ":".join(parts)
    hash_hex = hashlib.sha256(input_str.encode()).hexdigest()[:32]
    return f"{namespace}:{hash_hex}"


# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool(annotations={"title": "Create Human Task", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def create_task(
    task_type: Annotated[str, Field(description="Category of work, e.g. 'content_moderation', 'refund_eligibility', 'data_verification', 'quality_assessment'")],
    payload: Annotated[dict[str, Any], Field(description="Context for the human worker — include everything they need to make a decision (order details, content to review, customer history, etc.)")],
    output_schema: Annotated[dict[str, Any], Field(description="JSON Schema defining what the worker must return, e.g. {\"type\": \"object\", \"required\": [\"eligible\"], \"properties\": {\"eligible\": {\"type\": \"boolean\"}}}")],
    idempotency_key: Annotated[str | None, Field(description="Prevents duplicate tasks on retry. Auto-generated if not provided.")] = None,
    risk_tier: Annotated[str, Field(description="Worker pool and verification depth: 'low', 'medium', 'high', or 'critical'. Default: 'medium'.")] = "medium",
    sla_seconds: Annotated[int, Field(description="Deadline in seconds (60-86400). Default: 600 (10 minutes).")] = 600,
    payout_currency: Annotated[str, Field(description="Payment currency: 'USD', 'BTC', or 'SATS'. Default: 'USD'.")] = "USD",
    payout_max_amount: Annotated[float, Field(description="Maximum worker payout amount. Default: 0.50 USD.")] = 0.50,
    callback_url: Annotated[str | None, Field(description="Optional HTTPS URL to receive webhook events for this task.")] = None,
    metadata: Annotated[dict[str, Any] | None, Field(description="Optional tracking metadata (not visible to workers).")] = None,
) -> dict:
    """Create a task for human review and judgment. Use when the AI agent needs a human to make a subjective decision — content moderation, refund eligibility, data verification, quality assessment, or any edge case the AI isn't confident about. Returns a task object with an ID to track progress."""
    if not idempotency_key:
        idempotency_key = _idempotency_key(
            "mcp", task_type, json.dumps(payload, sort_keys=True), str(time.time())
        )

    body = {
        "idempotencyKey": idempotency_key,
        "taskType": task_type,
        "riskTier": risk_tier,
        "slaSeconds": sla_seconds,
        "payload": payload,
        "outputSchema": output_schema,
        "payout": {
            "currency": payout_currency,
            "maxAmount": payout_max_amount,
        },
    }
    if callback_url:
        body["callbackUrl"] = callback_url
    if metadata:
        body["metadata"] = metadata

    return _request("POST", "/tasks", body=body)


@mcp.tool(annotations={"title": "Get Task Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def get_task(
    task_id: Annotated[str, Field(description="The task UUID returned from create_task.")],
) -> dict:
    """Get the current status and result of a task. When status is 'verified', the 'output' field contains the worker's verified response. Statuses: posted, assigned, submitted, verified, failed, cancelled, expired."""
    return _request("GET", f"/tasks/{task_id}")


@mcp.tool(annotations={"title": "Wait for Task Completion", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def wait_for_task(
    task_id: Annotated[str, Field(description="The task UUID to wait for.")],
    poll_interval_seconds: Annotated[float, Field(description="Seconds between status checks. Default: 3.")] = 3.0,
    timeout_seconds: Annotated[float, Field(description="Maximum time to wait in seconds. Default: 300 (5 minutes).")] = 300.0,
) -> dict:
    """Wait for a task to complete by polling until it reaches a terminal state (verified, failed, cancelled, or expired). Use this when you need the human's answer before proceeding."""
    terminal = {"verified", "failed", "cancelled", "expired"}
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        task = _request("GET", f"/tasks/{task_id}")
        if task.get("status") in terminal:
            return task
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval_seconds, remaining))

    return {
        "error": f"Task {task_id} did not complete within {timeout_seconds}s",
        "last_status": task.get("status"),
        "task": task,
    }


@mcp.tool(annotations={"title": "Cancel Task", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
def cancel_task(
    task_id: Annotated[str, Field(description="The task UUID to cancel.")],
) -> dict:
    """Cancel a task that hasn't been completed yet. Only works for non-terminal states (posted, assigned, submitted). Cannot cancel verified, failed, or expired tasks."""
    return _request("POST", f"/tasks/{task_id}/cancel")


@mcp.tool(annotations={"title": "List Tasks", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def list_tasks(
    status: Annotated[str | None, Field(description="Filter by status: 'posted', 'assigned', 'submitted', 'verified', 'failed', 'cancelled', 'expired'.")] = None,
    task_type: Annotated[str | None, Field(description="Filter by task type, e.g. 'content_moderation'.")] = None,
    limit: Annotated[int, Field(description="Max results to return (1-100). Default: 20.")] = 20,
    created_after: Annotated[str | None, Field(description="ISO 8601 timestamp — only tasks created after this time.")] = None,
    created_before: Annotated[str | None, Field(description="ISO 8601 timestamp — only tasks created before this time.")] = None,
) -> dict:
    """List tasks with optional filters. Useful for checking recent activity, finding completed tasks, or monitoring pending work. Returns paginated results."""
    query: dict[str, Any] = {"limit": limit}
    if status:
        query["status"] = status
    if task_type:
        query["task_type"] = task_type
    if created_after:
        query["created_after"] = created_after
    if created_before:
        query["created_before"] = created_before

    return _request("GET", "/tasks", query=query)


@mcp.tool(annotations={"title": "Get Usage Stats", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
def get_usage() -> dict:
    """Get usage statistics and billing summary for your organization. Returns task counts, API call volumes, and average latency metrics."""
    return _request("GET", "/org/usage")


@mcp.tool(annotations={"title": "Health Check", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
def health_check() -> dict:
    """Check if the HumanRail API is healthy and reachable. Returns status and HTTP code."""
    url = f"{BASE_URL.rsplit('/v1', 1)[0]}/healthz"
    with httpx.Client(timeout=10.0) as client:
        response = client.get(url)
    return {"status": "healthy" if response.is_success else "unhealthy", "code": response.status_code}


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
