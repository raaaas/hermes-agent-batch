/**
 * Agent Batch — Hermes desktop plugin.
 *
 * Parallel AI task orchestrator: paste a task list at night, the Hermes agent
 * phases it (dependencies → parallel groups), each phase dispatches a GitHub
 * Actions workflow that runs one opencode agent per task on its own branch,
 * and opens PRs. This UI is the operator console: tasks in, phases out,
 * runs + PRs tracked.
 *
 * Backend: ~/.hermes/plugins/agent-batch/dashboard/plugin_api.py
 * (mounted at /api/plugins/agent-batch/ — needs plugins.enabled in
 * config.yaml + a gateway restart).
 *
 * Workflow file lives in the repo: .github/workflows/agent-batch.yml
 */

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Textarea,
  Select,
  EmptyState,
  Loader,
  Separator,
  cn,
  host,
  icons
} from '@hermes/plugin-sdk'
import { useMutation, useQuery, useQueryClient } from '@hermes/plugin-sdk'
import {
  ROUTES_AREA,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState } from 'react'

const PLUGIN = 'agent-batch'
const API = `/api/plugins/${PLUGIN}`

/* ------------------------------------------------------------------ */
/* API helpers                                                         */
/* ------------------------------------------------------------------ */

async function api(path, method = 'GET', body = undefined) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined
  })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${txt.slice(0, 200)}`)
  }
  return res.json()
}

/* ------------------------------------------------------------------ */
/* Sidebar entry + route                                               */
/* ------------------------------------------------------------------ */

function registerSidebar() {
  host.sidebar.register?.({
    area: SIDEBAR_NAV_AREA,
    id: 'agent-batch',
    label: 'Agent Batch',
    icon: 'GitBranch',
    route: '/agent-batch',
    order: 50
  })
}

function registerRoute() {
  host.routes.register?.({
    area: ROUTES_AREA,
    path: '/agent-batch',
    element: <AgentBatchPage />
  })
}

/* ------------------------------------------------------------------ */
/* Main page                                                           */
/* ------------------------------------------------------------------ */

function AgentBatchPage() {
  const qc = useQueryClient()
  const [tasksText, setTasksText] = useState('')
  const [repo, setRepo] = useState('raaaas/agent-batch')
  const [model, setModel] = useState('opencode/mimo-v2.5-free')
  const [context, setContext] = useState('')
  const [phaseToDispatch, setPhaseToDispatch] = useState(null)

  const { data: plan, isLoading } = useQuery({
    queryKey: ['agent-batch-plan'],
    queryFn: () => api('/plan')
  })

  const { data: runs, refetch: refetchRuns } = useQuery({
    queryKey: ['agent-batch-runs'],
    queryFn: () => api(`/runs?repo=${encodeURIComponent(repo)}`),
    refetchInterval: 30000
  })

  const saveTasks = useMutation({
    mutationFn: () => api('/plan', 'POST', {
      tasks: tasksText.split('\n').map(s => s.trim()).filter(Boolean),
      repo,
      model
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-batch-plan'] })
  })

  const dispatchPhase = useMutation({
    mutationFn: (idx) => api('/dispatch', 'POST', {
      phase: idx,
      repo,
      model,
      context
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-batch-plan'] })
      qc.invalidateQueries({ queryKey: ['agent-batch-runs'] })
      setPhaseToDispatch(null)
    }
  })

  const phases = plan?.phases ?? []
  const phaseStatus = plan?.phase_status ?? []

  return jsxs('div', {
    className: 'flex h-full flex-col gap-4 p-6',
    children: [
      /* header */
      jsxs('div', {
        className: 'flex items-center justify-between',
        children: [
          jsxs('div', {
            children: [
              jsx('h1', { className: 'text-xl font-semibold', children: 'Agent Batch' }),
              jsx('p', { className: 'text-sm text-(--ui-text-tertiary)', children: 'Parallel AI tasks — phase, dispatch, track.' })
            ]
          }),
          jsx(Badge, { variant: 'outline', children: plan?.task_count ? `${plan.task_count} tasks` : 'empty' })
        ]
      }),

      /* task input */
      jsx('div', {
        className: 'rounded-xl border border-(--ui-stroke-secondary) p-4',
        children: jsxs('div', {
          className: 'flex flex-col gap-3',
          children: [
            jsx(Textarea, {
              value: tasksText,
              onChange: (e) => setTasksText(e.target.value),
              placeholder: 'One task per line. Example:\nupdate README with API docs\nadd tests for the auth module\nfix the flaky e2e test',
              rows: 6,
              className: 'w-full'
            }),
            jsxs('div', {
              className: 'flex flex-wrap items-center gap-2',
              children: [
                jsx(Input, {
                  value: repo,
                  onChange: (e) => setRepo(e.target.value),
                  placeholder: 'owner/repo',
                  className: 'w-56'
                }),
                jsx(Input, {
                  value: model,
                  onChange: (e) => setModel(e.target.value),
                  placeholder: 'provider/model',
                  className: 'w-64'
                }),
                jsx(Button, {
                  onClick: () => saveTasks.mutate(),
                  disabled: saveTasks.isPending || !tasksText.trim(),
                  children: saveTasks.isPending ? 'Saving…' : 'Save tasks'
                }),
                jsx(Button, {
                  variant: 'outline',
                  onClick: () => setContext(context ? '' : 'no-context'),
                  children: context ? 'Context set ✓' : 'Context'
                })
              ]
            }),
            context && context !== 'no-context' && jsx(Textarea, {
              value: context,
              onChange: (e) => setContext(e.target.value),
              placeholder: 'Project context / memory to hand each agent…',
              rows: 3,
              className: 'w-full'
            })
          ]
        })
      }),

      /* phases */
      jsx('div', {
        className: 'flex flex-col gap-2',
        children: isLoading
          ? jsx(Loader, {})
          : phases.length === 0
            ? jsx(EmptyState, {
                title: 'No phases yet',
                description: 'Save tasks, then ask Hermes to phase them (dependencies → parallel groups).'
              })
            : phases.map((group, i) => {
                const st = phaseStatus[i] ?? 'pending'
                const variant = st === 'done' ? 'default' : st === 'running' ? 'warn' : 'outline'
                return jsxs('div', {
                  key: i,
                  className: 'rounded-xl border border-(--ui-stroke-secondary) p-3',
                  children: [
                    jsxs('div', {
                      className: 'flex items-center justify-between',
                      children: [
                        jsxs('div', {
                          className: 'flex items-center gap-2',
                          children: [
                            jsx(Badge, { variant: 'muted', children: `Phase ${i + 1}` }),
                            jsx(Badge, { variant, children: st }),
                            jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: `${group.length} task(s) — parallel` })
                          ]
                        }),
                        jsx(Button, {
                          size: 'sm',
                          onClick: () => setPhaseToDispatch(i),
                          disabled: st === 'running',
                          children: 'Dispatch'
                        })
                      ]
                    }),
                    jsx('ul', {
                      className: 'mt-2 space-y-1 text-sm',
                      children: group.map((t, j) => jsx('li', {
                        key: j,
                        className: 'flex items-center gap-2',
                        children: [
                          jsx(icons.ChevronRight, { className: 'h-3.5 w-3.5 text-(--ui-text-quaternary)' }),
                          jsx('span', { children: t })
                        ]
                      }))
                    })
                  ]
                }, i)
              })
      }),

      /* runs + PRs */
      jsx('div', {
        className: 'flex flex-col gap-3',
        children: [
          jsxs('div', {
            className: 'flex items-center justify-between',
            children: [
              jsx('h2', { className: 'text-sm font-medium', children: 'GitHub activity' }),
              jsx(Button, { variant: 'ghost', size: 'sm', onClick: () => refetchRuns(), children: 'Refresh' })
            ]
          }),
          jsx('div', {
            className: 'grid grid-cols-1 gap-2 md:grid-cols-2',
            children: [
              jsx('div', {
                className: 'rounded-xl border border-(--ui-stroke-secondary) p-3',
                children: jsxs('div', {
                  className: 'flex flex-col gap-1.5',
                  children: [
                    jsx('h3', { className: 'text-xs font-medium text-(--ui-text-secondary)', children: 'Workflow runs' }),
                    (runs?.runs ?? []).slice(0, 6).map(r => jsxs('div', {
                      className: 'flex items-center justify-between gap-2 text-xs',
                      children: [
                        jsx('span', { className: 'truncate', children: `${r.name ?? 'run'} · ${r.head_branch ?? ''}` }),
                        jsx(Badge, {
                          variant: r.conclusion === 'success' ? 'default' : r.status === 'in_progress' ? 'warn' : 'muted',
                          children: r.conclusion ?? r.status
                        })
                      ]
                    }, r.id))
                  ]
                })
              }),
              jsx('div', {
                className: 'rounded-xl border border-(--ui-stroke-secondary) p-3',
                children: jsxs('div', {
                  className: 'flex flex-col gap-1.5',
                  children: [
                    jsx('h3', { className: 'text-xs font-medium text-(--ui-text-secondary)', children: 'Open PRs' }),
                    (runs?.prs ?? []).slice(0, 6).map(p => jsxs('a', {
                      href: p.html_url,
                      target: '_blank',
                      rel: 'noreferrer',
                      className: 'flex items-center justify-between gap-2 text-xs hover:text-(--ui-accent)',
                      children: [
                        jsx('span', { className: 'truncate', children: `#${p.number} ${p.title}` }),
                        jsx('span', { className: 'shrink-0 text-(--ui-text-quaternary)', children: p.head })
                      ]
                    }, p.number))
                  ]
                })
              })
            ]
          })
        ]
      }),

      /* dispatch confirm dialog */
      phaseToDispatch !== null && jsx(Dialog, {
        open: true,
        onOpenChange: (open) => { if (!open) setPhaseToDispatch(null) },
        children: jsx(DialogContent, {
          children: jsxs('div', {
            className: 'flex flex-col gap-4',
            children: [
              jsx(DialogHeader, {
                children: [
                  jsx(DialogTitle, { children: `Dispatch phase ${phaseToDispatch + 1}?` }),
                  jsx(DialogDescription, {
                    children: `Launches ${phases[phaseToDispatch]?.length ?? 0} agent(s) in parallel on ${repo} — each on its own branch, then opens PRs.`
                  })
                ]
              }),
              jsx(DialogFooter, {
                children: [
                  jsx(Button, { variant: 'outline', onClick: () => setPhaseToDispatch(null), children: 'Cancel' }),
                  jsx(Button, {
                    onClick: () => dispatchPhase.mutate(phaseToDispatch),
                    disabled: dispatchPhase.isPending,
                    children: dispatchPhase.isPending ? 'Dispatching…' : 'Dispatch'
                  })
                ]
              })
            ]
          })
        })
      })
    ]
  })
}

/* ------------------------------------------------------------------ */
/* Registration                                                        */
/* ------------------------------------------------------------------ */

export default {
  id: PLUGIN,
  activate(ctx) {
    registerSidebar()
    registerRoute()
    return () => {}
  }
}
