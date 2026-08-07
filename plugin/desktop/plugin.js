/**
 * Agent Batch — desktop plugin.
 * Page at /agent-batch: drop your task list at night, let Hermes phase it,
 * dispatch each phase as parallel GitHub Actions agents, track the PRs.
 *
 * The PLAN (tasks → dependency-ordered phases + shared CONTEXT.md) is written
 * by the Hermes agent into $HERMES_AGENT_BATCH_ROOT/plan.json. This UI stores
 * it via the backend and fires dispatches — the agent does the thinking.
 *
 * Backend: plugin/backend/plugin_api.py (mounted at /api/plugins/agent-batch/).
 */

import {
  Badge,
  Button,
  Input,
  Textarea,
  cn,
  haptic,
  host,
  icons
} from '@hermes/plugin-sdk'
import { useMutation, useQuery, useQueryClient } from '@hermes/plugin-sdk'
import {
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState } from 'react'

const ID = 'agent-batch'
let rest = () => Promise.reject(new Error('plugin not registered'))

/* ------------------------------------------------------------------ hooks */

function usePlan() {
  return useQuery({
    queryKey: [ID, 'plan'],
    queryFn: () => rest('/plan'),
    refetchInterval: 10000
  })
}

function useStatus(repo) {
  return useQuery({
    queryKey: [ID, 'status', repo],
    queryFn: () => rest('/status/' + encodeURIComponent(repo)),
    enabled: !!repo,
    refetchInterval: 15000
  })
}

/* ------------------------------------------------------------- page */

function BatchPage() {
  const qc = useQueryClient()
  const plan = usePlan()
  const [repo, setRepo] = useState('')
  const [tasks, setTasks] = useState('')
  const [model, setModel] = useState('opencode/mimo-v2.5-free')
  const [dispatchMsg, setDispatchMsg] = useState('')

  const p = plan.data && plan.data.repo ? plan.data : null

  const savePlan = useMutation({
    mutationFn: (body) => rest('/plan', { method: 'POST', body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [ID, 'plan'] })
  })

  const dispatchPhase = useMutation({
    mutationFn: (body) => rest('/dispatch', { method: 'POST', body }),
    onSuccess: (d) => {
      setDispatchMsg('Dispatched ' + (d.dispatched || []).length + ' agent(s) 🚀')
      qc.invalidateQueries({ queryKey: [ID, 'status'] })
    }
  })

  const status = useStatus(p ? p.repo : (repo || ''))

  return jsxs('div', {
    className: 'flex h-full flex-col overflow-hidden',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-3 border-b border-(--ui-stroke-secondary) px-4 py-2.5',
        children: [
          jsx('div', { className: 'text-sm font-medium', children: 'Agent Batch' }),
          p
            ? jsxs('div', {
                className: 'flex items-center gap-2',
                children: [
                  jsx(Badge, { variant: 'default', children: p.repo }),
                  jsx(Badge, { variant: 'muted', children: (p.phases || []).length + ' phases' }),
                  jsx(Badge, { variant: 'muted', children: (p.tasks || []).length + ' tasks' })
                ]
              })
            : jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'no plan yet' }),
          jsx('div', { className: 'flex-1' }),
          dispatchMsg && jsx('div', { className: 'text-xs text-(--ui-accent)', children: dispatchMsg })
        ]
      }),

      jsxs('div', {
        className: 'flex min-h-0 flex-1',
        children: [
          /* ---- left: task entry ---- */
          jsxs('div', {
            className: 'flex w-96 shrink-0 flex-col gap-3 overflow-y-auto border-r border-(--ui-stroke-secondary) p-4',
            children: [
              jsx('div', { className: 'text-xs font-medium text-(--ui-text-tertiary)', children: '1 · Define' }),
              jsx(Input, {
                placeholder: 'owner/repo  (e.g. raaaas/hermes-agent-batch)',
                value: repo,
                onChange: (e) => setRepo(e.target.value)
              }),
              jsx(Textarea, {
                rows: 12,
                placeholder: 'One task per line:\nadd tests for the auth module\nfix the flaky e2e test\nupdate README with API docs',
                value: tasks,
                onChange: (e) => setTasks(e.target.value),
                className: 'font-mono text-xs'
              }),
              jsx('div', {
                className: 'flex items-center gap-2',
                children: [
                  jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'model' }),
                  jsx(Input, { value: model, onChange: (e) => setModel(e.target.value), className: 'h-8 text-xs' })
                ]
              }),
              jsx(Button, {
                onClick: () => {
                  haptic('tap')
                  const list = tasks.split('\n').map((s) => s.trim()).filter((s) => s && !s.startsWith('#'))
                  savePlan.mutate({ repo, tasks: list, phases: [list], context: '' })
                },
                disabled: !repo || !tasks.trim(),
                children: 'Save plan (Hermes will re-phase)'
              }),
              jsx('div', {
                className: 'rounded-md border border-(--ui-stroke-secondary) p-2 text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
                children:
                  'The real phasing happens when you ask Hermes: "plan this batch". ' +
                  'Hermes groups tasks by dependency, writes CONTEXT.md with memory + architecture, ' +
                  'and saves the phases — they appear on the right.'
              })
            ]
          }),

          /* ---- right: phases + status ---- */
          jsxs('div', {
            className: 'min-w-0 flex-1 overflow-y-auto p-4',
            children: [
              jsxs('div', {
                className: 'space-y-4',
                children: [
                  jsxs('div', {
                    className: 'space-y-2',
                    children: [
                      jsx('div', { className: 'text-xs font-medium text-(--ui-text-tertiary)', children: '2 · Phases' }),
                      !p
                        ? jsx('div', { className: 'text-sm text-(--ui-text-tertiary)', children: 'Save a plan first.' })
                        : (p.phases || []).map((phase, pi) =>
                            jsxs('div', {
                              key: pi,
                              className: 'rounded-xl border border-(--ui-stroke-secondary) p-3',
                              children: [
                                jsxs('div', {
                                  className: 'flex items-center gap-2',
                                  children: [
                                    jsx(Badge, { variant: pi === 0 ? 'default' : 'outline', children: 'Phase ' + (pi + 1) }),
                                    jsx('div', { className: 'flex-1' }),
                                    jsx(Button, {
                                      size: 'xs',
                                      variant: 'secondary',
                                      onClick: () => {
                                        haptic('tap')
                                        dispatchPhase.mutate({
                                          repo: p.repo,
                                          tasks: phase,
                                          model,
                                          base_branch: 'main'
                                        })
                                      },
                                      children: '▶ Run phase'
                                    })
                                  ]
                                }),
                                jsx('div', {
                                  className: 'mt-2 space-y-1',
                                  children: phase.map((t, ti) =>
                                    jsx('div', {
                                      key: ti,
                                      className: 'flex items-start gap-2 text-sm',
                                      children: [
                                        jsx('span', { className: 'text-(--ui-text-quaternary)', children: '▸' }),
                                        jsx('span', { children: t })
                                      ]
                                    })
                                  )
                                })
                              ]
                            })
                          )
                    ]}),

                  jsxs('div', {
                    className: 'space-y-2',
                    children: [
                      jsx('div', { className: 'text-xs font-medium text-(--ui-text-tertiary)', children: '3 · Open agent PRs' }),
                      !status.data
                        ? jsx('div', { className: 'text-sm text-(--ui-text-tertiary)', children: '—' })
                        : status.data.count === 0
                          ? jsx('div', { className: 'text-sm text-(--ui-text-tertiary)', children: 'no agent PRs open' })
                          : status.data.prs.map((pr) =>
                              jsxs('a', {
                                key: pr.number,
                                href: pr.url,
                                target: '_blank',
                                rel: 'noreferrer',
                                className: 'flex items-center gap-2 rounded-lg border border-(--ui-stroke-secondary) px-3 py-2 text-sm transition-colors hover:border-(--ui-accent)',
                                children: [
                                  jsx('span', { className: 'font-mono text-xs text-(--ui-text-tertiary)', children: '#' + pr.number }),
                                  jsx('span', { className: 'flex-1 truncate', children: pr.title }),
                                  jsx('span', { className: 'font-mono text-[0.6875rem] text-(--ui-text-quaternary)', children: pr.branch })
                                ]
                              })
                            )
                    ]
                  })
                ]
              })
            ]
          })
        ]
      })
    ]
  })
}

/* ---------------------------------------------------------------- plugin */

export default {
  id: ID,
  name: 'Agent Batch',
  register(ctx) {
    rest = ctx.rest
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/agent-batch' },
        render: () => jsx(BatchPage, {})
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: '/agent-batch', label: 'Agent Batch', codicon: 'git-pull-request' }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'agent-batch.open',
          label: 'Open Agent Batch',
          keywords: ['batch', 'agents', 'parallel', 'night', 'tasks'],
          run: () => host.navigate('/agent-batch')
        }
      },
      {
        id: 'chip',
        area: STATUSBAR_AREAS.right,
        order: 110,
        render: () => {
          const plan = usePlan()
          const count = (plan.data && (plan.data.tasks || []).length) || 0
          return jsx('button', {
            type: 'button',
            className:
              'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground',
            onClick: () => host.navigate('/agent-batch'),
            children: count > 0 ? '⚡ batch ' + count : '⚡ batch'
          })
        }
      }
    ])
  }
}
