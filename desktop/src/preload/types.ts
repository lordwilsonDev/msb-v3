/**
 * MSB v3 Desktop - Typed IPC Contract
 *
 * Shapes match msb-v3 v0.3.x REST responses, verified live 2026-08-28.
 * The renderer consumes `window.msb.*`; the main process is the authority
 * and re-validates every payload.
 */

// --- result envelope --------------------------------------------------

/** Every IPC call resolves to this shape and never rejects. */
export interface Result<T = unknown> {
  ok: boolean;
  error?: string;
  detail?: string;
  data?: T;
}

export type RuntimeState = 'READY' | 'DEGRADED' | 'OFFLINE' | 'BLOCKED' | 'NOT_ATTACHED';

// --- domain types ---------------------------------------------------

export interface Health {
  ok?: boolean;
  status?: string;
  [k: string]: unknown;
}

/** GET /status */
export interface Identity {
  service?: string;
  version?: string;
  ready: boolean;
  model?: string;
  host?: string;
  port?: number;
  /** true when service === "msb-v3" - i.e. the expected runtime. */
  expected: boolean;
}

export interface AttachResult {
  ok: boolean;
  state: RuntimeState;
  health?: Health;
  identity?: Identity | null;
  operator?: boolean;
  error?: string;
  detail?: string;
}

/** GET /governance/approvals -> { items: Approval[] } */
export interface Approval {
  id: string;
  kind: string;
  title?: string;
  status: string;
  created_at?: string;
  evidence_refs?: string[];
}

/** GET /governance/status */
export interface GovernanceStatus {
  killswitch: {
    armed?: boolean;
    scopes?: Record<string, unknown>;
    [k: string]: unknown;
  };
  budgets?: Record<string, unknown>;
  governor?: Record<string, unknown>;
  approvals?: {
    pending: number;
    kinds_requiring_approval: string[];
  };
}

/** GET /memory/{session} */
export interface MemoryMessage {
  role: string;
  content: string;
  ts?: string;
}

export interface MemoryPage {
  session: string;
  messages: MemoryMessage[];
}

/** POST /rag/search */
export interface SearchHit {
  source?: string;
  text?: string;
  score?: number;
  [k: string]: unknown;
}

// --- exposed API ---------------------------------------------------

export interface MsbApi {
  attach(opts?: { host?: string; port?: string }): Promise<AttachResult>;
  health(): Promise<Result<Health>>;
  identity(): Promise<Result<Identity>>;
  cockpit(): Promise<Result<Record<string, unknown>>>;
  governanceStatus(): Promise<Result<GovernanceStatus>>;
  approvals(): Promise<Result<{ items: Approval[] }>>;
  approve(id: string, action: 'approve' | 'reject', reason?: string): Promise<Result>;
  killswitch(): Promise<Result<GovernanceStatus>>;
  killswitchSet(op: 'arm' | 'disarm', reason?: string): Promise<Result>;
  memory(session?: string, limit?: number): Promise<Result<MemoryPage>>;
  search(query: string, limit?: number): Promise<Result<{ results?: SearchHit[]; [k: string]: unknown }>>;
}

declare global {
  interface Window {
    msb: MsbApi;
  }
}
