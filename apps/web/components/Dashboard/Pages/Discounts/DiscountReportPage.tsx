'use client'
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatCurrency, formatDateTime, formatNumber } from '@/lib/format'
import { useOrg } from '@components/Contexts/OrgContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getDiscountReport, listDiscountRedemptions } from '@services/payments/discounts'

interface CodeStats {
  code_id: number
  code: string
  description: string
  is_active: boolean
  max_redemptions: number | null
  redemptions: number
  unique_buyers: number
  original_total: number
  discount_total: number
  revenue_total: number
  incomplete_attempts: number
}

interface Report {
  redemptions: number
  discount_total: number
  revenue_total: number
  original_total: number
  codes: CodeStats[]
}

interface Redemption {
  payment_uuid: string
  gateway: string
  status: string
  code: string | null
  username: string | null
  email: string | null
  first_name: string | null
  last_name: string | null
  course_name: string | null
  original_amount: number | null
  discount_amount: number | null
  amount: number
  date: string
}

const PAGE_SIZE = 25

/** Usage report: totals, per-code stats, and the payments that used a code. */
const DiscountReportPage: React.FC = () => {
  const { t, i18n } = useTranslation()
  const org = useOrg() as any
  const session = useLHSession() as any
  const accessToken = session?.data?.tokens?.access_token
  const orgId: number | undefined = org?.id

  const [report, setReport] = useState<Report | null>(null)
  const [rows, setRows] = useState<Redemption[]>([])
  const [codeFilter, setCodeFilter] = useState('')
  const [includeIncomplete, setIncludeIncomplete] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)

  const money = (n: number | null | undefined) =>
    n == null ? '-' : formatCurrency(n, 'IRR', i18n.language)

  useEffect(() => {
    if (!orgId || !accessToken) return
    let active = true
    getDiscountReport(orgId, accessToken)
      .then((res: any) => {
        if (active) setReport(res?.data ?? res)
      })
      .catch(() => {
        if (active) setReport(null)
      })
    return () => {
      active = false
    }
  }, [orgId, accessToken])

  const load = useCallback(
    async (offset: number) => {
      if (!orgId || !accessToken) return
      setLoading(true)
      try {
        const res: any = await listDiscountRedemptions(orgId, accessToken, {
          codeId: codeFilter ? Number(codeFilter) : null,
          includeIncomplete,
          limit: PAGE_SIZE,
          offset,
        })
        const list: Redemption[] = Array.isArray(res?.data ?? res) ? (res?.data ?? res) : []
        setRows((prev) => (offset === 0 ? list : [...prev, ...list]))
        setHasMore(list.length === PAGE_SIZE)
      } catch {
        if (offset === 0) setRows([])
        setHasMore(false)
      } finally {
        setLoading(false)
      }
    },
    [orgId, accessToken, codeFilter, includeIncomplete]
  )

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load(0)
  }, [load])

  const buyerName = (r: Redemption) =>
    [r.first_name, r.last_name].filter(Boolean).join(' ') || r.username || '-'

  const summary = [
    { label: t('dashboard.discounts.report.redemptions'), value: formatNumber(report?.redemptions ?? 0, i18n.language) },
    { label: t('dashboard.discounts.report.discount_total'), value: money(report?.discount_total ?? 0) },
    { label: t('dashboard.discounts.report.revenue_total'), value: money(report?.revenue_total ?? 0) },
  ]

  return (
    <div className="sm:mx-10 mx-0 space-y-6 pb-10">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {summary.map((s) => (
          <div key={s.label} className="bg-white rounded-xl nice-shadow p-4">
            <div className="text-xs text-gray-500">{s.label}</div>
            <div className="text-2xl font-black text-gray-900 mt-1">{s.value}</div>
          </div>
        ))}
      </div>

      <div className="bg-white rounded-xl nice-shadow overflow-hidden">
        <div className="bg-gray-50 px-5 py-3 m-3 rounded-md">
          <h3 className="font-bold text-lg text-gray-800">{t('dashboard.discounts.report.by_code')}</h3>
        </div>
        {!report || report.codes.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">{t('dashboard.discounts.empty')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500">
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.code')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.description')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.uses')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.buyers')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.discount_total')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.revenue_total')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.incomplete')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {report.codes.map((c) => (
                  <tr key={c.code_id} className={c.is_active ? '' : 'opacity-60'}>
                    <td className="px-4 py-2 font-mono font-semibold text-gray-900" dir="ltr">{c.code}</td>
                    <td className="px-4 py-2 text-gray-700 max-w-xs">{c.description}</td>
                    <td className="px-4 py-2 text-gray-700">
                      {c.redemptions}
                      {c.max_redemptions ? ` / ${c.max_redemptions}` : ''}
                    </td>
                    <td className="px-4 py-2 text-gray-700">{c.unique_buyers}</td>
                    <td className="px-4 py-2 text-gray-700">{money(c.discount_total)}</td>
                    <td className="px-4 py-2 text-gray-700">{money(c.revenue_total)}</td>
                    <td className="px-4 py-2 text-gray-700">{c.incomplete_attempts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl nice-shadow overflow-hidden">
        <div className="bg-gray-50 px-5 py-3 m-3 rounded-md flex flex-wrap items-center justify-between gap-3">
          <h3 className="font-bold text-lg text-gray-800">{t('dashboard.discounts.report.usage')}</h3>
          <div className="flex flex-wrap items-center gap-3">
            <select
              aria-label={t('dashboard.discounts.report.filter_code')}
              value={codeFilter}
              onChange={(e) => setCodeFilter(e.target.value)}
              className="h-9 rounded-md border border-input bg-white px-3 text-sm"
            >
              <option value="">{t('dashboard.discounts.report.all_codes')}</option>
              {(report?.codes ?? []).map((c) => (
                <option key={c.code_id} value={String(c.code_id)}>
                  {c.code}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={includeIncomplete}
                onChange={(e) => setIncludeIncomplete(e.target.checked)}
              />
              {t('dashboard.discounts.report.include_incomplete')}
            </label>
          </div>
        </div>
        {rows.length === 0 && !loading ? (
          <p className="p-6 text-sm text-gray-500">{t('dashboard.discounts.report.no_usage')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500">
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.date')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.code')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.buyer')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.course')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.original')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.discount')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.paid')}</th>
                  <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.gateway')}</th>
                  {includeIncomplete && (
                    <th className="px-4 py-2 text-start font-medium">{t('dashboard.discounts.report.status')}</th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {rows.map((r) => (
                  <tr key={r.payment_uuid}>
                    <td className="px-4 py-2 text-gray-700 whitespace-nowrap">
                      {(() => {
                        const d = new Date(r.date)
                        return Number.isNaN(d.getTime()) ? r.date : formatDateTime(d, i18n.language)
                      })()}
                    </td>
                    <td className="px-4 py-2 font-mono text-gray-900" dir="ltr">{r.code ?? '-'}</td>
                    <td className="px-4 py-2 text-gray-700">
                      <div>{buyerName(r)}</div>
                      <div className="text-xs text-gray-500" dir="ltr">{r.email}</div>
                    </td>
                    <td className="px-4 py-2 text-gray-700">{r.course_name ?? '-'}</td>
                    <td className="px-4 py-2 text-gray-700">{money(r.original_amount)}</td>
                    <td className="px-4 py-2 text-gray-700">{money(r.discount_amount)}</td>
                    <td className="px-4 py-2 font-semibold text-gray-900">{money(r.amount)}</td>
                    <td className="px-4 py-2 text-gray-700">{t(`courses.gateways.${r.gateway}`)}</td>
                    {includeIncomplete && <td className="px-4 py-2 text-gray-700">{r.status}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {hasMore && (
          <div className="p-4">
            <button
              onClick={() => load(rows.length)}
              disabled={loading}
              className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              {t('dashboard.discounts.report.load_more')}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export default DiscountReportPage
