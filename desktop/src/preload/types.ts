/**
 * MSB v3 Desktop — Typed IPC Contract
 *
 * This file defines the types for the IPC bridge between main and renderer.
 * The renderer uses window.msb.* which is typed via this interface.
 *
 * Security: every method is a named function (no arbitrary channel access).
 * The preload script exposes exactly these methods via contextBridge.
 */

// --- Result types ---

export interface MsbResult<T = unknown> {
  ok: boolean;
  error?: string;
  data?: T;
}

export interface HealthResult {
  ok: boolean;
  service?: string;
  version?: string;
  ts?: string;
}

export interface IdentityResult {
  ok: boolean;
  config?: {
    version?: string;
    route_count?: number;
    [key: string]: unknown;
  };
}

export interface AttachResult {
  ok: boolean;
  health?: HealthResult;
  identity?: IdentityResult;
  error?: string;
}

export interface Approval {
  id: number;
  kind: string;
  status: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface KillSwitch {
  scope: string;
  armed: boolean;
  created_at?: string;
  [key: string]: unknown;
}

export interface MemoryMessage {
  role: string;
  content: string;
  timestamp?: string;
  source?: 'msb-v3-evidence' | 'vault-knowledge';
}

export interface SearchResult {
  title?: string;
  source?: string;
  text?: string;
  content?: string;
  score?: number;
  source_type?: 'msb-v3-evidence' | 'vault-knowledge';
}

// --- IPC API (exposed via contextBridge) ---

export interface MsbApi {
  /** Attach to the msb-v3 server */
  attach(opts?: { host?: string; port?: string }): Promise<AttachResult>;

  /** Check server health */
  health(): Promise<HealthResult>;

  /** Get cockpit dashboard data */
  cockpit(): Promise<Record<string, unknown>>;

  /** Get pending approvals */
  approvals(): Promise<{ approvals: Approval[] }>;

  /** Approve or reject a pending action */
  approve(id: string, action: 'approve' | 'reject'): Promise<MsbResult>;

  /** Get kill switch status */
  killswitch(): Promise<{ switches: KillSwitch[] }>;

  /** Get conversation memory for a session */
  memory(session?: string, limit?: number): Promise<{ messages: MemoryMessage[] }>;

  /** Search the vault (semantic search via RAG) */
  search(query: string, limit?: number): Promise<{ results: SearchResult[] }>;
}

// Global declaration for the renderer
declare global {
  interface Window {
    msb: MsbApi;
  }
}
