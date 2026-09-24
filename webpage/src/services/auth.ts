const RAW_BASE = import.meta.env.VITE_POLYDATA_API_BASE_URL || '/wm-api';
const API_BASE = RAW_BASE.endsWith('/') ? RAW_BASE.slice(0, -1) : RAW_BASE;

export type AuthUser = {
  id: number;
  username: string;
  role: 'user' | 'admin';
  forcePasswordChange: boolean;
};

export type AuthSession = {
  enabled: boolean;
  authenticated: boolean;
  user: AuthUser | null;
  csrfToken: string | null;
  allowedScopes: string[];
};

export type ProductApiKey = {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  rateLimitPerMinute?: number;
  dailyQuota?: number;
  createdAt?: string | null;
  expiresAt?: string | null;
  lastUsedAt?: string | null;
  revokedAt?: string | null;
  key?: string;
};

export type AuditEvent = {
  id: number;
  occurredAt: string;
  username?: string | null;
  actorKind: string;
  action: string;
  targetType?: string | null;
  targetId?: string | null;
  result: string;
  requestId?: string | null;
  details: Record<string, unknown>;
};

type AuthErrorBody = {
  error?: string | {
    code?: string;
    message?: string;
  };
  requestId?: string;
};

export class AuthApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;

  constructor(status: number, code: string, message: string, requestId?: string) {
    super(message);
    this.name = 'AuthApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

let csrfToken: string | null = null;

export async function authRequest<T>(
  path: string,
  init: RequestInit = {},
  options: { csrf?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  if (init.body) headers.set('Content-Type', 'application/json');
  if (options.csrf) {
    if (!csrfToken) throw new AuthApiError(403, 'CSRF_MISSING', 'Refresh your session before changing account settings.');
    headers.set('X-CSRF-Token', csrfToken);
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'same-origin',
    headers,
  });
  const data = await response.json().catch(() => ({})) as T & AuthErrorBody;
  if (!response.ok) {
    const structured = typeof data.error === 'object' ? data.error : null;
    throw new AuthApiError(
      response.status,
      structured?.code || 'AUTH_REQUEST_FAILED',
      structured?.message || (typeof data.error === 'string' ? data.error : `Request failed with ${response.status}`),
      data.requestId,
    );
  }
  return data;
}

function rememberSession(session: AuthSession) {
  csrfToken = session.csrfToken || null;
  return session;
}

export async function fetchAuthSession(): Promise<AuthSession> {
  return rememberSession(await authRequest<AuthSession>('/auth/session'));
}

export async function login(username: string, password: string): Promise<AuthSession> {
  const response = await authRequest<Omit<AuthSession, 'enabled' | 'allowedScopes'>>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  return rememberSession({
    enabled: true,
    authenticated: response.authenticated,
    user: response.user,
    csrfToken: response.csrfToken,
    allowedScopes: ['mcp:read', 'operations:read'],
  });
}

export async function logout(): Promise<void> {
  await authRequest<{ status: string }>('/auth/logout', { method: 'POST', body: '{}' }, { csrf: true });
  csrfToken = null;
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await authRequest<{ status: string }>(
    '/auth/password',
    { method: 'POST', body: JSON.stringify({ currentPassword, newPassword }) },
    { csrf: true },
  );
}

export async function fetchApiKeys(): Promise<ProductApiKey[]> {
  const response = await authRequest<{ items: ProductApiKey[] }>('/auth/api-keys');
  return response.items || [];
}

export async function createApiKey(name: string, scopes: string[] = ['operations:read']): Promise<ProductApiKey> {
  const response = await authRequest<{ item: ProductApiKey }>(
    '/auth/api-keys',
    {
      method: 'POST',
      body: JSON.stringify({
        name,
        scopes,
        rateLimitPerMinute: 60,
        dailyQuota: 5000,
      }),
    },
    { csrf: true },
  );
  return response.item;
}

export async function revokeApiKey(id: string): Promise<void> {
  await authRequest<{ status: string }>(`/auth/api-keys/${encodeURIComponent(id)}`, { method: 'DELETE' }, { csrf: true });
}

export async function fetchAuditLog(): Promise<AuditEvent[]> {
  const response = await authRequest<{ items: AuditEvent[] }>('/auth/audit?limit=80');
  return response.items || [];
}
