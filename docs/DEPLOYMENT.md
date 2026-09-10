# Deploying the demo

The whole thing is one container: the three demo enterprise systems, the gateway, the
console API and the built console. That is right for a demo and wrong for production,
where the enterprise systems would be someone else's software on someone else's network.
Splitting them is a change to `backend/mcp_upstreams.yaml`, not to any code.

```bash
docker compose up --build     # then open http://127.0.0.1:8000
```

## What the container needs

| Setting | Required | What it does |
|---|---|---|
| `PORT` | no (default 8000) | Most hosts set this for you. |
| `GEMINI_API_KEY` or `GROQ_API_KEY` | no | Enables live model runs. Without one, the console offers replay, which needs no key and shows the same governed workflow. |
| `AEGIS_LIVE_PASSCODE` | recommended when public | Live runs then require the passcode in an `X-Aegis-Passcode` header. Replay stays open. Without this, a stranger can exhaust your free-tier quota in a few clicks. |
| A writable `/data` volume | no | Keeps investigations and the audit trail across restarts. Without it a redeploy resets the demo, which is often what you want. |

## Choosing a host

The backend needs a long-lived process: the agent loop, the SSE stream, the MCP session
manager and four processes in one container. That rules out short-lived serverless
functions, so **the backend does not belong on Vercel**. Vercel is a good home for the
console alone if you split the frontend out, pointing it at the API with a rewrite.

Hosts that suit a single always-on container, in rough order of how little work they are:

- **Render** and **Railway** — build straight from the Dockerfile, set the environment
  variables, done.
- **Fly.io** — `fly launch` reads the Dockerfile; a volume gives you `/data`.
- **Hugging Face Spaces** (Docker SDK) — works, and is free, but expects port 7860, so
  set `PORT=7860`.

Free tiers, their limits and their sleep behaviour change often, so check current terms
before committing. Most free tiers sleep after inactivity and take 30 to 60 seconds to
wake, which matters in an interview: **open the demo a minute before you present**, or
keep a local `make demo` as the fallback.

## Before a public deployment

1. Set `AEGIS_LIVE_PASSCODE`. Visitors get replay; you get live runs.
2. Decide whether you want persistence. Without a volume every redeploy is a clean demo.
3. Check the console footer still says the data is synthetic. It should; do not remove it.
4. If you have a provider key, record a real trace first
   (`make record-trace INC=INC-1042`) so replay shows a genuine model run rather than
   the authored one.

## What is deliberately not here

No TLS termination, no authentication, no rate limiting, no secret manager. The persona
picker in the console is a demo identity, not a login. This is a portfolio prototype and
the deployment guidance should not pretend otherwise. Real single sign-on, real secret
storage and a real network boundary between Aegis and the enterprise systems are the
first three things a production version would need.
