import { httpClient } from '@wix/essentials';

const ORDERS_PAGE = '/my-orders';
const checked = new Set<string>();

function productSlug(): string | null {
  return location.pathname.match(/\/product-page\/([^/?#]+)/)?.[1] ?? null;
}

async function sendReturningBuyerToOrders(force = false) {
  const slug = productSlug();
  if (!slug || (!force && checked.has(slug))) return;
  checked.add(slug);
  try {
    const res = await httpClient.fetchWithAuth(
      `${import.meta.env.BASE_API_URL}/prior-order?slug=${encodeURIComponent(slug)}`,
    );
    if (!res.ok) return;
    const { found } = await res.json();
    if (found && productSlug() === slug) location.assign(ORDERS_PAGE);
  } catch (error) {
    console.warn('Prior order check failed', error);
  }
}

// Wix sites switch pages without reloading, so watch for the path to change.
let lastPath = location.pathname;
setInterval(() => {
  if (location.pathname !== lastPath) {
    lastPath = location.pathname;
    sendReturningBuyerToOrders();
  }
}, 500);

const embeds = (window as unknown as {
  wixEmbedsAPI?: { registerToEvent?: (event: string, callback: () => void) => void };
}).wixEmbedsAPI;
embeds?.registerToEvent?.('login', () => sendReturningBuyerToOrders(true));

sendReturningBuyerToOrders();
