import { describe, expect, it } from "vitest";
import { parsePairingLink } from "../src/lib/connection";
import { pairingLink, pairingTarget } from "@janus/client";

describe("parsePairingLink", () => {
  it("reads the link the PC's QR code encodes", () => {
    expect(parsePairingLink("https://abc.trycloudflare.com?pairing_code=A1B2C3")).toEqual({ host: "https://abc.trycloudflare.com", code: "A1B2C3" });
  });

  it("accepts a trailing slash and a path before the query, keeping only the origin", () => {
    expect(parsePairingLink("https://abc.trycloudflare.com/?pairing_code=A1B2C3")?.host).toBe("https://abc.trycloudflare.com");
    expect(parsePairingLink("https://abc.trycloudflare.com/some/path?pairing_code=A1B2C3")?.host).toBe("https://abc.trycloudflare.com");
  });

  it("assumes https when the scheme is left out", () => {
    expect(parsePairingLink("abc-123.trycloudflare.com/?pairing_code=a1b2c3")).toEqual({ host: "https://abc-123.trycloudflare.com", code: "A1B2C3" });
  });

  it("allows loopback HTTP for local browser testing but requires HTTPS remotely", () => {
    expect(parsePairingLink("http://127.0.0.1:8000?pairing_code=000FFF")).toEqual({ host: "http://127.0.0.1:8000", code: "000FFF" });
    expect(parsePairingLink("http://192.168.1.20:8000?pairing_code=000FFF")).toBeNull();
  });

  it("uppercases a lowercase code and ignores surrounding spaces", () => {
    expect(parsePairingLink("  https://pc.example?pairing_code=abcdef \n")?.code).toBe("ABCDEF");
  });

  it("ignores other query parameters", () => {
    expect(parsePairingLink("https://pc.example?x=1&pairing_code=A1B2C3&y=2")?.code).toBe("A1B2C3");
  });

  it("reads the twelve-character JSON QR emitted by scoped Studio pairing", () => {
    expect(parsePairingLink(JSON.stringify({ host: "https://pc.example", code: "a1b2c3d4e5f6" }))).toEqual({ host: "https://pc.example", code: "A1B2C3D4E5F6" });
  });

  it("round-trips the same target through a pasted link and a PWA launch link", () => {
    const target = pairingTarget("pc.example/", " a1b2c3d4e5f6 ");
    expect(target).toEqual({ host: "https://pc.example", code: "A1B2C3D4E5F6" });
    expect(parsePairingLink(pairingLink(target))).toEqual(target);
    const appLink = pairingLink(target, "https://phone.example");
    expect(new URL(appLink).origin).toBe("https://phone.example");
    expect(parsePairingLink(appLink)).toEqual(target);
    expect(appLink).not.toContain("token");
  });

  it.each([
    '{"host":"https://pc.example","code":12}',
    '{"host":"https://pc.example"}',
    '{"host":"https://pc.example","code":"short"}',
    'https://user:secret@pc.example?pairing_code=A1B2C3D4E5F6',
    'https://pc.example?pairing_code=A1B2C3D4E5F6&pairing_code=000000000000',
    'https://phone.example?backend=https://pc.example&backend=https://other.example&pairing_code=A1B2C3D4E5F6',
    'https://phone.example?backend=http://remote.example&pairing_code=A1B2C3D4E5F6',
    'https://pc.example?pairing_code=A1B2C3D4E5F6#token=secret',
  ])("rejects malformed or ambiguous pairing data: %s", (value) => {
    expect(parsePairingLink(value)).toBeNull();
  });

  it.each([
    ["nothing", ""],
    ["only spaces", "   "],
    ["a link with no code", "https://pc.example"],
    ["an empty code", "https://pc.example?pairing_code="],
    ["a code that is too short", "https://pc.example?pairing_code=A1B2C"],
    ["a code that is too long", "https://pc.example?pairing_code=A1B2C3D"],
    ["a code with characters outside 0-F", "https://pc.example?pairing_code=A1B2CG"],
    ["the code under another name", "https://pc.example?code=A1B2C3"],
    ["a scheme that is not http or https", "ftp://pc.example?pairing_code=A1B2C3"],
    ["a javascript link", "javascript:alert(1)"],
    ["text that is not a link", "not a link"],
  ])("rejects %s", (_name, text) => {
    expect(parsePairingLink(text)).toBeNull();
  });
});
