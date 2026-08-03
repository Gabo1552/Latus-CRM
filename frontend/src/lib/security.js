export const safeExternalUrl = (value) => {
  try {
    const parsed = new URL(String(value || ""));
    return parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
};
