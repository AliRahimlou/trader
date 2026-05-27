# Trading Bot Gap Report

Date: 2026-05-27

## Scope

The uploaded Atlas archive was treated as an architectural reference only. It contains useful multi-agent trading concepts, JANUS weighting ideas, simulations, prompts, and static artifacts, but it is not a production trading system.

The existing local repo is a working paper-trading/control-plane prototype built around flat Python modules and a React dashboard. It is useful, but it does not match the requested production-shaped app layout or persistence/deployment architecture.

## Component Audit

| Capability | Existing repo status | Atlas ZIP status | Gap | New implementation target |
| --- | --- | --- | --- | --- |
| Dashboard | Present in `dashboard/`, paper-control focused | Not present | Needs simpler non-technical flow and bot decision visibility | `frontend/src` React + TypeScript dashboard |
| Broker adapter | Alpaca paper/live-shaped code exists, paper-only guarded | Not present | Needs broker-agnostic interface and live safety gates | `backend/app/broker` |
| Market-data adapter | Alpaca/yfinance helpers exist | Not present | Needs provider abstraction and freshness checks | `backend/app/market_data` |
| Stock scanner | Present in `scanner_engine.py` | Conceptual only | Needs structured ranking output/API | `backend/app/strategies/ranking.py` |
| Strategy engine | Present for current FVG-style runner | Prompt/prototype only | Needs preset strategies requested by user | `backend/app/strategies` |
| Agent pipeline | ATLAS-inspired review added to current scanner | Conceptual only | Needs explicit macro/sector/CRO/CIO modules | `backend/app/agents` |
| Risk engine | Present in `live_risk.py` | Not present | Needs immutable session risk config and API-visible risk events | `backend/app/risk` |
| Position sizing | Present in current runner | Not present | Needs standalone tested module | `backend/app/risk/position_sizing.py` |
| Execution engine | Present in current runner | Not present | Needs idempotent order intent and broker confirmation model | `backend/app/execution` |
| Order reconciliation | Present in current runner | Not present | Needs reusable broker/db reconciliation module | `backend/app/broker/reconciliation.py` |
| Scheduler/background worker | Current thread supervisor exists | Not present | Needs worker/scheduler modules | `backend/app/workers` |
| Database | Current SQLite store exists | Not present | Needs SQLAlchemy models and PostgreSQL-ready config | `backend/app/db` |
| Tests | Minimal/no formal suite for requested architecture | Not present | Needs pytest coverage for risk, sizing, signals, execution, API | `backend/app/tests` |
| Deployment files | Scripts exist; no compose for requested stack | Not present | Needs Dockerfile and docker-compose | root Docker/deployment files |
| Environment example | Present for old runner | Not present | Needs new app envs | `.env.example` extended |
| Setup docs | Present for old app | Docs only | Needs new app docs and walkthrough | `README.md` extended |

## Safety Findings

- The current app is paper-first and has useful risk controls, but it should not be represented as a profit-guaranteeing autonomous trader.
- Atlas includes simulation patterns that can leak future price paths into recommendations. Those patterns must not be used for decisions or backtesting.
- Live trading must remain disabled unless the user explicitly selects live mode, provides credentials, types the configured confirmation phrase, and passes all risk checks.

## Build Decision

The new implementation is added alongside the existing app. This keeps the current paper-trading demo intact while introducing a production-shaped system with clean module boundaries, typed schemas, PostgreSQL-ready persistence, paper broker execution, deterministic demo market data, a worker loop, tests, Docker deployment, and a user-friendly dashboard.
