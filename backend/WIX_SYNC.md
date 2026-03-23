# Wix — auth, subscriptions, and API keys

**Wix** is the source for sign-in (Members), subscriptions (Pricing Plans), and for provisioning **API keys** that desktop and mobile apps use to call the backend. The backend tracks usage and enforces tier limits; tier is driven by Wix via the sync endpoint below.

## Wix site setup

Create a **Members Account** page (e.g. at `/account-settings` or `/members-area`) so logged-in members can:
- See their API key (from `POST /api/v1/auth/api-key`)
- View subscription and usage (optional)
- Link to upgrade

Desktop app opens this via "Account → Manage account on web" (default: `/account-settings`). Configure `LIVE_TRANSLATE_ACCOUNT_PATH` as an environment variable in your backend deployment (e.g. backend `.env`, Docker/Kubernetes env vars, or platform-specific config) to override the path; the default is `/account-settings`. The desktop app relies on the backend-provided account URL.

## Tier limits (usage caps per month)

| Tier             | Dubbing / STT cap | Wix plan |
|------------------|-------------------|---------|
| free             | 30 minutes        | Free trial |
| starter          | 5 hours           | Monthly Language Unlocked - Hobby Tier |
| pro              | 15 hours          | Monthly Language Unlocked - Pro Tier |
| early_adopters   | Unlimited         | Early Adopters Life Time Access |

## Backend

- **Tier sync:** `POST /api/v1/billing/wix/sync`
  - **Auth:** Header `X-Wix-Sync-Secret: <WIX_SYNC_SECRET>` or `Authorization: Bearer <WIX_SYNC_SECRET>`
  - **Body (JSON):** `email` (required), `plan_id`, `plan_name`, `status` (optional)
  - **Auto-provision:** If user does not exist, creates user with API key. If user exists without API key, assigns one. API key is thus automatically assigned when Wix sync runs (e.g. on account page load).
- **API key (for desktop/mobile):** `POST /api/v1/auth/api-key`
  - **Auth:** Same as above (`X-Wix-Sync-Secret` or `Authorization: Bearer <secret>`)
  - **Body (JSON):** `email` (required)
  - **Response:** `{ "api_key", "user_id", "email", "tier" }` — show `api_key` once on the Wix account page so the user can paste it into the desktop or mobile app.

Tier is mapped from plan id/name (e.g. pro tier, hobby tier, early adopters). Sync creates a user by email if missing, then updates `tier` and `subscription_status`.

## Wix Velo: call sync when member’s plan is known

Example: on the **Members Account** or **Pricing Plans** page, after loading the current member’s orders, call your backend.

```javascript
import { currentMember } from 'wix-members-frontend';
import { orders } from 'wix-pricing-plans.v2';

const BACKEND_URL = 'https://your-backend.example.com';  // or env
const WIX_SYNC_SECRET = '...';  // store in Secrets Manager or env

export async function syncMemberTierToBackend() {
  const member = await currentMember.getMember();
  if (!member || !member.loginEmail) return;

  const list = await orders.memberListOrders();
  const active = list.orders?.find(o => o.status === 'ACTIVE') || list.orders?.[0];
  const planId = active?.planId ?? null;
  const planName = active?.planName ?? null;
  const status = active?.status ?? null;

  const res = await fetch(`${BACKEND_URL}/api/v1/billing/wix/sync`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Wix-Sync-Secret': WIX_SYNC_SECRET,
    },
    body: JSON.stringify({
      email: member.loginEmail,
      plan_id: planId,
      plan_name: planName,
      status,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
```

Call `syncMemberTierToBackend()` when the member lands on the account/dashboard page, or after a plan purchase. Then call **POST /api/v1/auth/api-key** (same secret) with the member’s email to create or retrieve an API key; show it once on the account page so the user can paste it into the desktop or mobile app.

## Flow summary

1. User signs up and subscribes on **Wix** (Members + Pricing Plans).
2. On account page load (or after purchase), Velo calls **POST /billing/wix/sync** with email + plan info. Backend creates or updates the user and sets `tier` and `subscription_status`.
3. Velo calls **POST /api/v1/auth/api-key** with the member’s email; backend returns an API key. Show it on the members-only account page with instructions: “Copy this key into the desktop or mobile app.”
4. **Desktop and mobile apps** use that API key (`Authorization: Bearer <api_key>`) for all backend requests. Backend returns tier and usage from `GET /user/usage`; usage is tracked and capped by tier.
