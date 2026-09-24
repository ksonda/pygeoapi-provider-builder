// Typed API client (ADR-0011). Every request carries the CSRF header the
// backend requires for state-changing calls (ADR-0012).
import createClient from 'openapi-fetch'

import type { components, paths } from './schema'

export type Schemas = components['schemas']

// The API accepts these with defaults; the wizard always fills them in, so
// its own types make every field required (still assignable to the API's).
type Full<T> = { [K in keyof T]-?: T[K] }
export type ProviderState = Full<Schemas['ProviderState']>
export type ResourceState = Full<Schemas['ResourceState']>
export type FormState = Omit<Full<Schemas['FormState']>, 'providers' | 'resource'> & {
  providers: ProviderState[]
  resource: ResourceState
}
export type Finding = Schemas['Finding']
export type ValidateResult = Schemas['ValidateResult']
export type ExportResult = Schemas['ExportResult']
export type RenderResult = Schemas['RenderResult']
export type EnvVar = Schemas['EnvVar']
export type JobInfo = Schemas['JobInfo']

export const api = createClient<paths>({ baseUrl: '', headers: { 'X-Provider-Builder': '1' } })

export class ApiError extends Error {}

export function unwrap<T>(res: { data?: T; error?: unknown; response: Response }): T {
  if (res.data === undefined) {
    const detail = (res.error as { detail?: unknown } | undefined)?.detail
    throw new ApiError(typeof detail === 'string' ? detail : `Request failed (${res.response.status})`)
  }
  return res.data
}

/** Poll a job until it finishes; resolves with its result. */
export async function waitForJob<T>(jobId: string, signal?: AbortSignal): Promise<T> {
  for (;;) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    const job = unwrap(await api.GET('/api/jobs/{job_id}', { params: { path: { job_id: jobId } } }))
    if (job.status === 'done') return job.result as T
    if (job.status === 'failed') throw new ApiError(job.error ?? 'The job failed')
    await new Promise((r) => setTimeout(r, 300))
  }
}
