import { auth } from '@wix/essentials';
import { orders } from '@wix/ecom';

// The free app downloads on livetranslate.net, keyed by their product page slug.
const APP_PRODUCTS: Record<string, string> = {
  'dubtool-installation-file': '78550f14-d605-470c-ba15-25845f9e6d73',
  'livetranslate-live-ai-dubbing': '173163ed-5e38-4920-a8e3-d58b1c117aa8',
};

const searchOrders = auth.elevate(orders.searchOrders);

const PAGE_SIZE = 100;
// Safety cap so one request can't loop forever: 50 pages = 5,000 orders.
const MAX_PAGES = 50;

export async function GET(req: Request) {
  const slug = new URL(req.url).searchParams.get('slug') ?? '';
  const productId = APP_PRODUCTS[slug];
  if (!productId) {
    return Response.json({ found: false });
  }

  const token = await auth.getTokenInfo();
  if (token.subjectType !== 'MEMBER' || !token.subjectId) {
    return Response.json({ found: false });
  }

  // Walk every page of the member's orders (newest first) so an older approved
  // download order is still found for members with more than one page of orders.
  // Follow-up pages pass only the cursor, as Wix cursor paging requires.
  let cursor: string | null | undefined;
  let found = false;
  for (let page = 0; page < MAX_PAGES && !found; page++) {
    const result = await searchOrders(
      cursor
        ? { cursorPaging: { limit: PAGE_SIZE, cursor } }
        : {
            filter: { 'buyerInfo.memberId': { $eq: token.subjectId } },
            cursorPaging: { limit: PAGE_SIZE },
          },
    );
    found = (result.orders ?? []).some(
      (order) =>
        order.status === 'APPROVED' &&
        (order.lineItems ?? []).some((item) => item.catalogReference?.catalogItemId === productId),
    );
    cursor = result.metadata?.cursors?.next;
    if (!cursor || result.metadata?.hasNext === false) {
      break;
    }
  }
  return Response.json({ found });
}
