'use server';
// Gateway-agnostic course-payment service.
//
// A course sells for a single shared IRR price regardless of gateway, and the
// buyer picks one of the gateways configured on this instance at payment time.
// These endpoints expose that shared view so the UI can set one price and offer
// a gateway choice. Gateway-specific calls (checkout) still live in
// providers/zarinpal.ts and providers/irankish.ts.
//
// Backend routes live under /api/v1/payments/... (see
// apps/api/src/routers/payments/gateways.py).
import { getAPIUrl } from '@services/config/config';
import { RequestBodyWithAuthHeader, errorHandling, getResponseMetadata, secureFetch } from '@services/utils/ts/requests';

/**
 * Which payment gateways are active (configured) on this instance.
 * Returns { zarinpal: boolean, irankish: boolean, active: string[] }.
 * Public probe; reveals no credentials.
 */
export async function getActiveGateways(access_token?: string) {
  const result = await secureFetch(
    `${getAPIUrl()}payments/gateways`,
    RequestBodyWithAuthHeader('GET', null, null, access_token || '')
  );
  return getResponseMetadata(result);
}

/**
 * A course's single shared IRR price and the active gateways it can be bought
 * through. Returns
 * { course_id, price_irr: number | null, gateways: string[], purchasable }.
 */
export async function getCoursePaymentOptions(
  orgId: number,
  courseId: number,
  access_token?: string
) {
  const result = await secureFetch(
    `${getAPIUrl()}payments/${encodeURIComponent(String(orgId))}/courses/${encodeURIComponent(String(courseId))}/options`,
    RequestBodyWithAuthHeader('GET', null, null, access_token || '')
  );
  return getResponseMetadata(result);
}

/**
 * Admin: set (or clear, with price_irr = null) the single shared IRR price a
 * course sells for across all gateways. Routed through the ZarinPal price
 * endpoint, which writes the shared, gateway-agnostic price key on the backend.
 */
export async function setCoursePrice(
  orgId: number,
  courseId: number,
  priceIrr: number | null,
  access_token: string
) {
  const result = await secureFetch(
    `${getAPIUrl()}payments/zarinpal/${encodeURIComponent(String(orgId))}/courses/${encodeURIComponent(String(courseId))}/price`,
    RequestBodyWithAuthHeader('POST', { price_irr: priceIrr }, null, access_token)
  );
  return errorHandling(result);
}
