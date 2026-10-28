import { describe, expect, it } from "vitest";
import { authHeader, tokenExpiry } from "../auth";

const b64url = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

describe("api auth helpers", () => {
  it("reads exp from a signed API token", () => {
    expect(tokenExpiry(`${b64url({ exp: 1793132167, s: "site_1", t: "join" })}.mac`)).toBe(1793132167);
  });
  it("returns null for anything else", () => {
    for (const t of ["", "operator-token-without-dot", "a.b.c", "!!!.mac", `${b64url({ s: "x" })}.mac`]) expect(tokenExpiry(t)).toBeNull();
  });
  it("sends a bearer header only when there is a token", () => {
    expect(authHeader(null)).toEqual({});
    expect(authHeader("abc")).toEqual({ Authorization: "Bearer abc" });
  });
});
