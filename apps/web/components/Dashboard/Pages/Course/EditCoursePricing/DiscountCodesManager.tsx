'use client'
import React, { useCallback, useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'
import { useTranslation } from 'react-i18next'
import { Input } from '@components/ui/input'
import { Label } from '@components/ui/label'
import { formatCurrency } from '@/lib/format'
import { Ticket } from 'lucide-react'
import { getOrgCourses } from '@services/courses/courses'
import {
  createDiscountCode,
  deleteDiscountCode,
  listDiscountCodes,
  setDiscountCodeActive,
} from '@services/payments/discounts'

interface DiscountCode {
  id: number
  course_id: number | null
  code: string
  percent_off: number | null
  amount_off: number | null
  max_redemptions: number | null
  redemption_count: number
  expires_at: string | null
  single_use_per_user: boolean
  is_active: boolean
}

interface Props {
  orgId: number
  orgSlug: string
  courseId: number
  accessToken: string
}

/**
 * Manage the organization's discount codes from a course's pricing page.
 * A new code can apply to this course only or to every course in the org.
 */
const DiscountCodesManager: React.FC<Props> = ({ orgId, orgSlug, courseId, accessToken }) => {
  const { t, i18n } = useTranslation()
  const [codes, setCodes] = useState<DiscountCode[]>([])
  const [code, setCode] = useState('')
  const [kind, setKind] = useState<'percent' | 'amount'>('percent')
  const [value, setValue] = useState('')
  const [maxRedemptions, setMaxRedemptions] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  // '' = every course in the organization; otherwise a specific course id.
  const [scopeCourseId, setScopeCourseId] = useState<string>(String(courseId))
  const [orgCourses, setOrgCourses] = useState<{ id: number; name: string }[]>([])
  const [oncePerBuyer, setOncePerBuyer] = useState(true)
  const [saving, setSaving] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const res: any = await listDiscountCodes(orgId, accessToken)
      const list = res?.data ?? res
      setCodes(Array.isArray(list) ? list : [])
    } catch {
      setCodes([])
    }
  }, [orgId, accessToken])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh()
  }, [refresh])

  // The org's courses, so a code can be tied to any one of them.
  useEffect(() => {
    let active = true
    getOrgCourses(orgSlug, null, accessToken, true)
      .then((res: any) => {
        const list = res?.data ?? res
        if (active && Array.isArray(list)) {
          setOrgCourses(list.map((c: any) => ({ id: Number(c.id), name: c.name })))
        }
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [orgSlug, accessToken])

  const numValue = Number(value)
  const valueInvalid =
    value === '' ||
    !Number.isInteger(numValue) ||
    numValue <= 0 ||
    (kind === 'percent' && numValue > 99)
  const canCreate = code.trim().length >= 3 && !valueInvalid && !saving

  const create = async () => {
    setSaving(true)
    try {
      await createDiscountCode(
        orgId,
        {
          code: code.trim(),
          course_id: scopeCourseId ? Number(scopeCourseId) : null,
          percent_off: kind === 'percent' ? numValue : null,
          amount_off: kind === 'amount' ? numValue : null,
          max_redemptions: maxRedemptions ? Number(maxRedemptions) : null,
          expires_at: expiresAt ? `${expiresAt}T23:59:59` : null,
          single_use_per_user: oncePerBuyer,
        },
        accessToken
      )
      toast.success(t('dashboard.courses.pricing.discounts.created'))
      setCode('')
      setValue('')
      setMaxRedemptions('')
      setExpiresAt('')
      await refresh()
    } catch (e: any) {
      toast.error(e?.message || t('dashboard.courses.pricing.discounts.error'))
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (c: DiscountCode) => {
    try {
      await setDiscountCodeActive(orgId, c.id, !c.is_active, accessToken)
      await refresh()
    } catch (e: any) {
      toast.error(e?.message || t('dashboard.courses.pricing.discounts.error'))
    }
  }

  const remove = async (c: DiscountCode) => {
    try {
      await deleteDiscountCode(orgId, c.id, accessToken)
      toast.success(t('dashboard.courses.pricing.discounts.deleted'))
      await refresh()
    } catch (e: any) {
      toast.error(e?.message || t('dashboard.courses.pricing.discounts.error'))
    }
  }

  const describe = (c: DiscountCode) =>
    c.percent_off
      ? `${c.percent_off}%`
      : formatCurrency(c.amount_off ?? 0, 'IRR', i18n.language)

  return (
    <div className="bg-white rounded-xl nice-shadow">
      <div className="flex items-center gap-2 bg-gray-50 px-5 py-3 m-3 rounded-md">
        <Ticket className="w-5 h-5 text-gray-600" />
        <h3 className="font-bold text-lg text-gray-800">
          {t('dashboard.courses.pricing.discounts.title')}
        </h3>
      </div>
      <div className="p-4 pt-1 space-y-4 max-w-xl">
        <p className="text-sm text-gray-500">
          {t('dashboard.courses.pricing.discounts.help')}
        </p>

        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <Label htmlFor="discount-code">{t('dashboard.courses.pricing.discounts.code')}</Label>
            <Input
              id="discount-code"
              dir="ltr"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              autoComplete="off"
            />
          </div>
          <div>
            <Label htmlFor="discount-kind">{t('dashboard.courses.pricing.discounts.type')}</Label>
            <select
              id="discount-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value as 'percent' | 'amount')}
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
            >
              <option value="percent">{t('dashboard.courses.pricing.discounts.type_percent')}</option>
              <option value="amount">{t('dashboard.courses.pricing.discounts.type_amount')}</option>
            </select>
          </div>
          <div>
            <Label htmlFor="discount-value">{t('dashboard.courses.pricing.discounts.value')}</Label>
            <Input
              id="discount-value"
              inputMode="numeric"
              value={value}
              onChange={(e) => setValue(e.target.value.replace(/\D/g, ''))}
              aria-invalid={value !== '' && valueInvalid}
            />
          </div>
          <div>
            <Label htmlFor="discount-max">{t('dashboard.courses.pricing.discounts.max_redemptions')}</Label>
            <Input
              id="discount-max"
              inputMode="numeric"
              value={maxRedemptions}
              onChange={(e) => setMaxRedemptions(e.target.value.replace(/\D/g, ''))}
              placeholder={t('dashboard.courses.pricing.discounts.unlimited')}
            />
          </div>
          <div>
            <Label htmlFor="discount-expires">{t('dashboard.courses.pricing.discounts.expires')}</Label>
            <Input
              id="discount-expires"
              type="date"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
            />
          </div>
          <div className="col-span-2">
            <Label htmlFor="discount-course">{t('dashboard.courses.pricing.discounts.applies_to')}</Label>
            <select
              id="discount-course"
              value={scopeCourseId}
              onChange={(e) => setScopeCourseId(e.target.value)}
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
            >
              <option value="">{t('dashboard.courses.pricing.discounts.scope_all')}</option>
              {orgCourses.map((c) => (
                <option key={c.id} value={String(c.id)}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <label className="col-span-2 flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={oncePerBuyer}
              onChange={(e) => setOncePerBuyer(e.target.checked)}
            />
            {t('dashboard.courses.pricing.discounts.once_per_buyer')}
          </label>
        </div>

        <button
          onClick={create}
          disabled={!canCreate}
          className="inline-flex items-center gap-2 rounded-lg bg-black px-4 py-2 text-sm font-semibold text-white nice-shadow hover:bg-neutral-800 transition-colors disabled:opacity-40"
        >
          {t('dashboard.courses.pricing.discounts.create')}
        </button>

        {codes.length > 0 && (
          <ul className="divide-y divide-gray-100 border-t border-gray-100">
            {codes.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <div className="min-w-0">
                  <span className="font-mono font-semibold text-gray-900" dir="ltr">{c.code}</span>
                  <span className="ms-2 text-gray-600">{describe(c)}</span>
                  <div className="text-xs text-gray-500">
                    {c.course_id === null
                      ? t('dashboard.courses.pricing.discounts.scope_all')
                      : orgCourses.find((oc) => oc.id === c.course_id)?.name ??
                        t('dashboard.courses.pricing.discounts.scope_other')}
                    {' · '}
                    {c.redemption_count}
                    {c.max_redemptions ? `/${c.max_redemptions}` : ''}{' '}
                    {t('dashboard.courses.pricing.discounts.used')}
                    {c.expires_at ? ` · ${c.expires_at.slice(0, 10)}` : ''}
                    {c.single_use_per_user ? '' : ` · ${t('dashboard.courses.pricing.discounts.reusable')}`}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <button
                    onClick={() => toggle(c)}
                    className={`text-xs font-semibold ${c.is_active ? 'text-emerald-700' : 'text-gray-500'}`}
                  >
                    {c.is_active
                      ? t('dashboard.courses.pricing.discounts.active')
                      : t('dashboard.courses.pricing.discounts.inactive')}
                  </button>
                  <button
                    onClick={() => remove(c)}
                    className="text-xs font-semibold text-red-600"
                  >
                    {t('dashboard.courses.pricing.discounts.delete')}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default DiscountCodesManager
