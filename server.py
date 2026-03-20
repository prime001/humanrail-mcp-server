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
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

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


@mcp.tool()
def create_task(
    task_type: str,
    payload: dict[str, Any],
    output_schema: dict[str, Any],
    idempotency_key: str | None = None,
    risk_tier: str = "medium",
    sla_seconds: int = 600,
    payout_currency: str = "USD",
    payout_max_amount: float = 0.50,
    callback_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    """Create a task for human review and judgment.

    Use this when the AI agent encounters something that needs human decision-making:
    - Content moderation (is this appropriate?)
    - Refund eligibility (should we approve this refund?)
    - Data verification (is this information accurate?)
    - Subjective assessment (rate this quality 1-10)
    - Edge cases the AI isn't confident about

    The task is routed to a vetted human worker who returns a structured result
    matching your output_schema. Results are verified before delivery.

    Args:
        task_type: Category of work (e.g., "content_moderation", "refund_eligibility",
                   "data_verification", "quality_assessment").
        payload: Context for the human worker. Include everything they need to make
                 a decision (order details, content to review, customer history, etc.).
        output_schema: JSON Schema defining what the worker must return.
                       Example: {"type": "object", "required": ["eligible"],
                                 "properties": {"eligible": {"type": "boolean"}}}
        idempotency_key: Prevents duplicate tasks on retry. Auto-generated if not provided.
        risk_tier: "low", "medium", "high", or "critical". Higher tiers get more
                   experienced workers and deeper verification. Default: "medium".
        sla_seconds: Deadline in seconds (60-86400). Default: 600 (10 minutes).
        payout_currency: "USD", "BTC", or "SATS". Default: "USD".
        payout_max_amount: Maximum worker payout. Default: 0.50 USD.
        callback_url: Optional HTTPS URL to receive webhook events for this task.
        metadata: Optional tracking metadata (not visible to workers).

    Returns:
        The created task with id, status, and all fields.
    """
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


@mcp.tool()
def get_task(task_id: str) -> dict:
    """Get the current status and result of a task.

    Use this to check if a human worker has completed their review.
    When status is "verified", the "output" field contains the worker's
    verified response matching your output_schema.

    Task statuses:
    - posted: Task created, waiting for assignment
    - assigned: Worker picked up the task
    - submitted: Worker submitted result, awaiting verification
    - verified: Result verified — check the "output" field for the answer
    - failed: Task failed (see failureReason)
    - cancelled: Task was cancelled
    - expired: SLA deadline passed without completion

    Args:
        task_id: The task UUID returned from create_task.

    Returns:
        Full task object including status, output (if complete), and timestamps.
    """
    return _request("GET", f"/tasks/{task_id}")


@mcp.tool()
def wait_for_task(
    task_id: str,
    poll_interval_seconds: float = 3.0,
    timeout_seconds: float = 300.0,
) -> dict:
    """Wait for a task to complete by polling until it reaches a terminal state.

    This is a convenience method that polls get_task repeatedly until the task
    is verified, failed, cancelled, or expired. Use this when you need the
    human's answer before proceeding.

    Args:
        task_id: The task UUID to wait for.
        poll_interval_seconds: Seconds between status checks. Default: 3.
        timeout_seconds: Maximum time to wait. Default: 300 (5 minutes).

    Returns:
        The task in its terminal state with the worker's output.
    """
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


@mcp.tool()
def cancel_task(task_id: str) -> dict:
    """Cancel a task that hasn't been completed yet.

    Only works if the task is still in a non-terminal state (posted, assigned,
    submitted). Cannot cancel tasks that are already verified, failed, or expired.

    Args:
        task_id: The task UUID to cancel.

    Returns:
        Cancellation confirmation with the updated status and timestamp.
    """
    return _request("POST", f"/tasks/{task_id}/cancel")


@mcp.tool()
def list_tasks(
    status: str | None = None,
    task_type: str | None = None,
    limit: int = 20,
    created_after: str | None = None,
    created_before: str | None = None,
) -> dict:
    """List tasks with optional filters.

    Useful for checking recent task activity, finding completed tasks,
    or monitoring pending work.

    Args:
        status: Filter by status ("posted", "assigned", "submitted",
                "verified", "failed", "cancelled", "expired").
        task_type: Filter by task type (e.g., "content_moderation").
        limit: Max results to return (1-100). Default: 20.
        created_after: ISO 8601 timestamp — only tasks created after this time.
        created_before: ISO 8601 timestamp — only tasks created before this time.

    Returns:
        Paginated list of tasks with has_more and next_cursor for pagination.
    """
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


@mcp.tool()
def get_usage() -> dict:
    """Get usage statistics and billing summary for your organization.

    Returns task counts, API call volumes, and average latency metrics.
    Useful for monitoring your HumanRail usage and costs.

    Returns:
        Usage stats including tasks_created, tasks_completed, tasks_failed,
        api_calls_total, and avg_latency_ms.
    """
    return _request("GET", "/org/usage")


@mcp.tool()
def health_check() -> dict:
    """Check if the HumanRail API is healthy and reachable.

    Returns:
        Health status of the API.
    """
    url = f"{BASE_URL.rsplit('/v1', 1)[0]}/healthz"
    with httpx.Client(timeout=10.0) as client:
        response = client.get(url)
    return {"status": "healthy" if response.is_success else "unhealthy", "code": response.status_code}


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
