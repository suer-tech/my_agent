/* eslint-disable @typescript-eslint/no-base-to-string, @typescript-eslint/restrict-template-expressions -- preserve legacy provider-value coercion exactly. */
type OpenRouterError = {
  message?: unknown;
  metadata?: { raw?: unknown; provider_name?: unknown };
};
type OpenRouterBody = { error?: OpenRouterError };
type RawProviderError = { error?: unknown; message?: unknown };

/** Разворачивает `metadata.raw` провайдера: строка JSON, объект или уже готовый текст. */
function unwrapRawProvider(raw: unknown): unknown {
  let value: unknown = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      /* raw is not JSON; keep the original string */
    }
  }
  if (!value || typeof value !== "object") return value;
  const rawObject = value as RawProviderError;
  const nested = rawObject.error;
  return (
    (nested && typeof nested === "object"
      ? (nested as RawProviderError).message
      : nested) || rawObject.message
  );
}

export function openrouterErrReason(body: unknown, status: number): unknown {
  // This deliberately mirrors the former JavaScript property accesses.  These
  // values come from a provider response, and coercing malformed shapes into a
  // harmless fallback would change the error shown by setup.
  const error = (body as OpenRouterBody | null | undefined)?.error || {};
  const reason: unknown = error.message || `HTTP ${status}`;
  const raw = error.metadata?.raw;
  if (raw == null) return reason;
  const inner = unwrapRawProvider(raw);
  if (inner == null || !String(inner).trim()) return reason;
  const provider = error.metadata?.provider_name;
  return provider ? `${String(inner)} (${provider})` : String(inner);
}
