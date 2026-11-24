import { describe, expect, it } from "vitest";

import { fmtAge, fmtPips, fmtPrice, fmtSigned, fmtUtc, priceDigits } from "./format";

describe("format", () => {
  it("rounds prices like the market quotes them", () => {
    expect(priceDigits("EUR/USD")).toBe(5);
    expect(priceDigits("USD/JPY")).toBe(3);
    expect(fmtPrice(1.0984183549880981, "EUR/USD")).toBe("1.09842");
    expect(fmtPrice(158.91799926757812, "USD/JPY")).toBe("158.918");
    expect(fmtPrice(null, "EUR/USD")).toBe("n/a");
  });

  it("signs numbers with a real minus and no '-0.0'", () => {
    expect(fmtSigned(3.14)).toBe("+3.1");
    expect(fmtSigned(-3.14)).toBe("−3.1");
    expect(fmtSigned(-0.04)).toBe("0.0");
    expect(fmtPips(-12.34)).toBe("−12.3 pips");
  });

  it("formats ages and never shows negative ones", () => {
    expect(fmtAge(-5)).toBe("0s");
    expect(fmtAge(59)).toBe("59s");
    expect(fmtAge(2700)).toBe("45m");
    expect(fmtAge(3 * 3600 + 20 * 60)).toBe("3h 20m");
    expect(fmtAge(49 * 3600)).toBe("2d 1h");
  });

  it("shows times in UTC", () => {
    expect(fmtUtc("2026-11-25T12:34:56+00:00")).toBe("12:34 UTC");
    expect(fmtUtc("2026-11-25T12:34:56+00:00", true)).toBe("12:34:56 UTC");
    expect(fmtUtc(null)).toBe("n/a");
  });
});
