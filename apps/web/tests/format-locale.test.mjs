import { describe, expect, test } from "bun:test";

import { formatDate, formatDateTime, formatNumber, formatTime, intlLocale } from "../lib/format.ts";

describe("intlLocale", () => {
  test("keeps valid tags and forces Latin digits for ar/fa", () => {
    expect(intlLocale("fr")).toBe("fr");
    expect(intlLocale("pt-BR")).toBe("pt-BR");
    expect(intlLocale("ar")).toBe("ar-u-nu-latn");
    expect(intlLocale("fa-IR")).toBe("fa-u-nu-latn");
  });

  test("repairs underscore tags instead of throwing", () => {
    expect(intlLocale("en_US")).toBe("en-US");
    expect(intlLocale("ar_EG")).toBe("ar-u-nu-latn");
  });

  test("falls back to English for missing or malformed tags", () => {
    expect(intlLocale(undefined)).toBe("en");
    expect(intlLocale("")).toBe("en");
    expect(intlLocale("!!")).toBe("en");
  });
});

describe("formatters never throw on a bad locale", () => {
  test("formatDate", () => {
    expect(() => formatDate("2026-01-15T10:00:00Z", "en_US")).not.toThrow();
    expect(() => formatDate("2026-01-15T10:00:00Z", "not a locale")).not.toThrow();
  });

  test("formatNumber", () => {
    expect(() => formatNumber(1234.5, "@@")).not.toThrow();
  });
});

describe("intlLocale cache", () => {
  test("returns the same answer on repeat calls", () => {
    expect(intlLocale("en_GB")).toBe("en-GB");
    expect(intlLocale("en_GB")).toBe("en-GB");
  });

  test("stays correct after many distinct inputs", () => {
    for (let i = 0; i < 200; i++) intlLocale(`x-${i}`);
    expect(intlLocale("ar")).toBe("ar-u-nu-latn");
    expect(intlLocale("fr")).toBe("fr");
  });
});

describe("Persian dates", () => {
  const iso = "2025-03-21T10:05:09Z";
  test("fa uses the Jalali calendar with Persian digits", () => {
    expect(formatDate(iso, "fa")).toBe("۱ فروردین ۱۴۰۴");
    expect(formatDate(iso, "fa-IR", { month: "short", day: "numeric" })).toBe("۱ فروردین");
    expect(formatDateTime(iso, "fa")).toContain("۱۴۰۴");
    expect(formatTime(iso, "fa")).toMatch(/^[۰-۹]{2}:[۰-۹]{2}$/);
  });
  test("other languages stay Gregorian with Latin digits", () => {
    expect(formatDate(iso, "en")).toBe("Mar 21, 2025");
    expect(formatDate(iso, "ar")).toMatch(/2025/);
    expect(formatTime(iso, "en")).toMatch(/^\d{2}:\d{2}$/);
  });
  test("invalid dates render as empty text", () => {
    expect(formatDate("nonsense", "fa")).toBe("");
  });
  test("numbers stay Latin even in fa", () => {
    expect(formatNumber(1234, "fa")).not.toMatch(/[۰-۹]/);
  });
});
