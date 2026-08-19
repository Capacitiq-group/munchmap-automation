# Munchmap Automation

Python services that sit alongside the Lovable frontend and PocketBase backend. This service owns everything that shouldn't live in React: QR code generation, link shortening, and the twice-weekly automated payout run against Paystack.

Per the Munchmap V1 Build Brief: **Lovable never generates QR codes, never shortens links, and never triggers a payout.** It calls this service's API for all three.

## What's in here

| Module | Owns |
|---|---|
| `app/services/link_service.py` | Short code generation, short-link creation, redirect resolution, click tracking |
| `app/services/qr_service.py` | QR image generation (restaurant ordering QR, order collection QR) |
| `app/services/payout_runner.py` | The Monday/Wednesday automated payout run against Paystack Transfers API |
| `app/services/email_service.py` | All transactional email via Resend — password resets, order confirmations, payout notifications, verification status, disputes |
| `app/paystack_client.py` | Thin wrapper around the Paystack API (transfers, recipients, webhook signature verification) |
| `app/pocketbase_client.py` | Thin wrapper around the PocketBase REST API |
| `app/main.py` | FastAPI app exposing the endpoints Lovable calls, plus the Paystack webhook receiver |
| `scripts/run_payout_job.py` | Standalone entrypoint for the scheduled payout run — this is what cron/GitHub Actions calls |

## Why the logic is split the way it is

Every service is split into **pure logic** (no network calls, fully unit-testable, zero external dependencies) and a thin **I/O layer** (the actual HTTP calls to Paystack/PocketBase). This matters for the payout job specifically: the cycle-date math, idempotency-key construction, and eligibility filtering are the parts most likely to have a subtle bug, and they're the parts checked by the test suite without needing real credentials or a live network call.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# fill in .env with real values
```

## Running the tests

```bash
pytest -v
```

Expected: all tests pass. The pure-logic tests (`test_payout_logic.py`, `test_link_logic.py`, `test_shortcode.py`) need no credentials at all — they were run directly in development with a plain stdlib test runner and pass with 0 failures across 44 assertions. The mocked-I/O test (`test_payout_runner_io.py`) uses `respx` to fake the HTTP layer, so it also needs no real credentials — it checks that this code calls Paystack/PocketBase correctly, not that Paystack/PocketBase actually respond correctly. This one needs `pip install -r requirements.txt` to run, since it depends on `respx`/`httpx`/`fastapi` being installed.

## Running the API locally

```bash
uvicorn app.main:app --reload --port 8000
```

Then `http://localhost:8000/docs` gives you the interactive API docs FastAPI generates automatically.

## Running the payout job manually (for testing against Paystack's sandbox)

```bash
python scripts/run_payout_job.py
```

This is exactly what the Monday 17:00 / Wednesday 17:00 scheduled run calls. Point `.env` at Paystack's test keys before running this against anything other than production.

## Deployment — the scheduled payout job

This service does not run its own internal scheduler by default (no APScheduler background loop). That's deliberate — a scheduler tied to the process lifetime is fragile if the process restarts or the container recycles, which is common on typical hosting. Instead, `scripts/run_payout_job.py` is meant to be triggered externally, by whichever of these fits your deployment:

- A **cron job** on the server Codify deploys to (`0 17 * * 1,3` — 17:00 on Monday and Wednesday)
- A **GitHub Actions scheduled workflow** (`schedule: cron: '0 15 * * 1,3'` in UTC, since South Africa is UTC+2) that SSHes in or calls a deploy hook
- Any other external scheduler your hosting provides

The job is idempotent (see `payout_logic.py` and `payout_runner.py`) — running it twice in the same cycle will not double-pay a restaurant, so an accidental duplicate trigger is safe.

## Environment variables (`.env`)

See `.env.example` for the full list. The important ones:

- `POCKETBASE_URL`, `POCKETBASE_ADMIN_EMAIL`, `POCKETBASE_ADMIN_PASSWORD` — service-level PocketBase auth, not a per-user token
- `PAYSTACK_SECRET_KEY` — starts with `sk_test_` in sandbox, `sk_live_` in production
- `PAYSTACK_WEBHOOK_SECRET` — used to verify inbound webhook signatures (Paystack signs webhooks with your secret key itself; see `paystack_client.py` for the exact HMAC check)
- `SHORT_LINK_BASE_URL` — e.g. `https://mm.synkra.co.za` — the domain short links resolve from. Keep this short since it's printed on stickers and menus.
- `RESEND_API_KEY` — all transactional email (password resets, order confirmations, payout notifications, verification status, disputes) goes through Resend. No other email provider anywhere in this codebase.

## PocketBase collections this service expects to exist

Create these in the PocketBase admin UI (or via the PocketBase migration files, if the main build already manages migrations — check with whoever owns the PocketBase schema before creating these by hand, so you don't end up with two sources of truth for the schema).

### `restaurants` (likely already exists from the main build — these are the fields this service specifically reads/writes)

| Field | Type | Notes |
|---|---|---|
| `paystack_subaccount_code` | text | Set when the restaurant completes Paystack onboarding |
| `paystack_recipient_code` | text | Set the first time a transfer recipient is created for this restaurant; cached so we don't recreate it every payout run |
| `wallet_available_balance_cents` | number | Store money in cents/integers, never floats |
| `wallet_pending_balance_cents` | number | |
| `payout_status` | select | `active`, `held` |
| `short_link_code` | text | The restaurant's permanent ordering short code, generated once at listing publish time |

### `payouts` (new collection — this service owns it)

| Field | Type | Notes |
|---|---|---|
| `restaurant` | relation → restaurants | |
| `cycle_date` | date | The target payout day (Tuesday or Friday), not the trigger day |
| `amount_cents` | number | |
| `status` | select | `pending`, `processing`, `completed`, `failed` |
| `idempotency_key` | text, unique | `{restaurant_id}:{cycle_date}` — this is what makes re-running the job safe |
| `paystack_transfer_code` | text | Set once Paystack accepts the transfer request |
| `failure_reason` | text | Set on failure, cleared on retry in the next cycle |
| `initiated_at` | date | |
| `completed_at` | date | |

### `short_links` (new collection — this service owns it)

| Field | Type | Notes |
|---|---|---|
| `code` | text, unique | The short code itself, e.g. `a7k2m9` |
| `target_url` | url | |
| `link_type` | select | `restaurant_ordering`, `order_collection` |
| `restaurant` | relation → restaurants, optional | |
| `order` | relation → orders, optional | |
| `click_count` | number | Incremented on each resolve |
| `created_at` | date | |

### `qr_codes` (new collection — this service owns it)

| Field | Type | Notes |
|---|---|---|
| `short_link` | relation → short_links | |
| `image` | file (PNG) | The rendered QR code, stored so it isn't regenerated on every request |
| `created_at` | date | |

## What Lovable calls, concretely

- `POST /links` — create a short link, get back `{code, short_url}`
- `GET /r/{code}` — public redirect endpoint; this is what the physical stickers/short links actually point to
- `POST /qr` — generate a QR code for a given short link, get back the image
- `GET /payouts/status/{restaurant_id}` — for the restaurant dashboard's pending/available/next-payout display (read-only, never triggers anything)

## What's deliberately NOT in this service yet

- **SEO assistant (Ollama-backed).** On hold per instruction — waiting on confirmation of which model is actually installed on the self-hosted Ollama instance before building this. A stub module (`app/services/seo_service.py`) exists with the intended function signature and a `NotImplementedError`, so the integration point is reserved without guessing at model behavior.
- **Meet to Connect automation.** Correctly belongs in Python long-term (trigger detection on first-order events, templated message dispatch), but it's V1.5/V2+ scope per the Build Brief — not built now.
