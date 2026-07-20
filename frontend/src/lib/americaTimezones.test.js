import { AMERICA_TIMEZONES, DEFAULT_AMERICA_TIMEZONE, normalizeAmericaTimezone } from "./americaTimezones";

test("sólo ofrece zonas horarias de América", () => {
  expect(AMERICA_TIMEZONES.length).toBeGreaterThan(20);
  expect(AMERICA_TIMEZONES.every((timezone) => timezone.value.startsWith("America/"))).toBe(true);
});

test("reemplaza valores libres o externos por la opción argentina predeterminada", () => {
  expect(normalizeAmericaTimezone("Europe/Madrid")).toBe(DEFAULT_AMERICA_TIMEZONE);
  expect(normalizeAmericaTimezone("texto libre")).toBe(DEFAULT_AMERICA_TIMEZONE);
  expect(normalizeAmericaTimezone("America/Montevideo")).toBe("America/Montevideo");
});
