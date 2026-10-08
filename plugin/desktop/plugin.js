/**
 * Agent Batch — Hermes desktop plugin (operator console).
 *
 * Paste a task list, phase it into parallel groups, dispatch one phase at a time to a
 * GitHub Actions workflow that gives every task its own agent, branch and PR, and watch
 * the runs and PRs land. Phasing is either Hermes' call (`agent_batch_phase`) or the
 * panel's own "one phase" button — both write the same plan through the plugin backend.
 *
 * Plain ESM: no JSX syntax, `react/jsx-runtime` only. Every request goes through
 * `ctx.rest`, which is scoped to this plugin's namespace and profile by construction.
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
  EmptyState,
  ErrorState,
  Input,
  Loader,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  Textarea,
  icons,
  useMutation,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState } from 'react'

const PLUGIN = 'agent-batch'
const NAV_PATH = '/agent-batch'

/* ------------------------------------------------------------------ */
/* API — ctx.rest, namespace-scoped                                    */
/* ------------------------------------------------------------------ */

function makeApi(ctx) {
  return {
    plan: () => ctx.rest('/plan'),
    runs: (repo) => ctx.rest(`/runs?repo=${encodeURIComponent(repo)}`),
    saveTasks: (payload) => ctx.rest('/plan', { method: 'POST', body: payload }),
    phase: (phases) => ctx.rest('/plan/phase', { method: 'POST', body: { phases } }),
    dispatch: (payload) => ctx.rest('/dispatch', { method: 'POST', body: payload })
  }
}

function failure(err, fallback) {
  const text = String(err && err.message ? err.message : err)
  return text.includes('no repo set') || text.length < 300 ? text : `${text.slice(0, 300)}…`
}

/* ------------------------------------------------------------------ */
/* Main page                                                           */
/* ------------------------------------------------------------------ */

function AgentBatchPage({ api }) {
  const qc = useQueryClient()
  const [tasksText, setTasksText] = useState('')
  // null = untouched, so the field mirrors what the backend already has (which may come
  // from AGENT_BATCH_REPO); any string, including '', is the operator's own override.
  const [repoDraft, setRepoDraft] = useState(null)
  const [modelDraft, setModelDraft] = useState(null)
  const [context, setContext] = useState('')
  const [showContext, setShowContext] = useState(false)
  const [phaseToDispatch, setPhaseToDispatch] = useState(null)

  const { data: plan, isLoading, error: planError } = useQuery({
    queryKey: [PLUGIN, 'plan'],
    queryFn: api.plan
  })

  const repo = repoDraft ?? plan?.repo ?? ''
  const model = modelDraft ?? plan?.model ?? ''
  const tasks = plan?.tasks ?? []
  const phases = plan?.phases ?? []
  const phaseStatus = plan?.phase_status ?? []

  const { data: runs, error: runsError, refetch: refetchRuns } = useQuery({
    queryKey: [PLUGIN, 'runs', repo],
    queryFn: () => api.runs(repo),
    enabled: repo.trim() !== '',
    refetchInterval: 30000
  })

  const saveTasks = useMutation({
    mutationFn: () => {
      const body = { tasks: tasksText.split('\n').map(line => line.trim()).filter(Boolean) }
      if (repoDraft !== null && repoDraft.trim()) body.repo = repoDraft.trim()
      if (modelDraft !== null && modelDraft.trim()) body.model = modelDraft.trim()
      return api.saveTasks(body)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [PLUGIN, 'plan'] })
  })

  // Item 5: the panel can phase without waiting for Hermes — every stored task lands in
  // one parallel group, which is the honest meaning of "no dependencies known yet".
  const phaseAll = useMutation({
    mutationFn: () => api.phase([tasks]),
    onSuccess: () => qc.invalidateQueries({ queryKey: [PLUGIN, 'plan'] })
  })

  const dispatchPhase = useMutation({
    mutationFn: (idx) => api.dispatch({
      phase: idx,
      repo,
      model,
      context: showContext ? context : ''
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [PLUGIN, 'plan'] })
      qc.invalidateQueries({ queryKey: [PLUGIN, 'runs'] })
      setPhaseToDispatch(null)
    }
  })

  const dispatchError = dispatchPhase.error ? failure(dispatchPhase.error) : ''

  return jsxs('div', {
    className: 'flex h-full flex-col gap-4 overflow-y-auto p-6',
    children: [
      /* header */
      jsxs('div', {
        className: 'flex items-center justify-between gap-3',
        children: [
          jsxs('div', {
            children: [
              jsx('h1', { className: 'text-xl font-semibold', children: 'Agent Batch' }),
              jsx('p', {
                className: 'text-sm text-(--ui-text-tertiary)',
                children: 'Task list in, phases out — one agent, branch and PR per task.'
              })
            ]
          }),
          jsx(Badge, { variant: 'outline', children: tasks.length ? `${tasks.length} task(s)` : 'no tasks' })
        ]
      }),

      planError && jsx(ErrorState, {
        title: 'Backend unreachable',
        description: `${failure(planError)} — the Agent Batch dashboard API needs plugins.enabled and a gateway restart.`
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
                  onChange: (e) => setRepoDraft(e.target.value),
                  placeholder: 'owner/repo',
                  'aria-label': 'Repository',
                  className: 'w-56'
                }),
                jsx(Input, {
                  value: model,
                  onChange: (e) => setModelDraft(e.target.value),
                  placeholder: 'provider/model (empty = runner default)',
                  'aria-label': 'Model',
                  className: 'w-72'
                }),
                jsx(Button, {
                  onClick: () => saveTasks.mutate(),
                  disabled: saveTasks.isPending || !tasksText.trim(),
                  children: saveTasks.isPending ? 'Saving…' : 'Save tasks'
                }),
                jsx(Button, {
                  variant: 'outline',
                  onClick: () => setShowContext(v => !v),
                  children: showContext ? 'Hide context' : 'Context'
                })
              ]
            }),
            showContext && jsx(Textarea, {
              value: context,
              onChange: (e) => setContext(e.target.value),
              placeholder: 'Project context / memory handed to every agent…',
              rows: 3,
              className: 'w-full'
            }),
            saveTasks.error && jsx('p', {
              className: 'text-xs text-destructive',
              children: failure(saveTasks.error)
            })
          ]
        })
      }),

      /* phases */
      jsxs('div', {
        className: 'flex flex-col gap-2',
        children: [
          jsxs('div', {
            className: 'flex items-center justify-between',
            children: [
              jsx('h2', { className: 'text-sm font-medium', children: 'Phases' }),
              jsx(Button, {
                size: 'sm',
                variant: 'outline',
                onClick: () => phaseAll.mutate(),
                disabled: phaseAll.isPending || tasks.length === 0,
                children: phaseAll.isPending ? 'Grouping…' : 'Group as one phase'
              })
            ]
          }),
          phaseAll.error && jsx('p', {
            className: 'text-xs text-destructive',
            children: failure(phaseAll.error)
          }),
          isLoading
            ? jsx(Loader, {})
            : phases.length === 0
              ? jsx(EmptyState, {
                  title: 'No phases yet',
                  description: tasks.length
                    ? 'Save tasks, then group them: ask Hermes to phase the list by dependencies (agent_batch_phase), or use "Group as one phase" to run every task in parallel.'
                    : 'Add a task list above, then group it into phases.'
                })
              : phases.map((group, i) => {
                  const st = phaseStatus[i] ?? 'pending'
                  const variant = st === 'running' ? 'warn' : st === 'done' ? 'success' : 'outline'
                  return jsxs('div', {
                    className: 'rounded-xl border border-(--ui-stroke-secondary) p-3',
                    children: [
                      jsxs('div', {
                        className: 'flex items-center justify-between gap-2',
                        children: [
                          jsxs('div', {
                            className: 'flex items-center gap-2',
                            children: [
                              jsx(Badge, { variant: 'muted', children: `Phase ${i + 1}` }),
                              jsx(Badge, { variant, children: st }),
                              jsx('span', {
                                className: 'text-xs text-(--ui-text-tertiary)',
                                children: `${group.length} task(s) in parallel`
                              })
                            ]
                          }),
                          jsx(Button, {
                            size: 'sm',
                            onClick: () => setPhaseToDispatch(i),
                            disabled: st === 'running' || repo.trim() === '',
                            children: 'Dispatch'
                          })
                        ]
                      }),
                      jsx('ul', {
                        className: 'mt-2 space-y-1 text-sm',
                        children: group.map((t, j) => jsxs('li', {
                          className: 'flex items-center gap-2',
                          children: [
                            jsx(icons.ChevronRight, { className: 'h-3.5 w-3.5 shrink-0 text-(--ui-text-quaternary)' }),
                            jsx('span', { children: t })
                          ]
                        }, j))
                      })
                    ]
                  }, i)
                })
        ]
      }),

      /* runs + PRs */
      jsx('div', {
        className: 'flex flex-col gap-3',
        children: jsxs('div', {
          className: 'grid grid-cols-1 gap-2 md:grid-cols-2',
          children: [
            jsxs('div', {
              className: 'rounded-xl border border-(--ui-stroke-secondary) p-3',
              children: [
                jsxs('div', {
                  className: 'flex items-center justify-between',
                  children: [
                    jsx('h3', {
                      className: 'text-xs font-medium text-(--ui-text-secondary)',
                      children: 'Workflow runs'
                    }),
                    jsx(Button, {
                      variant: 'ghost',
                      size: 'sm',
                      onClick: () => refetchRuns(),
                      disabled: repo.trim() === '',
                      children: 'Refresh'
                    })
                  ]
                }),
                repo.trim() === ''
                  ? jsx('p', {
                      className: 'mt-2 text-xs text-(--ui-text-tertiary)',
                      children: 'Set a repo to see its runs.'
                    })
                  : runsError
                    ? jsx('p', { className: 'mt-2 text-xs', children: failure(runsError) })
                    : jsxs('div', {
                        className: 'mt-2 flex flex-col gap-1.5',
                        children: [
                          (runs?.runs ?? []).slice(0, 8).map(r => jsxs('div', {
                            className: 'flex items-center justify-between gap-2 text-xs',
                            children: [
                              jsx('span', {
                                className: 'truncate',
                                children: `${r.name ?? 'run'} · ${r.head_branch ?? ''}`
                              }),
                              jsx(Badge, {
                                variant: r.conclusion === 'success'
                                  ? 'success'
                                  : r.status === 'in_progress' || r.status === 'queued'
                                    ? 'warn'
                                    : r.conclusion
                                      ? 'destructive'
                                      : 'muted',
                                children: r.conclusion ?? r.status ?? '?'
                              })
                            ]
                          }, r.id)),
                          runs?.runs_error && jsx('p', {
                            className: 'text-xs text-(--ui-text-tertiary)',
                            children: runs.runs_error
                          })
                        ]
                      })
              ]
            }, 'runs'),
            jsxs('div', {
              className: 'rounded-xl border border-(--ui-stroke-secondary) p-3',
              children: [
                jsx('h3', {
                  className: 'text-xs font-medium text-(--ui-text-secondary)',
                  children: 'Open PRs'
                }),
                repo.trim() === ''
                  ? jsx('p', {
                      className: 'mt-2 text-xs text-(--ui-text-tertiary)',
                      children: 'Set a repo to see its pull requests.'
                    })
                  : (runs?.prs ?? []).length === 0
                    ? jsx('p', {
                        className: 'mt-2 text-xs text-(--ui-text-tertiary)',
                        children: 'No open PRs.'
                      })
                    : jsx('div', {
                        className: 'mt-2 flex flex-col gap-1.5',
                        children: (runs?.prs ?? []).slice(0, 8).map(p => jsxs('a', {
                          href: p.html_url,
                          target: '_blank',
                          rel: 'noreferrer',
                          className: 'flex items-center justify-between gap-2 text-xs hover:text-(--ui-accent)',
                          children: [
                            jsx('span', { className: 'truncate', children: `#${p.number} ${p.title}` }),
                            jsx('span', {
                              className: 'shrink-0 text-(--ui-text-quaternary)',
                              children: p.head
                            })
                          ]
                        }, p.number))
                      })
              ]
            }, 'prs')
          ]
        })
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
                children: jsxs('div', {
                  children: [
                    jsx(DialogTitle, { children: `Dispatch phase ${phaseToDispatch + 1}?` }),
                    jsx(DialogDescription, {
                      children: `Launches ${phases[phaseToDispatch]?.length ?? 0} agent(s) in parallel on ${repo} — each on its own branch, then opens a PR.`
                    })
                  ]
                })
              }),
              dispatchError && jsx('p', {
                className: 'text-xs',
                children: dispatchError
              }),
              jsx(DialogFooter, {
                children: jsxs('div', {
                  className: 'flex items-center justify-end gap-2',
                  children: [
                    jsx(Button, {
                      variant: 'outline',
                      onClick: () => setPhaseToDispatch(null),
                      children: 'Cancel'
                    }),
                    jsx(Button, {
                      onClick: () => dispatchPhase.mutate(phaseToDispatch),
                      disabled: dispatchPhase.isPending,
                      children: dispatchPhase.isPending ? 'Dispatching…' : 'Dispatch'
                    })
                  ]
                })
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
  name: 'Agent Batch',
  description: 'Phase a task list and dispatch each phase to parallel coding agents via GitHub Actions.',
  defaultEnabled: false,
  register(ctx) {
    const api = makeApi(ctx)
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: NAV_PATH },
        render: () => jsx(AgentBatchPage, { api })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 50,
        data: { codicon: 'git-branch', label: 'Agent Batch', path: NAV_PATH }
      }
    ])
  }
}
