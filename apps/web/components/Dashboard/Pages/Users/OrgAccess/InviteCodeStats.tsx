import { useOrg } from '@components/Contexts/OrgContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getAPIUrl } from '@services/config/config'
import { apiFetch } from '@services/utils/ts/requests'
import { queryKeys } from '@/lib/query/keys'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { ChevronDown, ChevronRight } from 'lucide-react'
import React from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatDate, formatDateTime } from '@/lib/format'

type CodeStat = {
  invite_code_uuid: string
  code: string | null
  status: 'active' | 'deleted'
  usergroup_name?: string | null
  total_signups: number
  signups_in_period: number
  last_used: string | null
}

type Stats = {
  period_days: number
  summary: {
    signups_via_invite: number
    new_members: number
    active_codes: number
    total_signups_all_time: number
  }
  codes: CodeStat[]
  timeseries: { date: string; total: number }[]
}

const PERIODS = [7, 30, 90, 365]

function SummaryCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex-1 min-w-[140px] bg-slate-50 rounded-xl px-4 py-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-2xl font-bold text-gray-800 mt-0.5">{value}</div>
    </div>
  )
}

function CodeUsers({ orgId, codeUuid, token }: { orgId: number; codeUuid: string; token: string }) {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.org.inviteCodeUsers(orgId, codeUuid),
    queryFn: () =>
      apiFetch(`${getAPIUrl()}orgs/${orgId}/invites/usage/${encodeURIComponent(codeUuid)}`, token),
    enabled: !!token,
  })

  if (isLoading) return <div className="text-xs text-gray-400 px-2 py-2">Loading...</div>
  if (!data?.length) return <div className="text-xs text-gray-400 px-2 py-2">No users yet.</div>

  return (
    <ul className="divide-y divide-gray-100">
      {data.map((u: any) => (
        <li key={u.user_uuid} className="flex items-center justify-between px-2 py-1.5 text-xs">
          <span className="text-gray-700">
            {[u.first_name, u.last_name].filter(Boolean).join(' ') || u.username}
            <span className="text-gray-400"> @{u.username}</span>
          </span>
          <span className="text-gray-400">{formatDateTime(dayjs(u.signed_up_at), undefined, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })}</span>
        </li>
      ))}
    </ul>
  )
}

function InviteCodeStats() {
  const org = useOrg() as any
  const session = useLHSession() as any
  const token = session?.data?.tokens?.access_token
  const [days, setDays] = React.useState(30)
  const [expanded, setExpanded] = React.useState<string | null>(null)

  const { data, isLoading, isError } = useQuery<Stats>({
    queryKey: queryKeys.org.inviteCodeStats(org?.id, days),
    queryFn: () => apiFetch(`${getAPIUrl()}orgs/${org?.id}/invites/stats?days=${days}`, token),
    enabled: !!org?.id && !!token,
  })

  return (
    <div className="mx-4 sm:mx-10 mt-4 bg-white rounded-xl nice-shadow">
      <div className="px-4 sm:px-6 py-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-bold text-lg text-gray-800">Invite code usage</h2>
          <p className="text-sm text-gray-500 mt-0.5">How many people signed up with each code.</p>
        </div>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="text-sm border border-gray-200 rounded-md px-2 py-1.5 bg-white"
          aria-label="Period"
        >
          {PERIODS.map((p) => (
            <option key={p} value={p}>
              Last {p} days
            </option>
          ))}
        </select>
      </div>

      {isLoading && <div className="p-6 text-sm text-gray-400">Loading...</div>}
      {isError && <div className="p-6 text-sm text-rose-600">Could not load invite code statistics.</div>}

      {data && (
        <div className="p-4 sm:p-6 space-y-6">
          <div className="flex flex-wrap gap-3">
            <SummaryCard label={`Signups with a code (${data.period_days}d)`} value={data.summary.signups_via_invite} />
            <SummaryCard
              label={`Signups without a code (${data.period_days}d)`}
              value={Math.max(data.summary.new_members - data.summary.signups_via_invite, 0)}
            />
            <SummaryCard label="Active codes" value={data.summary.active_codes} />
            <SummaryCard label="All-time code signups" value={data.summary.total_signups_all_time} />
          </div>

          <div className="h-[200px]">
            {data.timeseries.length === 0 ? (
              <div className="h-full flex items-center justify-center text-sm text-gray-400">
                No signups with an invite code in this period.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.timeseries}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" tickFormatter={(d) => formatDate(dayjs(d), undefined, { day: '2-digit', month: 'short' })} fontSize={11} />
                  <YAxis allowDecimals={false} fontSize={11} width={28} />
                  <Tooltip labelFormatter={(d) => formatDate(dayjs(d as string), undefined, { year: 'numeric', month: '2-digit', day: '2-digit' })} />
                  <Bar dataKey="total" name="Signups" fill="#16a34a" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="table-auto w-full text-start whitespace-nowrap">
              <thead className="bg-gray-100 text-gray-500 uppercase text-xs">
                <tr>
                  <th className="py-2 px-3 text-start">Code</th>
                  <th className="py-2 px-3 text-start">Status</th>
                  <th className="py-2 px-3 text-start">User group</th>
                  <th className="py-2 px-3 text-end">Signups ({data.period_days}d)</th>
                  <th className="py-2 px-3 text-end">Total</th>
                  <th className="py-2 px-3 text-start">Last used</th>
                </tr>
              </thead>
              <tbody>
                {data.codes.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-4 px-3 text-sm text-gray-400">
                      No invite codes yet.
                    </td>
                  </tr>
                )}
                {data.codes.map((c) => {
                  const open = expanded === c.invite_code_uuid
                  return (
                    <React.Fragment key={c.invite_code_uuid}>
                      <tr
                        className={`border-b border-gray-100 text-sm ${c.total_signups ? 'cursor-pointer hover:bg-gray-50' : ''}`}
                        onClick={() => c.total_signups && setExpanded(open ? null : c.invite_code_uuid)}
                      >
                        <td className="py-2.5 px-3">
                          <span className="inline-flex items-center gap-1.5">
                            {c.total_signups ? (
                              open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />
                            ) : (
                              <span className="w-3.5" />
                            )}
                            <code className="bg-gray-50 px-2 py-0.5 rounded font-mono">{c.code}</code>
                          </span>
                        </td>
                        <td className="py-2.5 px-3">
                          <span
                            className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                              c.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                            }`}
                          >
                            {c.status === 'active' ? 'Active' : 'Deleted'}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-gray-600">{c.usergroup_name || '-'}</td>
                        <td className="py-2.5 px-3 text-end font-semibold">{c.signups_in_period}</td>
                        <td className="py-2.5 px-3 text-end">{c.total_signups}</td>
                        <td className="py-2.5 px-3 text-gray-600">
                          {c.last_used ? formatDate(dayjs(c.last_used), undefined, { year: 'numeric', month: '2-digit', day: '2-digit' }) : '-'}
                        </td>
                      </tr>
                      {open && (
                        <tr className="bg-gray-50/60">
                          <td colSpan={6} className="px-6 py-2">
                            <CodeUsers orgId={org.id} codeUuid={c.invite_code_uuid} token={token} />
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

export default InviteCodeStats
