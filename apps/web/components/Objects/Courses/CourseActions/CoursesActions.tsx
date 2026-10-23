import React, { useEffect, useState } from 'react'
import { removeCourse, startCourse } from '@services/courses/activity'
import { revalidateTags, asArray } from '@services/utils/ts/requests'
import { useRouter, useSearchParams } from 'next/navigation'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getUriWithOrg } from '@services/config/config'
import { getOffersByResource } from '@services/payments/offers'
import { startZarinPalCheckout } from '@services/payments/providers/zarinpal'
import { startIranKishCheckout } from '@services/payments/providers/irankish'
import { getActiveGateways } from '@services/payments/gateways'
import { UserPen, ClockIcon, ArrowRight, BookOpen, UserPlus, Banknote } from 'lucide-react'
import { OfferCard } from './OfferCard'
import { applyForContributor } from '@services/courses/courses'
import toast from 'react-hot-toast'
import { useContributorStatus } from '../../../../hooks/useContributorStatus'
import CourseProgress from '../CourseProgress/CourseProgress'
import UserAvatar from '@components/Objects/UserAvatar'
import { useOrg, useOrgMembership } from '@components/Contexts/OrgContext'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import { useTranslation } from 'react-i18next'
import { formatCurrency } from '@/lib/format'
import { useLHAnalytics, AnalyticsEvent } from '@services/analytics'

// A course sells for a single shared IRR price regardless of gateway. The
// backend stores it under one key; the legacy per-gateway keys are read as a
// fallback so courses priced before this change still show a price.
const SHARED_PRICE_KEY = 'course_price_irr'
const LEGACY_PRICE_KEYS = ['zarinpal_price_irr', 'irankish_price_irr']

// Read the course's shared positive-integer IRR price, or null if not for sale.
function readCoursePrice(course: any): number | null {
  const meta = course?.extra_metadata
  for (const key of [SHARED_PRICE_KEY, ...LEGACY_PRICE_KEYS]) {
    const raw = meta?.[key]
    const n = Number(raw)
    if (raw != null && Number.isFinite(n) && n > 0) return n
  }
  return null
}

// POST a set of form fields to a URL by building and auto-submitting a form.
// IranKish requires the browser to POST tokenIdentity to its payment page
// (unlike ZarinPal's simple GET redirect).
function postToGateway(url: string, fields: Record<string, string>) {
  const form = document.createElement('form')
  form.method = 'POST'
  form.action = url
  for (const [name, value] of Object.entries(fields)) {
    const input = document.createElement('input')
    input.type = 'hidden'
    input.name = name
    input.value = value
    form.appendChild(input)
  }
  document.body.appendChild(form)
  form.submit()
}

interface CourseRun {
  status: string
  course_id: string
  steps: Array<{
    activity_id: string
    complete: boolean
  }>
}

interface Course {
  id: string
  course_uuid: string
  trail?: {
    runs: CourseRun[]
  }
  chapters?: Array<{
    name: string
    activities: Array<{
      activity_uuid: string
      name: string
      activity_type: string
    }>
  }>
  open_to_contributors?: boolean
}

interface CourseActionsProps {
  courseuuid: string
  orgslug: string
  course: Course & {
    org_id: number
  }
  trailData?: any
}

function CoursesActions({ courseuuid, orgslug, course, trailData }: CourseActionsProps) {
  const { t, i18n } = useTranslation()
  const router = useRouter()
  const session = useLHSession() as any
  const [isActionLoading, setIsActionLoading] = useState(false)
  const [isContributeLoading, setIsContributeLoading] = useState(false)
  const { contributorStatus, refetch } = useContributorStatus(courseuuid)
  const [isProgressOpen, setIsProgressOpen] = useState(false)
  // Which gateway's checkout is currently in flight ('zarinpal' | 'irankish' | null).
  const [checkoutLoading, setCheckoutLoading] = useState<'zarinpal' | 'irankish' | null>(null)
  const org = useOrg() as any
  const { isUserPartOfTheOrg } = useOrgMembership()
  const queryClient = useQueryClient()
  const { track } = useLHAnalytics('learner')
  const searchParams = useSearchParams()

  // The course's single shared price (in Iranian Rial), if the admin set one.
  // Every active gateway charges this same amount.
  const coursePriceIrr = readCoursePrice(course)

  // Which gateways are active (configured) on this instance. The buyer chooses
  // one of these at payment time. Public probe: no auth needed.
  const { data: gatewaysResult } = useQuery({
    queryKey: ['payments', 'active-gateways'],
    queryFn: () => getActiveGateways(),
    staleTime: 60_000,
  })
  const activeGateways: string[] = (() => {
    const list = (gatewaysResult as any)?.data?.active ?? (gatewaysResult as any)?.active
    return Array.isArray(list) ? (list as string[]) : []
  })()

  // Both gateways redirect the buyer back to the course page with
  // ?payment=completed|failed (see the backend callbacks). Surface that result
  // once, then clear it from the URL.
  useEffect(() => {
    const payment = searchParams?.get('payment')
    if (!payment) return
    if (payment === 'completed') {
      toast.success(t('courses.payment_success'))
    } else if (payment === 'failed') {
      toast.error(t('courses.payment_failed'))
    }
    if (org?.id) queryClient.invalidateQueries({ queryKey: queryKeys.trail.org(org.id) })
    const url = new URL(window.location.href)
    url.searchParams.delete('payment')
    window.history.replaceState({}, '', url.toString())
    // Intentionally run once on mount for the incoming redirect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Clean up course UUID by removing 'course_' prefix if it exists
  const cleanCourseUuid = course.course_uuid?.replace('course_', '');
  const resourceUuid = cleanCourseUuid ? `course_${cleanCourseUuid}` : null;

  const isStarted = trailData?.runs?.find(
    (run: any) => {
      const cleanRunCourseUuid = run.course?.course_uuid?.replace('course_', '');
      return cleanRunCourseUuid === cleanCourseUuid;
    }
  ) ?? false;

  // Public endpoint: no auth needed, works for unauthenticated visitors too
  const { data: offersResult, isLoading } = useQuery({
    queryKey: ['offers', 'by-resource', org?.id, resourceUuid],
    queryFn: () => getOffersByResource(org.id, resourceUuid!),
    enabled: !!org && !!resourceUuid,
    staleTime: 60_000,
  });
  const linkedOffers: any[] = asArray(offersResult);

  const handleCourseAction = async () => {
    if (!session.data?.user) {
      track(AnalyticsEvent.CourseSignupPrompted, {
        reason: 'unauthenticated',
        intended_action: isStarted ? 'leave_course' : 'start_course',
      })
      router.push(getUriWithOrg(orgslug, '/signup'))
      return
    }

    // Check if user is part of the organization
    if (!isUserPartOfTheOrg) {
      router.push(getUriWithOrg(orgslug, '/signup'))
      return
    }

    setIsActionLoading(true)
    const loadingToast = toast.loading(
      isStarted ? t('courses.leave_course') + '...' : t('courses.start_course') + '...'
    )

    try {
      if (isStarted) {
        await removeCourse('course_' + courseuuid, orgslug, session.data?.tokens?.access_token)
        if (org?.id) queryClient.invalidateQueries({ queryKey: queryKeys.trail.org(org.id) })
        track(AnalyticsEvent.CourseLeft, { course_uuid: cleanCourseUuid })
        toast.success(t('courses.leave_course_success'), { id: loadingToast })
      } else {
        await startCourse('course_' + courseuuid, orgslug, session.data?.tokens?.access_token)
        if (org?.id) queryClient.invalidateQueries({ queryKey: queryKeys.trail.org(org.id) })
        track(AnalyticsEvent.CourseStarted, {
          course_uuid: cleanCourseUuid,
          total_activities: course.chapters?.reduce((acc: number, chapter: any) => acc + chapter.activities.length, 0) || 0,
          has_offers: linkedOffers.length > 0,
        })
        toast.success(t('courses.start_course_success'), { id: loadingToast })

        // Get the first activity from the first chapter
        const firstChapter = course.chapters?.[0]
        const firstActivity = firstChapter?.activities?.[0]

        if (firstActivity) {
          // Redirect to the first activity
          router.push(
            getUriWithOrg(orgslug, '') +
            `/course/${courseuuid}/activity/${firstActivity.activity_uuid.replace('activity_', '')}`
          )
        }
      }
    } catch (error) {
      // 402 = this course is purchasable and the user has no entitlement yet.
      // Surface a purchase prompt instead of a generic error, and refresh the
      // linked-offers query so the buy UI renders.
      if ((error as any)?.status === 402) {
        track(AnalyticsEvent.CourseSignupPrompted, {
          reason: 'purchase_required',
          intended_action: 'start_course',
        })
        if (org?.id) {
          queryClient.invalidateQueries({ queryKey: ['offers', 'by-resource', org.id, resourceUuid] })
        }
        toast.error(t('courses.purchase_required'), { id: loadingToast })
      } else {
        console.error('Failed to perform course action:', error)
        toast.error(
          isStarted
            ? t('courses.leave_course_error')
            : t('courses.start_course_error'),
          { id: loadingToast }
        )
      }
    } finally {
      setIsActionLoading(false)
    }
  }

  // Shared guard for either gateway: must be a signed-in org member. Returns
  // false (and redirects) when the buyer can't check out.
  const ensureCanCheckout = (): boolean => {
    if (!session.data?.user) {
      track(AnalyticsEvent.CourseSignupPrompted, {
        reason: 'unauthenticated',
        intended_action: 'purchase_course',
      })
      router.push(getUriWithOrg(orgslug, '/signup'))
      return false
    }
    if (!isUserPartOfTheOrg) {
      router.push(getUriWithOrg(orgslug, '/signup'))
      return false
    }
    return true
  }

  const handleZarinPalCheckout = async () => {
    if (!ensureCanCheckout()) return

    setCheckoutLoading('zarinpal')
    const loadingToast = toast.loading(t('courses.payment_redirecting'))
    try {
      const res = await startZarinPalCheckout(
        org.id,
        Number((course as any).id),
        session.data?.tokens?.access_token
      )
      if (res?.payment_url) {
        // Hand the browser off to ZarinPal's hosted payment page (GET redirect).
        window.location.href = res.payment_url
        return
      }
      toast.error(t('courses.payment_start_error'), { id: loadingToast })
      setCheckoutLoading(null)
    } catch (error) {
      console.error('Failed to start ZarinPal checkout:', error)
      toast.error((error as any)?.message || t('courses.payment_start_error'), { id: loadingToast })
      setCheckoutLoading(null)
    }
  }

  const handleIranKishCheckout = async () => {
    if (!ensureCanCheckout()) return

    setCheckoutLoading('irankish')
    const loadingToast = toast.loading(t('courses.payment_redirecting'))
    try {
      const res = await startIranKishCheckout(
        org.id,
        Number((course as any).id),
        session.data?.tokens?.access_token
      )
      if (res?.payment_url && res?.form_fields) {
        // IranKish requires the browser to POST the token to its payment page,
        // so build and auto-submit a form rather than a GET redirect.
        toast.dismiss(loadingToast)
        postToGateway(res.payment_url, res.form_fields as Record<string, string>)
        return
      }
      toast.error(t('courses.payment_start_error'), { id: loadingToast })
      setCheckoutLoading(null)
    } catch (error) {
      console.error('Failed to start IranKish checkout:', error)
      toast.error((error as any)?.message || t('courses.payment_start_error'), { id: loadingToast })
      setCheckoutLoading(null)
    }
  }

  const handleApplyToContribute = async () => {
    if (!session.data?.user) {
      router.push(getUriWithOrg(orgslug, '/signup'))
      return
    }

    setIsContributeLoading(true)
    const loadingToast = toast.loading(t('courses.submitting_contributor_application'))

    try {
      const data = {
        message: "I would like to contribute to this course."
      }

      await applyForContributor('course_' + courseuuid, data, session.data?.tokens?.access_token)
      await revalidateTags(['courses'], orgslug)
      await refetch()
      track(AnalyticsEvent.ContributorApplicationSubmitted, { course_uuid: cleanCourseUuid })
      toast.success(t('courses.contributor_application_success'), { id: loadingToast })
    } catch (error) {
      console.error('Failed to apply as contributor:', error)
      toast.error(t('courses.contributor_application_error'), { id: loadingToast })
    } finally {
      setIsContributeLoading(false)
    }
  }

  const renderActionButton = (action: 'start' | 'leave') => {
    if (!session.data?.user) {
      return (
        <>
          <UserAvatar width={24} predefined_avatar="empty" rounded="rounded-full" border="border-2" borderColor="border-white" />
          <span>{action === 'start' ? t('courses.start_course') : t('courses.leave_course')}</span>
          <ArrowRight className="w-5 h-5" />
        </>
      );
    }

    return (
      <>
        <UserAvatar
          width={24}
          use_with_session={true}
          rounded="rounded-full"
          border="border-2"
          borderColor="border-white"
        />
        <span>{action === 'start' ? t('courses.start_course') : t('courses.leave_course')}</span>
        <ArrowRight className="w-5 h-5" />
      </>
    );
  };

  const renderContributorButton = () => {
    if (contributorStatus === 'INACTIVE' || course.open_to_contributors !== true) {
      return null;
    }

    if (!session.data?.user) {
      return (
        <button
          onClick={() => router.push(getUriWithOrg(orgslug, '/signup'))}
          aria-label={t('auth.sign_up_to_contribute')}
          className="w-full bg-white text-neutral-700 border border-neutral-200 py-3 rounded-lg nice-shadow font-semibold hover:bg-neutral-50 transition-colors flex items-center justify-center gap-2 mt-3 cursor-pointer"
        >
          <UserPen className="w-5 h-5" />
          {t('auth.authenticate_to_contribute')}
        </button>
      );
    }

    if (contributorStatus === 'ACTIVE') {
      return (
        <div className="w-full bg-green-50 text-green-700 border border-green-200 py-3 rounded-lg nice-shadow font-semibold flex items-center justify-center gap-2 mt-3">
          <UserPen className="w-5 h-5" />
          {t('courses.you_are_contributor')}
        </div>
      );
    }

    if (contributorStatus === 'PENDING') {
      return (
        <div className="w-full bg-amber-50 text-amber-700 border border-amber-200 py-3 rounded-lg nice-shadow font-semibold flex items-center justify-center gap-2 mt-3">
          <ClockIcon className="w-5 h-5" />
          {t('courses.contributor_application_pending')}
        </div>
      );
    }

    return (
      <button
        onClick={handleApplyToContribute}
        disabled={isContributeLoading}
        aria-label={t('courses.apply_to_contribute')}
        className="w-full bg-white text-neutral-700 py-3 rounded-lg nice-shadow font-semibold hover:bg-neutral-50 transition-colors flex items-center justify-center gap-2 mt-3 cursor-pointer disabled:cursor-not-allowed"
      >
        {isContributeLoading ? (
          <div className="w-5 h-5 border-2 border-neutral-700 border-t-transparent rounded-full animate-spin" />
        ) : (
          <>
            <UserPen className="w-5 h-5" />
            {t('courses.apply_to_contribute')}
          </>
        )}
      </button>
    );
  };

  // One buy button per gateway. The checkout handler differs by gateway (GET
  // redirect vs. POST form), but every button buys at the same shared price.
  const gatewayCheckout: Record<'zarinpal' | 'irankish', () => void> = {
    zarinpal: handleZarinPalCheckout,
    irankish: handleIranKishCheckout,
  };

  // A single card showing the shared price, with a button per ACTIVE gateway so
  // the buyer chooses how to pay. Rendered only when a price is set, the buyer
  // lacks access, and at least one gateway is active.
  const renderBuyCards = () => {
    if (isStarted || coursePriceIrr === null) return null;
    const buyableGateways = activeGateways.filter(
      (g): g is 'zarinpal' | 'irankish' => g === 'zarinpal' || g === 'irankish'
    );
    if (buyableGateways.length === 0) return null;

    const formattedPrice = formatCurrency(coursePriceIrr, 'IRR', i18n.language);
    const anyLoading = checkoutLoading !== null;

    return (
      <>
        <div className="bg-white rounded-xl nice-shadow overflow-hidden">
          <div className="px-4 py-2 flex items-center gap-2 bg-emerald-50">
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700">
              <Banknote size={12} /> {t('courses.purchase_course_title')}
            </span>
          </div>
          <div className="p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <p className="font-bold text-gray-900 text-base leading-snug">
                  {t('courses.purchase_course_title')}
                </p>
                <p className="text-sm text-gray-500 mt-1 leading-relaxed">
                  {t('courses.paid_course_description')}
                </p>
              </div>
              <div className="shrink-0 text-end">
                <div className="text-xl font-black text-gray-900">{formattedPrice}</div>
              </div>
            </div>

            {buyableGateways.length > 1 && (
              <p className="text-xs text-gray-500 mt-3">
                {t('courses.choose_payment_gateway')}
              </p>
            )}

            <div className="mt-2 space-y-2">
              {buyableGateways.map((gatewayKey) => {
                const gatewayLabel = t(`courses.gateways.${gatewayKey}`);
                const isLoadingThis = checkoutLoading === gatewayKey;
                return (
                  <button
                    key={gatewayKey}
                    onClick={gatewayCheckout[gatewayKey]}
                    disabled={anyLoading}
                    aria-label={t('courses.buy_course_with', { gateway: gatewayLabel })}
                    className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl font-bold text-sm transition-colors cursor-pointer bg-emerald-600 hover:bg-emerald-700 text-white disabled:bg-emerald-400 disabled:cursor-not-allowed"
                  >
                    {isLoadingThis ? (
                      <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    ) : (
                      <>
                        {t('courses.buy_course_with', { gateway: gatewayLabel })}
                        <ArrowRight size={13} />
                      </>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </>
    );
  };

  const renderProgressSection = () => {
    const totalActivities = course.chapters?.reduce((acc: number, chapter: any) => acc + chapter.activities.length, 0) || 0;

    // Find the correct run using the cleaned UUID
    const run = trailData?.runs?.find(
      (run: any) => {
        const cleanRunCourseUuid = run.course?.course_uuid?.replace('course_', '');
        return cleanRunCourseUuid === cleanCourseUuid;
      }
    );

    const completedActivities = run?.steps?.filter((step: any) => step.complete)?.length || 0;
    const progressPercentage = totalActivities > 0 ? Math.round((completedActivities / totalActivities) * 100) : 0;

    if (!isStarted) {
      return (
        <div className="relative bg-white nice-shadow rounded-lg overflow-hidden">
          <div
            className="absolute inset-0 opacity-[0.05]"
            style={{
              backgroundImage: 'radial-gradient(circle at center, #101010 1px, transparent 1px)',
              backgroundSize: '12px 12px'
            }}
          />
          <div className="relative p-4">
            <div className="flex items-center gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-4">
                  <div className="relative w-16 h-16">
                    <svg className="w-full h-full transform -rotate-90">
                      <circle
                        cx="32"
                        cy="32"
                        r="28"
                        stroke="#e5e7eb"
                        strokeWidth="6"
                        fill="none"
                      />
                    </svg>
                    <div className="absolute inset-0 flex items-center justify-center">
                      <BookOpen className="w-6 h-6 text-neutral-400" />
                    </div>
                  </div>
                  <div className="flex-1">
                    <div className="text-sm font-medium text-gray-900">{t('courses.ready_to_begin')}</div>
                    <div className="text-sm text-gray-500">
                      {t('courses.start_learning_journey', { count: totalActivities })}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      );
    }

    return (
        <div className="relative bg-white nice-shadow rounded-lg overflow-hidden">
          <div
          className="absolute inset-0 opacity-[0.05]"
          style={{
            backgroundImage: 'radial-gradient(circle at center, #000 1px, transparent 1px)',
            backgroundSize: '24px 24px'
          }}
        />
        <div className="relative p-4">
          <div className="flex items-center gap-4">
            <div className="flex-1">
              <div className="flex items-center gap-4">
                <div className="relative w-16 h-16">
                  <svg className="w-full h-full transform -rotate-90">
                    <circle
                      cx="32"
                      cy="32"
                      r="28"
                      stroke="#e5e7eb"
                      strokeWidth="6"
                      fill="none"
                    />
                    <circle
                      cx="32"
                      cy="32"
                      r="28"
                      stroke="#10b981"
                      strokeWidth="6"
                      fill="none"
                      strokeLinecap="round"
                      strokeDasharray={2 * Math.PI * 28}
                      strokeDashoffset={2 * Math.PI * 28 * (1 - (totalActivities > 0 ? completedActivities / totalActivities : 0))}
                      className="transition-all duration-500 ease-out"
                    />
                  </svg>
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-lg font-bold text-gray-800">
                      {progressPercentage}%
                    </span>
                  </div>
                </div>
                <button
                  onClick={() => {
                    track(AnalyticsEvent.CourseProgressViewed, {
                      course_uuid: cleanCourseUuid,
                      completed_activities: completedActivities,
                      total_activities: totalActivities,
                      progress_percentage: progressPercentage,
                    })
                    setIsProgressOpen(true)
                  }}
                  aria-label={t('courses.view_course_progress', { completed: completedActivities, total: totalActivities })}
                  className="flex-1 text-start hover:bg-neutral-50/50 p-2 rounded-lg transition-colors"
                >
                  <div className="text-sm font-medium text-gray-900">{t('courses.course_progress')}</div>
                  <div className="text-sm text-gray-500">
                    {t('courses.completed_of', { completed: completedActivities, total: totalActivities })}
                  </div>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  };

  if (isLoading) {
    return <div className="animate-pulse h-20 bg-gray-100 rounded-lg nice-shadow" />
  }

  // Show join organization prompt for authenticated users who are not part of the org
  if (session.data?.user && !isUserPartOfTheOrg) {
    return (
      <div className="bg-white shadow-md shadow-gray-300/25 outline outline-1 outline-neutral-200/40 rounded-lg overflow-hidden p-4">
        <div className="space-y-4">
          <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg nice-shadow">
            <div className="flex items-center gap-3">
              <UserPlus className="w-5 h-5 text-amber-800" />
              <h3 className="text-amber-800 font-semibold">{t('courses.join_org_required')}</h3>
            </div>
            <p className="text-amber-700 text-sm mt-1">
              {t('courses.join_org_required_description')}
            </p>
          </div>
          <a
            href={getUriWithOrg(orgslug, '/signup')}
            className="w-full bg-neutral-900 text-white py-3 rounded-lg nice-shadow font-semibold hover:bg-neutral-800 transition-colors flex items-center justify-center gap-2"
          >
            <UserPlus className="w-5 h-5" />
            {t('courses.join_organization')}
          </a>
        </div>
      </div>
    )
  }

  if (linkedOffers.length > 0) {
    // User already enrolled / started: show "you own this" notice + leave button
    if (isStarted) {
      return (
        <div className="bg-white nice-shadow rounded-lg overflow-hidden p-4">
          <div className="space-y-4">
            <div className="p-4 bg-green-50 rounded-lg">
              <div className="flex items-center gap-3">
                <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                <h3 className="text-green-800 font-semibold">{t('courses.you_own_this_course')}</h3>
              </div>
              <p className="text-green-700 text-sm mt-1">
                {t('courses.you_own_this_course_description')}
              </p>
            </div>
            <button
              onClick={handleCourseAction}
              disabled={isActionLoading}
              aria-label={t('courses.leave_course')}
              className="w-full py-3 rounded-lg nice-shadow font-semibold transition-colors flex items-center justify-center gap-2 cursor-pointer bg-red-500 text-white hover:bg-red-600 disabled:bg-red-400"
            >
              {isActionLoading
                ? <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                : renderActionButton('leave')
              }
            </button>
            {renderContributorButton()}
          </div>
        </div>
      )
    }

    // Not enrolled: show all available offers
    return (
      <div className="space-y-3">
        {linkedOffers.length > 1 && (
          <p className="text-xs text-gray-400 font-medium px-1">
            {linkedOffers.length} options available
          </p>
        )}
        {linkedOffers.map((offer: any) => (
          <OfferCard key={offer.offer_id} offer={offer} orgslug={orgslug} />
        ))}
        {renderContributorButton()}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Gateway buy cards: one per priced gateway, when the buyer lacks access */}
      {renderBuyCards()}

    <div className="bg-white shadow-md shadow-gray-300/25 outline outline-1 outline-neutral-200/40 rounded-lg overflow-hidden p-4">
      <div className="space-y-4">
        {/* Progress Section */}
        {renderProgressSection()}

        {/* Start/Leave Course Button */}
        <button
          onClick={handleCourseAction}
          disabled={isActionLoading}
          aria-label={isStarted ? t('courses.leave_course') : t('courses.start_course')}
          className={`w-full py-3 rounded-lg nice-shadow font-semibold transition-colors flex items-center justify-center gap-2 cursor-pointer ${
            isStarted
              ? 'bg-red-500 text-white hover:bg-red-600 disabled:bg-red-400'
              : 'bg-neutral-900 text-white hover:bg-neutral-800 disabled:bg-neutral-700'
          }`}
        >
          {isActionLoading ? (
            <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
          ) : (
            renderActionButton(isStarted ? 'leave' : 'start')
          )}
        </button>

        {/* Contributor Button */}
        {renderContributorButton()}

        {/* Course Progress Modal */}
        <CourseProgress
          course={course}
          orgslug={orgslug}
          isOpen={isProgressOpen}
          onClose={() => setIsProgressOpen(false)}
          trailData={trailData}
        />
      </div>
    </div>
    </div>
  )
}

export default CoursesActions
