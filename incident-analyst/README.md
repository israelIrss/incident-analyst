# Incident Analyst

An interactive CLI tool that helps SREs and engineers investigate incidents by fetching logs from Datadog and analyzing them with GPT-4o.

## How it works

1. The tool asks five questions to narrow the scope of the investigation: affected service, environment, time window, log severity level, and optional keywords.
2. GPT-4o generates a tailored system prompt based on the incident context.
3. The tool fetches the most recent matching logs from Datadog.
4. Each log is analyzed by GPT-4o, which returns a structured root cause analysis with affected components, severity assessment, and recommended next steps.
5. All LLM calls are traced automatically via Datadog LLM Observability (LLMObs), giving you full visibility into prompt/response pairs and token usage.

## Technologies

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| AI model | OpenAI GPT-4o |
| Log source | Datadog Logs API (v2) |
| Observability | Datadog LLMObs via `ddtrace` |
| Containerization | Docker + Docker Compose |

## Prerequisites

- Docker and Docker Compose installed
- A Datadog account with Logs enabled
- An OpenAI API key

## Configuration

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `DATADOG_API_KEY` | Datadog API key (also used for LLMObs agentless export) |
| `DATADOG_APP_KEY` | Datadog Application key (required to query logs) |
| `DD_SITE` | Datadog intake site (default: `datadoghq.com`) |
| `DD_ENV` | Environment tag applied to traces (default: `production`) |

## Running with Docker

Build and start the tool (along with the Datadog agent sidecar):

```bash
docker compose up --build
```

The `incident-analyst` container runs interactively — Docker Compose keeps `stdin` and a TTY open so you can answer the prompts.

To run the analyzer alone without the Datadog agent (agentless mode is enabled by default in the code):

```bash
docker compose run --rm incident-analyst
```

## Running locally (without Docker)

```bash
pip install -r requirements.txt
python analyze.py
```
