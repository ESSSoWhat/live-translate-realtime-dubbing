# Backend API base URL (Wix + apps)

**Canonical production host:** `https://api.livetranslate.net`  
(No trailing slash in constants; callers use `/api/v1/...` paths.)

## Files that must stay aligned

| Location | How it is set |
|----------|----------------|
| `wix-app/velo-pages/api-key.web.js` | `BACKEND_URL` constant |
| `wix-app/live-translate-jsw/src/backend/sync.web.ts` | `import.meta.env.BACKEND_URL` or same default as above |
| Backend deployment | Public URL + TLS for that host |
| Mobile release | `--dart-define=API_BASE_URL=https://api.livetranslate.net/` |
| Next.js (`website/`) | `BACKEND_URL` / `NEXT_PUBLIC_BACKEND_URL` in `.env.local` |
| Desktop | `LIVE_TRANSLATE_BACKEND_URL` or app settings |

## Deploy / ops checklist

1. DNS: `api.livetranslate.net` resolves to your FastAPI load balancer.
2. Backend env: `WIX_SYNC_SECRET` matches Wix Secrets Manager (`WIX_SYNC_SECRET`).
3. After changing `BACKEND_URL` in Velo, republish the Wix site.
4. For JSW: `wix env set BACKEND_URL https://api.livetranslate.net` if you override the default.
5. Smoke-test: `POST /api/v1/billing/wix/sync` and `POST /api/v1/auth/api-key` from Velo with a test member.
