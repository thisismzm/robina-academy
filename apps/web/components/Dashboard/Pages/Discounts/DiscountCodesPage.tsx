'use client'
import React, { useCallback, useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'
import { useTranslation } from 'react-i18next'
import { Input } from '@components/ui/input'
import { Label } from '@components/ui/label'
import { formatCurrency, formatDate } from '@/lib/format'
import { useOrg } from '@components/Contexts/OrgContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
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
  description: string
  percent_off: number | null
  amount_off: number | null
  max_redemptions: number | null
  redemption_count: number
  expires_at: string | null
  single_use_per_user: boolean
  is_active: boolean
}

/** Create and manage the organization's discount codes. */
const DiscountCodesPage: React.FC = () => {
  const { t, i18n } = useTranslation()
  const org = useOrg() as any
  const session = useLHSession() as any
  const accessToken = session?.data?.tokens?.access_token
  const orgId: number | undefined = org?.id
  const orgSlug: string | undefined = org?.slug

  const [codes, setCodes] = useState<DiscountCode[]>([])
  const [orgCourses, setOrgCourses] = useState<{ id: number; name: string }[]>([])

  const [code, setCode] = useState('')
  const [description, setDescription] = useState('')
  const [kind, setKind] = useState<'percent' | 'amount'>('percent')
  const [value, setValue] = useState('')
  const [maxRedemptions, setMaxRedemptions] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  // '' = every course in the organization; otherwise a specific course id.
  const [scopeCourseId, setScopeCourseId] = useState('')
  const [oncePerBuyer, setOncePerBuyer] = useState(true)
  const [saving, setSaving] = useState(false)

  const refresh = useCallback(async () => {
    if (!orgId || !accessToken) return
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

  useEffect(() => {
    if (!orgSlug || !accessToken) return
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
  const canCreate =
    code.trim().length >= 3 && description.trim().length > 0 && !valueInvalid && !saving

  const create = async () => {
    if (!orgId) return
    setSaving(true)
    try {
      await createDiscountCode(
        orgId,
        {
          code: code.trim(),
          description: description.trim(),
          course_id: scopeCourseId ? Number(scopeCourseId) : null,
          percent_off: kind === 'percent' ? numValue : null,
          amount_off: kind === 'amount' ? numValue : null,
          max_redemptions: maxRedemptions ? Number(maxRedemptions) : null,
          expires_at: expiresAt ? `${expiresAt}T23:59:59` : null,
          single_use_per_user: oncePerBuyer,
        },
        accessToken
      )
      toast.success(t('dashboard.discounts.created'))
      setCode('')
      setDescription('')
      setValue('')
      setMaxRedemptions('')
      setExpiresAt('')
      await refresh()
    } catch (e: any) {
      toast.error(e?.message || t('dashboard.discounts.error'))
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (c: DiscountCode) => {
    if (!orgId) return
    try {
      await setDiscountCodeActive(orgId, c.id, !c.is_active, accessToken)
      await refresh()
    } catch (e: any) {
      toast.error(e?.message || t('dashboard.discounts.error'))
    }
  }

  const remove = async (c: DiscountCode) => {
    if (!orgId) return
    if (!window.confirm(t('dashboard.discounts.confirm_delete', { code: c.code }))) return
    try {
      await deleteDiscountCode(orgId, c.id, accessToken)
      toast.success(t('dashboard.discounts.deleted'))
      await refresh()
    } catch (e: any) {
      toast.error(e?.message || t('dashboard.discounts.error'))
    }
  }

  const describeValue = (c: DiscountCode) =>
    c.percent_off
      ? `${c.percent_off}%`
      : formatCurrency(c.amount_off ?? 0, 'IRR', i18n.language)

  const scopeLabel = (c: DiscountCode) =>
    c.course_id === null
      ? t('dashboard.discounts.scope_all')
      : (orgCourses.find((oc) => oc.id === c.course_id)?.name ??
        t('dashboard.discounts.scope_other'))

  return (
    <div className="sm:mx-10 mx-0 space-y-6 pb-10">
      <div className="bg-white rounded-xl nice-shadow">
        <div className="bg-gray-50 px-5 py-3 m-3 rounded-md">
          <h3 className="font-bold text-lg text-gray-800">{t('dashboard.discounts.new_title')}</h3>
          <p className="text-sm text-gray-500">{t('dashboard.discounts.help')}</p>
        </div>
        <div className="p-4 pt-1 grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-3xl">
          <div>
            <Label htmlFor="discount-code">{t('dashboard.discounts.code')}</Label>
            <Input
              id="discount-code"
              dir="ltr"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              autoComplete="off"
            />
          </div>
          <div>
            <Label htmlFor="discount-course">{t('dashboard.discounts.applies_to')}</Label>
            <select
              id="discount-course"
              value={scopeCourseId}
              onChange={(e) => setScopeCourseId(e.target.value)}
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
            >
              <option value="">{t('dashboard.discounts.scope_all')}</option>
              {orgCourses.map((c) => (
                <option key={c.id} value={String(c.id)}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div className="sm:col-span-2">
            <Label htmlFor="discount-description">{t('dashboard.discounts.description')}</Label>
            <Input
              id="discount-description"
              value={description}
              maxLength={500}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t('dashboard.discounts.description_placeholder')}
            />
          </div>
          <div>
            <Label htmlFor="discount-kind">{t('dashboard.discounts.type')}</Label>
            <select
              id="discount-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value as 'percent' | 'amount')}
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
            >
              <option value="percent">{t('dashboard.discounts.type_percent')}</option>
              <option value="amount">{t('dashboard.discounts.type_amount')}</option>
            </select>
          </div>
          <div>
            <Label htmlFor="discount-value">{t('dashboard.discounts.value')}</Label>
            <Input
              id="discount-value"
              inputMode="numeric"
              value={value}
              onChange={(e) => setValue(e.target.value.replace(/\D/g, ''))}
              aria-invalid={value !== '' && valueInvalid}
            />
          </div>
          <div>
            <Label htmlFor="discount-max">{t('dashboard.discounts.max_redemptions')}</Label>
            <Input
              id="discount-max"
              inputMode="numeric"
              value={maxRedemptions}
              onChange={(e) => setMaxRedemptions(e.target.value.replace(/\D/g, ''))}
              placeholder={t('dashboard.discounts.unlimited')}
            />
          </div>
          <div>
            <Label htmlFor="discount-expires">{t('dashboard.discounts.expires')}</Label>
            <Input
              id="discount-expires"
              type="date"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
            />
          </div>
          <label className="sm:col-span-2 flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={oncePerBuyer}
              onChange={(e) => setOncePerBuyer(e.target.checked)}
            />
            {t('dashboard.discounts.once_per_buyer')}
          </label>
          <div className="sm:col-span-2">
            <button
              onClick={create}
              disabled={!canCreate}
              className="inline-flex items-center gap-2 rounded-lg bg-black px-4 py-2 text-sm font-semibold text-white nice-shadow hover:bg-neutral-800 transition-colors disabled:opacity-40"
            >
              {t('dashboard.discounts.create')}
            </button>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl nice-shadow overflow-hidden">
        <div className="bg-gray-50 px-5 py-3 m-3 rounded-md">
          <h3 className="font-bold text-lg text-gray-800">{t('dashboard.discounts.list_title')}</h3>
        </div>
        {codes.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">{t('dashboard.discounts.empty')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-start text-xs text-gray-500">
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.code')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.description')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.value')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.applies_to')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.uses')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.expires')}</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {codes.map((c) => (
                  <tr key={c.id} className={c.is_active ? '' : 'opacity-60'}>
                    <td className="px-4 py-2 font-mono font-semibold text-gray-900" dir="ltr">{c.code}</td>
                    <td className="px-4 py-2 text-gray-700 max-w-xs">{c.description}</td>
                    <td className="px-4 py-2 text-gray-700">{describeValue(c)}</td>
                    <td className="px-4 py-2 text-gray-700">{scopeLabel(c)}</td>
                    <td className="px-4 py-2 text-gray-700">
                      {c.redemption_count}
                      {c.max_redemptions ? ` / ${c.max_redemptions}` : ` / ${t('dashboard.discounts.unlimited')}`}
                      {c.single_use_per_user ? '' : ` · ${t('dashboard.discounts.reusable')}`}
                    </td>
                    <td className="px-4 py-2 text-gray-700">
                      {c.expires_at ? formatDate(c.expires_at, i18n.language) : '-'}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex items-center justify-end gap-3">
                        <button
                          onClick={() => toggle(c)}
                          className={`text-xs font-semibold ${c.is_active ? 'text-emerald-700' : 'text-gray-500'}`}
                        >
                          {c.is_active ? t('dashboard.discounts.active') : t('dashboard.discounts.inactive')}
                        </button>
                        <button onClick={() => remove(c)} className="text-xs font-semibold text-red-600">
                          {t('dashboard.discounts.delete')}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

export default DiscountCodesPage
