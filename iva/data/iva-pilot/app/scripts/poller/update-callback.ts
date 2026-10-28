export type UpdateCallbackAction = { action: "do" } | { action: "skip" };

const UPDATE_CALLBACK_PREFIX = "iva_update:";

export function parseUpdateCallbackData(
  data: unknown,
): UpdateCallbackAction | null {
  if (data === `${UPDATE_CALLBACK_PREFIX}do`) return { action: "do" };
  if (data === `${UPDATE_CALLBACK_PREFIX}skip`) return { action: "skip" };
  return null;
}
