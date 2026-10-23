'use client'
import React, { useEffect, useState } from 'react'
import { useOrg } from '@components/Contexts/OrgContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useCourse } from '@components/Contexts/CourseContext'
import { toast } from 'react-hot-toast'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import { revalidateTags } from '@services/utils/ts/requests'
import {
  getActiveGateways,
  setCoursePrice,
} from '@services/payments/gateways'
import { useTranslation } from 'react-i18next'
import { Input } from '@components/ui/input'
import { Label } from '@components/ui/label'
import { formatCurrency, formatNumber } from '@/lib/format'
import { FloppyDisk } from '@phosphor-icons/react'
import { Banknote, Info } from 'lucide-react'

// The single, gateway-agnostic key the backend stores the price under. The
// shared price is read from here (with a fallback to legacy per-gateway keys so
// courses priced before this change still show their price).
const SHARED_PRICE_KEY = 'course_price_irr'
const LEGACY_PRICE_KEYS = ['zarinpal_price_irr', 'irankish_price_irr']

// Read a positive integer price off a course's metadata, preferring the shared
// key and falling back to whichever legacy key still carries a value.
function readSharedPrice(extraMetadata: any): number | null {
  const keys = [SHARED_PRICE_KEY, ...LEGACY_PRICE_KEYS]
  for (const key of keys) {
    const raw = extraMetadata?.[key]
    const n = Number(raw)
    if (raw != null && Number.isFinite(n) && n > 0) return n
  }
  return null
}

/**
 * Single price control shared by every payment gateway. A course sells for one
 * IRR amount regardless of which gateway the buyer chooses, so there is one
 * input here. The card also shows which gateways are active (configured) on
 * this instance, since the buyer picks one of those at payment time.
 */
const EditCoursePricing: React.FC = () => {
  const { t, i18n } = useTranslation()
  const org = useOrg() as any
  const session = useLHSession() as any
  const access_token = session?.data?.tokens?.access_token
  const course = useCourse() as any
  const courseStructure = course?.courseStructure
  const queryClient = useQueryClient()

  // Which gateways are active on this instance (configured with credentials).
  const [activeGateways, setActiveGateways] = useState<string[] | null>(null)
  const [price, setPrice] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let active = true
    getActiveGateways(access_token)
      .then((res: any) => {
        const list = res?.data?.active ?? res?.active ?? []
        if (active) setActiveGateways(Array.isArray(list) ? list : [])
      })
      .catch(() => {
        if (active) setActiveGateways([])
      })
    return () => {
      active = false
    }
  }, [access_token])

  useEffect(() => {
    const current = readSharedPrice(courseStructure?.extra_metadata)
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPrice(current != null ? String(current) : '')
  }, [courseStructure])

  // `price` is a raw digit string; parse it for validation/saving.
  const parsed = price.trim() === '' ? null : Number(price.trim())
  const priceInvalid = parsed !== null && (!Number.isInteger(parsed) || parsed <= 0)

  // Show thousands separators while typing (e.g. 500,000) but keep only digits
  // in state.
  const displayValue =
    parsed !== null && Number.isFinite(parsed)
      ? formatNumber(parsed, i18n.language)
      : price

  const onPriceChange = (raw: string) => {
    setPrice(raw.replace(/\D/g, ''))
  }

  const hasActiveGateway = (activeGateways?.length ?? 0) > 0

  const save = async () => {
    if (priceInvalid) {
      toast.error(t('dashboard.courses.pricing.price_invalid'))
      return
    }
    setSaving(true)
    const tid = toast.loading(t('dashboard.courses.pricing.saving'))
    try {
      await setCoursePrice(org.id, Number(courseStructure.id), parsed, access_token)
      await revalidateTags(['courses'], org.slug)
      queryClient.invalidateQueries({
        queryKey: queryKeys.courses.meta(courseStructure.course_uuid),
      })
      toast.success(
        parsed === null
          ? t('dashboard.courses.pricing.cleared')
          : t('dashboard.courses.pricing.saved'),
        { id: tid }
      )
    } catch (e: any) {
      toast.error(e?.message || t('dashboard.courses.pricing.error'), { id: tid })
    } finally {
      setSaving(false)
    }
  }

  const preview =
    parsed !== null && !priceInvalid
      ? formatCurrency(parsed, 'IRR', i18n.language)
      : null

  return (
    <div className="sm:mx-10 mx-0 space-y-4">
      <div className="px-1">
        <h1 className="font-bold text-xl text-gray-800">
          {t('dashboard.courses.pricing.title')}
        </h1>
        <h2 className="text-gray-500 text-md">
          {t('dashboard.courses.pricing.subtitle')}
        </h2>
      </div>

      <div className="bg-white rounded-xl nice-shadow">
        <div className="flex items-center justify-between bg-gray-50 px-5 py-3 m-3 rounded-md">
          <div className="flex items-center gap-2">
            <Banknote className="w-5 h-5 text-gray-600" />
            <h3 className="font-bold text-lg text-gray-800">
              {t('dashboard.courses.pricing.price_label')}
            </h3>
          </div>
          <button
            onClick={save}
            disabled={saving || !hasActiveGateway}
            className="inline-flex items-center gap-2 rounded-lg bg-black px-4 py-2 text-sm font-semibold text-white nice-shadow hover:bg-neutral-800 transition-colors disabled:opacity-40"
          >
            <FloppyDisk size={16} weight="bold" />
            <span>{t('dashboard.courses.pricing.save')}</span>
          </button>
        </div>

        <div className="p-4 pt-1 space-y-4 max-w-xl">
          {/* Which gateways the buyer will be able to choose from. */}
          {activeGateways !== null && (
            hasActiveGateway ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-gray-500">
                  {t('dashboard.courses.pricing.active_gateways_label')}
                </span>
                {activeGateways.map((g) => (
                  <span
                    key={g}
                    className="text-xs font-medium text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full"
                  >
                    {t(`dashboard.courses.pricing.gateways.${g}`)}
                  </span>
                ))}
              </div>
            ) : (
              <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg">
                <div className="flex items-center gap-3">
                  <Info className="w-5 h-5 text-amber-800 shrink-0" />
                  <h4 className="text-amber-800 font-semibold">
                    {t('dashboard.courses.pricing.no_gateway_title')}
                  </h4>
                </div>
                <p className="text-amber-700 text-sm mt-1">
                  {t('dashboard.courses.pricing.no_gateway_description')}
                </p>
              </div>
            )
          )}

          <div>
            <Label htmlFor="course-price" className="flex items-center gap-2">
              {t('dashboard.courses.pricing.price_label')}
            </Label>
            <Input
              id="course-price"
              type="text"
              inputMode="numeric"
              value={displayValue}
              onChange={(e) => onPriceChange(e.target.value)}
              placeholder={t('dashboard.courses.pricing.price_placeholder')}
              aria-invalid={priceInvalid}
              disabled={!hasActiveGateway}
            />
            <p className={`text-xs mt-1 ${priceInvalid ? 'text-red-600' : 'text-gray-500'}`}>
              {priceInvalid
                ? t('dashboard.courses.pricing.price_invalid')
                : t('dashboard.courses.pricing.price_help')}
            </p>
            {preview && (
              <p className="text-sm text-gray-700 mt-2">
                {t('dashboard.courses.pricing.price_preview', { price: preview })}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default EditCoursePricing
