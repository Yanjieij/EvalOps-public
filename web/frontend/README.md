# EvalOps Frontend (placeholder)

Week 2 scaffold for the Week 4 dashboard. Runs on Vite + React + TypeScript +
Ant Design, and proxies `/api/*` to the Go control-plane on port 8090.

## Week 4 scope

- **Run list** — filter by benchmark / SUT / judge, sorted by date
- **Run detail** — per-case table with live SSE progress
- **Capability radar** — breakdown of pass rate across the capability tree
- **Case-diff viewer** — side-by-side output comparison between two runs
- **Bad-case workbench** — promote flagged traces into the regression set

For now, this app ships a single informational page so CI has something
to typecheck and build.

## Local dev

```bash
cd web/frontend
npm ci
npm run dev          # http://localhost:5180 with /api -> :8090 proxy
npm run typecheck
npm run build
```
