# Wix — auth, subscriptions, and API keys

**Wix** is the source for sign-in (Members), subscriptions (Pricing Plans), and for provisioning **API keys** that desktop and mobile apps use to call the backend. The backend tracks usage and enforces tier limits; tier is driven by Wix.

## How the app gets the website package

1. **Velo pull (optional):** On `/api-key`, `syncMemberToBackend()` → `POST /api/v1/billing/wix/sync` with the member’s plan.
2. **Backend pull (primary for desktop SSO):** On desktop SSO complete and on `GET /api/v1/user/me` / `GET /api/v1/user/usage` (cached 5 minutes), the backend uses `WIX_API_KEY` + `WIX_ACCOUNT_ID` (optional `WIX_SITE_ID`) to look up the member by email and set `users.tier` from their active Pricing Plan. Cancel/expired/no plan → `free`.

Wix is the **absolute** source of truth on these paths (paid tiers are not kept via `_max_tier` after cancel).

## Wix site setup

Create a **Members Account** page (e.g. at `/account-settings` or `/members-area`) so logged-in members can:
- See their API key (from `POST /api/v1/auth/api-key`)
- View subscription and usage (optional)
- Link to upgrade

Desktop app opens this via "Account → Manage account on web" (default: `/account-settings`). Configure `LIVE_TRANSLATE_ACCOUNT_PATH` as an environment variable in your backend deployment to override the path; the default is `/account-settings`.

## Tier limits (usage caps per month)

| Tier             | Dubbing / STT cap | Wix plan |
|------------------|-------------------|---------|
| free             | 30 minutes        | Free trial |
| starter (Hobby)  | 5 hours           | Monthly Language Unlocked - Hobby Tier |
| pro              | 15 hours          | Monthly Language Unlocked - Pro Tier |
| early_adopters   | Unlimited         | Early Adopters Life Time Access |

Desktop usage meter labels: Free trial / Hobby / Pro / Early Adopters. Dubbing minutes are enforced on `/proxy/synthesize`.

## Backend env (Railway)

| Variable | Purpose |
|----------|---------|
| `LT_SYNC_SECRET` / `WIX_SYNC_SECRET` | Velo → `POST /billing/wix/sync` and `POST /auth/api-key` |
| `WIX_API_KEY` | Account API key (API Keys Manager) for server-side plan refresh |
| `WIX_ACCOUNT_ID` | Wix account id for the API key |
| `WIX_SITE_ID` | Optional; when empty the first site on the account is discovered |

## Backend endpoints

- **Tier sync:** `POST /api/v1/billing/wix/sync`
  - **Auth:** Header `X-Wix-Sync-Secret` or `Authorization: Bearer <secret>`
  - **Body (JSON):** `email` (required), `plan_id`, `plan_name`, `status` (optional)
  - **Auto-provision:** Creates user + API key if missing.
  - Sets tier **absolutely** from the payload (cancel/expired → free).
- **API key:** `POST /api/v1/auth/api-key` (same secret) → `{ "api_key", "user_id", "email", "tier" }`

Tier mapping: `app/services/wix_tier_map.py`.

## Wix Velo: call sync when member’s plan is known

```javascript
import { currentMember } from 'wix-members-frontend';
import { orders } from 'wix-pricing-plans.v2';

const BACKEND_URL = 'https://your-backend.example.com';
const WIX_SYNC_SECRET = '...';  // Secrets Manager

export async function syncMemberTierToBackend() {
  const member = await currentMember.getMember();
  if (!member || !member.loginEmail) return;

  const list = await orders.memberListOrders();
  const active = list.orders?.find(o => o.status === 'ACTIVE') || list.orders?.[0];

  const res = await fetch(`${BACKEND_URL}/api/v1/billing/wix/sync`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Wix-Sync-Secret': WIX_SYNC_SECRET,
    },
    body: JSON.stringify({
      email: member.loginEmail,
      plan_id: active?.planId ?? null,
      plan_name: active?.planName ?? null,
      status: active?.status ?? null,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
```

## Flow summary

1. User signs up and subscribes on **Wix** (Members + Pricing Plans).
2. Velo sync and/or desktop SSO / usage polling refresh `users.tier` from the website package.
3. Desktop/mobile call **GET /user/usage** (limits from `tier_limits`) and hit proxy quotas (including dubbing minutes on TTS).
