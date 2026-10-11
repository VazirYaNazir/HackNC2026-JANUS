import { describe, expect, it } from "vitest";
import { parsePairingLink } from "../src/lib/connection";

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

  it("keeps http and a port for a PC on the local network", () => {
    expect(parsePairingLink("http://192.168.1.20:8000?pairing_code=000FFF")).toEqual({ host: "http://192.168.1.20:8000", code: "000FFF" });
  });

  it("uppercases a lowercase code and ignores surrounding spaces", () => {
    expect(parsePairingLink("  https://pc.example?pairing_code=abcdef \n")?.code).toBe("ABCDEF");
  });

  it("ignores other query parameters", () => {
    expect(parsePairingLink("https://pc.example?x=1&pairing_code=A1B2C3&y=2")?.code).toBe("A1B2C3");
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
