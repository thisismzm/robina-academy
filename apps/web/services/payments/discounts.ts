'use server';
// Discount codes for course purchases.
//
// Backend routes live under /api/v1/payments/discounts/... (see
// apps/api/src/routers/payments/discounts.py).

import { getAPIUrl } from '@services/config/config';
import { RequestBodyWithAuthHeader, errorHandling, secureFetch } from '@services/utils/ts/requests';

const base = (orgId: number) =>
  `${getAPIUrl()}payments/discounts/${encodeURIComponent(String(orgId))}`;

/** Buyer: preview the price of a course with a code applied. */
export async function validateDiscountCode(
  orgId: number,
  courseId: number,
  code: string,
  access_token: string
) {
  const result = await secureFetch(
    `${base(orgId)}/courses/${encodeURIComponent(String(courseId))}/validate`,
    RequestBodyWithAuthHeader('POST', { code }, null, access_token)
  );
  return errorHandling(result);
}

/** Admin: list the organization's discount codes. */
export async function listDiscountCodes(orgId: number, access_token: string) {
  const result = await secureFetch(
    base(orgId),
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  );
  return errorHandling(result);
}

export interface CreateDiscountCodeInput {
  code: string;
  description: string;
  course_id?: number | null;
  percent_off?: number | null;
  amount_off?: number | null;
  max_redemptions?: number | null;
  expires_at?: string | null;
  single_use_per_user?: boolean;
}

/** Admin: create a discount code. */
export async function createDiscountCode(
  orgId: number,
  input: CreateDiscountCodeInput,
  access_token: string
) {
  const result = await secureFetch(
    base(orgId),
    RequestBodyWithAuthHeader('POST', input, null, access_token)
  );
  return errorHandling(result);
}

/** Admin: activate / deactivate a code. */
export async function setDiscountCodeActive(
  orgId: number,
  codeId: number,
  isActive: boolean,
  access_token: string
) {
  const result = await secureFetch(
    `${base(orgId)}/${encodeURIComponent(String(codeId))}`,
    RequestBodyWithAuthHeader('PUT', { is_active: isActive }, null, access_token)
  );
  return errorHandling(result);
}

/** Admin: delete a code (past payments keep their amounts). */
export async function deleteDiscountCode(
  orgId: number,
  codeId: number,
  access_token: string
) {
  const result = await secureFetch(
    `${base(orgId)}/${encodeURIComponent(String(codeId))}`,
    RequestBodyWithAuthHeader('DELETE', null, null, access_token)
  );
  return errorHandling(result);
}

/** Admin: per-code usage totals (completed payments). */
export async function getDiscountReport(orgId: number, access_token: string) {
  const result = await secureFetch(
    `${base(orgId)}/report`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  );
  return errorHandling(result);
}

/** Admin: payments that used a code, newest first. */
export async function listDiscountRedemptions(
  orgId: number,
  access_token: string,
  opts: { codeId?: number | null; includeIncomplete?: boolean; limit?: number; offset?: number } = {}
) {
  const qs = new URLSearchParams();
  if (opts.codeId) qs.set('code_id', String(opts.codeId));
  if (opts.includeIncomplete) qs.set('include_incomplete', 'true');
  qs.set('limit', String(opts.limit ?? 50));
  qs.set('offset', String(opts.offset ?? 0));
  const result = await secureFetch(
    `${base(orgId)}/redemptions?${qs.toString()}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  );
  return errorHandling(result);
}
