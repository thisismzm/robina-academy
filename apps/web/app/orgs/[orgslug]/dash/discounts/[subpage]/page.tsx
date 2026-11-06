'use client'
import React, { use } from 'react'
import { motion } from 'motion/react'
import { useTranslation } from 'react-i18next'
import { Tag, ChartBar } from '@phosphor-icons/react'
import { Breadcrumbs } from '@components/Objects/Breadcrumbs/Breadcrumbs'
import { getUriWithOrg } from '@services/config/config'
import { DashTabBar, DashTabItem } from '@components/Dashboard/Shared/DashTabBar/DashTabBar'
import DiscountCodesPage from '@components/Dashboard/Pages/Discounts/DiscountCodesPage'
import DiscountReportPage from '@components/Dashboard/Pages/Discounts/DiscountReportPage'

export type DiscountsParams = {
  subpage: string
  orgslug: string
}

function DiscountsPage(props: { params: Promise<DiscountsParams> }) {
  const params = use(props.params)
  const { t } = useTranslation()
  const subpage = params.subpage === 'report' ? 'report' : 'codes'

  const tabs: DashTabItem[] = [
    {
      key: 'codes',
      label: t('dashboard.discounts.tab_codes'),
      icon: <Tag size={16} />,
      href: getUriWithOrg(params.orgslug, '/dash/discounts/codes'),
      active: subpage === 'codes',
    },
    {
      key: 'report',
      label: t('dashboard.discounts.tab_report'),
      icon: <ChartBar size={16} />,
      href: getUriWithOrg(params.orgslug, '/dash/discounts/report'),
      active: subpage === 'report',
    },
  ]

  return (
    <div className="h-screen w-full bg-[#f8f8f8] flex flex-col">
      <div className="ps-4 pe-4 sm:ps-10 sm:pe-10 tracking-tight bg-[#fcfbfc] z-10 nice-shadow flex-shrink-0 relative">
        <div className="pt-6 pb-4">
          <Breadcrumbs
            items={[
              { label: t('dashboard.discounts.title'), href: '/dash/discounts/codes', icon: <Tag size={14} /> },
            ]}
          />
        </div>
        <div className="my-2 py-2 flex flex-col space-y-1 min-w-0">
          <div className="pt-3 flex font-bold text-3xl sm:text-4xl tracking-tighter truncate">
            {subpage === 'report'
              ? t('dashboard.discounts.report.title')
              : t('dashboard.discounts.title')}
          </div>
          <div className="flex font-medium text-gray-400 text-md truncate">
            {subpage === 'report'
              ? t('dashboard.discounts.report.subtitle')
              : t('dashboard.discounts.subtitle')}
          </div>
        </div>
        <DashTabBar tabs={tabs} />
      </div>
      <div className="h-6 flex-shrink-0"></div>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.1, type: 'spring', stiffness: 80 }}
        className="flex-1 overflow-y-auto"
      >
        {subpage === 'codes' && <DiscountCodesPage />}
        {subpage === 'report' && <DiscountReportPage />}
      </motion.div>
    </div>
  )
}

export default DiscountsPage
