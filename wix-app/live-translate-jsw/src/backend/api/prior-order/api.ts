import { auth } from '@wix/essentials';
import { orders } from '@wix/ecom';

// The free app downloads on livetranslate.net, keyed by their product page slug.
const APP_PRODUCTS: Record<string, string> = {
  'dubtool-installation-file': '78550f14-d605-470c-ba15-25845f9e6d73',
  'livetranslate-live-ai-dubbing': '173163ed-5e38-4920-a8e3-d58b1c117aa8',
};

const searchOrders = auth.elevate(orders.searchOrders);

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

  const result = await searchOrders({
    filter: { 'buyerInfo.memberId': { $eq: token.subjectId } },
    cursorPaging: { limit: 100 },
  });
  const found = (result.orders ?? []).some(
    (order) =>
      order.status === 'APPROVED' &&
      (order.lineItems ?? []).some((item) => item.catalogReference?.catalogItemId === productId),
  );
  return Response.json({ found });
}
