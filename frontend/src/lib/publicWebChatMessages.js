export const publicMessagesOnly = (items = []) => items.filter(
  (item) => ["contact", "bot", "agent"].includes(item?.sender_type)
);

export function mergePublicMessages(current = [], incoming = []) {
  const safeIncoming = publicMessagesOnly(incoming);
  const confirmedClientIds = new Set(
    safeIncoming.map((item) => item.client_message_id).filter(Boolean)
  );
  const merged = new Map();
  publicMessagesOnly(current)
    .filter((item) => !(
      item.id?.startsWith("temp_") && confirmedClientIds.has(item.client_message_id)
    ))
    .forEach((item) => merged.set(item.id, item));
  safeIncoming.forEach((item) => merged.set(item.id, item));
  const result = Array.from(merged.values())
    .sort((a, b) => {
      const byDate = new Date(a.created_at || 0) - new Date(b.created_at || 0);
      return byDate || String(a.id).localeCompare(String(b.id));
    })
    .slice(-250);
  const unchanged = result.length === current.length && result.every((item, index) => {
    const previous = current[index];
    return previous?.id === item.id
      && previous?.body === item.body
      && previous?.delivery_status === item.delivery_status;
  });
  return unchanged ? current : result;
}
