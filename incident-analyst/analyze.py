from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from openai import OpenAI
from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v2.api.logs_api import LogsApi
from datadog_api_client.v2.model.logs_list_request import LogsListRequest
from datadog_api_client.v2.model.logs_list_request_page import LogsListRequestPage
from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter
from datadog_api_client.v2.model.logs_sort import LogsSort
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs.decorators import workflow, task

import os

load_dotenv()

# Agentless mode: sends traces directly to Datadog — no local agent needed.
# Reuses DATADOG_API_KEY already in .env as DD_API_KEY.
os.environ.setdefault("DD_API_KEY", os.environ.get("DATADOG_API_KEY", ""))

LLMObs.enable(
    ml_app="incident-analyst",
    integrations_enabled=True,  # auto-traces all OpenAI calls as LLM spans
    agentless_enabled=True,     # sends directly to Datadog, no agent required
)

TIME_WINDOW_OPTIONS = {
    "1": ("Last 15 minutes", timedelta(minutes=15)),
    "2": ("Last 1 hour", timedelta(hours=1)),
    "3": ("Last 6 hours", timedelta(hours=6)),
    "4": ("Last 24 hours", timedelta(hours=24)),
    "5": ("Last 7 days", timedelta(days=7)),
}

LOG_LEVEL_OPTIONS = {
    "1": ("ERROR only", "status:error"),
    "2": ("WARN and above", "(status:warn OR status:error)"),
    "3": ("All levels", ""),
}


def ask(prompt: str, default: str = "") -> str:
    value = input(prompt).strip()
    return value if value else default


def gather_incident_context() -> dict:
    print("=" * 60)
    print("  INCIDENT LOG ANALYZER — Context Gathering")
    print("=" * 60)
    print("Answer these 5 questions to focus the Datadog query and\n"
          "improve the AI analysis. Press Enter to skip any question.\n")

    # Q1 — Service / application
    print("1. What service or application is affected?")
    print("   (e.g. 'payments-api', 'auth-service', 'frontend')")
    service = ask("   > ")

    # Q2 — Environment
    print("\n2. Which environment are you investigating?")
    print("   (e.g. 'production', 'staging', 'dev'  — default: production)")
    environment = ask("   > ", default="production")

    # Q3 — Time window
    print("\n3. How far back should we look for logs?")
    for key, (label, _) in TIME_WINDOW_OPTIONS.items():
        print(f"   [{key}] {label}")
    time_choice = ask("   > ", default="2")
    if time_choice not in TIME_WINDOW_OPTIONS:
        time_choice = "2"
    time_label, time_delta = TIME_WINDOW_OPTIONS[time_choice]

    # Q4 — Log level
    print("\n4. Which log severity levels should we include?")
    for key, (label, _) in LOG_LEVEL_OPTIONS.items():
        print(f"   [{key}] {label}")
    level_choice = ask("   > ", default="1")
    if level_choice not in LOG_LEVEL_OPTIONS:
        level_choice = "1"
    level_label, level_filter = LOG_LEVEL_OPTIONS[level_choice]

    # Q5 — Keywords / additional context
    print("\n5. Any keywords, error messages, or extra context to search for?")
    print("   (e.g. 'timeout', 'NullPointerException', 'connection refused')")
    keywords = ask("   > ")

    print("\n" + "=" * 60)
    print("  Summary of your selections:")
    print(f"  Service    : {service or '(all services)'}")
    print(f"  Environment: {environment}")
    print(f"  Time window: {time_label}")
    print(f"  Log level  : {level_label}")
    print(f"  Keywords   : {keywords or '(none)'}")
    print("=" * 60 + "\n")

    return {
        "service": service,
        "environment": environment,
        "time_delta": time_delta,
        "time_label": time_label,
        "level_filter": level_filter,
        "level_label": level_label,
        "keywords": keywords,
    }


def build_query(ctx: dict) -> str:
    parts = []
    if ctx["service"]:
        parts.append(f"service:{ctx['service']}")
    if ctx["environment"]:
        parts.append(f"env:{ctx['environment']}")
    if ctx["level_filter"]:
        parts.append(ctx["level_filter"])
    if ctx["keywords"]:
        parts.append(ctx["keywords"])
    return " ".join(parts) if parts else "*"


def fetch_recent_logs(ctx: dict, count: int = 10) -> list[str]:
    import os
    configuration = Configuration()
    configuration.api_key["apiKeyAuth"] = os.environ["DATADOG_API_KEY"]
    configuration.api_key["appKeyAuth"] = os.environ["DATADOG_APP_KEY"]

    query = build_query(ctx)
    now = datetime.now(timezone.utc)
    from_time = (now - ctx["time_delta"]).isoformat()

    print(f"Datadog query : {query}")
    print(f"Time range    : {ctx['time_label']}\n")

    with ApiClient(configuration) as api_client:
        api = LogsApi(api_client)
        request = LogsListRequest(
            filter=LogsQueryFilter(
                query=query,
                _from=from_time,
                to=now.isoformat(),
            ),
            sort=LogsSort("-timestamp"),
            page=LogsListRequestPage(limit=count),
        )
        response = api.list_logs(body=request)
        return [log.attributes.message for log in response.data]


@task
def generate_analysis_prompt(ctx: dict) -> str:
    """Ask the LLM to craft the best possible system prompt for this specific incident context."""
    LLMObs.annotate(
        input_data={"incident_context": ctx},
        metadata={"step": "prompt_generation"},
    )
    client = OpenAI()

    context_summary = (
        f"- Service: {ctx['service'] or 'unknown'}\n"
        f"- Environment: {ctx['environment']}\n"
        f"- Time window: {ctx['time_label']}\n"
        f"- Log severity: {ctx['level_label']}\n"
        f"- Engineer's keywords / notes: {ctx['keywords'] or 'none'}\n"
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1024,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a prompt engineering expert specializing in SRE and incident response. "
                    "Your job is to write system prompts for an AI log analyzer. "
                    "Given an incident context, generate the most effective system prompt possible "
                    "so that the analyzer produces sharp, actionable, and highly relevant root cause analyses. "
                    "The prompt you write must:\n"
                    "1. Set the right persona and urgency tone based on environment and severity.\n"
                    "2. Direct the analyzer to focus on what the engineer actually cares about.\n"
                    "3. Instruct the analyzer to connect observations back to the specific service and keywords.\n"
                    "4. Define a clear output structure (root cause, affected components, severity, next steps, signals to verify).\n"
                    "Return ONLY the system prompt text — no explanations, no preamble."
                ),
            },
            {
                "role": "user",
                "content": f"Generate the system prompt for this incident context:\n{context_summary}",
            },
        ],
    )

    generated_prompt = response.choices[0].message.content
    print("--- Generated Analysis Prompt ---")
    print(generated_prompt)
    print("---------------------------------\n")
    return generated_prompt


@task
def analyze_log(log_message: str, system_prompt: str) -> str:
    LLMObs.annotate(
        input_data={"log_message": log_message},
        metadata={"step": "log_analysis"},
    )
    client = OpenAI()

    message = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1024,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": f"Log message:\n{log_message}",
            },
        ],
    )
    return message.choices[0].message.content


@workflow
def main():
    ctx = gather_incident_context()
    LLMObs.annotate(
        input_data={
            "service": ctx["service"] or "all",
            "environment": ctx["environment"],
            "time_window": ctx["time_label"],
            "log_level": ctx["level_label"],
            "keywords": ctx["keywords"] or "none",
        },
        metadata={"datadog_query": build_query(ctx)},
    )

    print("Generating tailored analysis prompt from your incident context...\n")
    system_prompt = generate_analysis_prompt(ctx)

    print("Fetching logs from Datadog...\n")
    logs = fetch_recent_logs(ctx, count=10)

    if not logs:
        print("No logs found matching your criteria. Try broadening the time window or removing filters.")
        LLMObs.annotate(output_data={"logs_found": 0})
        return

    print(f"Found {len(logs)} log(s). Analyzing...\n")
    analyses = []
    for i, log in enumerate(logs, 1):
        print(f"--- Log {i} ---")
        print(f"Message: {log}")
        analysis = analyze_log(log, system_prompt)
        analyses.append(analysis)
        print(f"Analysis:\n{analysis}\n")

    LLMObs.annotate(output_data={"logs_analyzed": len(logs)})


if __name__ == "__main__":
    try:
        main()
    finally:
        LLMObs.flush()  # garantiza que las trazas se envíen antes de salir
