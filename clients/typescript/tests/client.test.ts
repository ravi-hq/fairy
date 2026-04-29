import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { Client, VERSION } from "../src/index.js";
import { MockServer, makeAgent } from "./helpers.js";

describe("Client", () => {
  const ORIGINAL_ENV = { ...process.env };

  beforeEach(() => {
    delete process.env.AOD_API_URL;
    delete process.env.AOD_API_TOKEN;
  });

  afterEach(() => {
    process.env = { ...ORIGINAL_ENV };
  });

  it("throws without a token", () => {
    expect(() => new Client({ baseUrl: "http://mock" })).toThrow(
      /Missing API token/,
    );
  });

  it("falls back to env vars", async () => {
    process.env.AOD_API_URL = "http://from-env";
    process.env.AOD_API_TOKEN = "aod_from_env";
    const server = new MockServer();
    server.json("GET", "/health", 200, { status: "ok" });
    const client = new Client({ fetch: server.fetch });
    await expect(client.health()).resolves.toEqual({ status: "ok" });
    expect(server.requests[0]?.headers["authorization"]).toBe(
      "Bearer aod_from_env",
    );
  });

  it("sends bearer auth on every request", async () => {
    const server = new MockServer();
    server.json("GET", "/agents", 200, { data: [makeAgent()] });
    const client = new Client({
      baseUrl: "http://mock",
      token: "aod_test",
      fetch: server.fetch,
    });
    await client.agents.list();
    expect(server.requests[0]?.headers["authorization"]).toBe("Bearer aod_test");
  });

  it("removes its abort listener from the external signal after each request", async () => {
    // Pre-fix, every completed request left a closure-capturing `abort`
    // listener on the external signal. Long-lived signals (e.g. a global
    // cancel-all controller) accumulated listeners — and the per-request
    // AbortControllers they pinned — across the lifetime of the signal.
    const server = new MockServer();
    server.json("GET", "/agents", 200, { data: [] });
    const real = new AbortController();
    let added = 0;
    let removed = 0;
    const fakeSignal = {
      aborted: false,
      reason: undefined,
      addEventListener: (type: string, listener: EventListener, opts?: AddEventListenerOptions) => {
        added++;
        real.signal.addEventListener(type, listener, opts);
      },
      removeEventListener: (type: string, listener: EventListener) => {
        removed++;
        real.signal.removeEventListener(type, listener);
      },
    } as unknown as AbortSignal;
    const client = new Client({
      baseUrl: "http://mock",
      token: "aod_test",
      fetch: server.fetch,
    });
    await client.agents.list({ signal: fakeSignal });
    await client.agents.list({ signal: fakeSignal });
    await client.agents.list({ signal: fakeSignal });
    expect(added).toBe(3);
    expect(removed).toBe(3);
  });

  it("returns health payload", async () => {
    const server = new MockServer();
    server.json("GET", "/health", 200, { status: "ok" });
    const client = new Client({
      baseUrl: "http://mock",
      token: "aod_test",
      fetch: server.fetch,
    });
    await expect(client.health()).resolves.toEqual({ status: "ok" });
  });

  it("sets a User-Agent identifying the SDK", async () => {
    const server = new MockServer();
    server.json("GET", "/health", 200, { status: "ok" });
    const client = new Client({
      baseUrl: "http://mock",
      token: "aod_test",
      fetch: server.fetch,
    });
    await client.health();
    expect(server.requests[0]?.headers["user-agent"]).toBe(`aod-sdk-ts/${VERSION}`);
  });

  it("aborts when reading the body exceeds timeoutMs", async () => {
    // Pre-fix, fetch resolved on headers and the body read had no timer,
    // so a slow body would hang the caller for as long as the server held
    // the connection open. Pin: timeoutMs covers parseBody too.
    const fetchFn: typeof fetch = (_input, init) => {
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          init?.signal?.addEventListener("abort", () => {
            controller.error(new DOMException("aborted", "AbortError"));
          });
        },
      });
      return Promise.resolve(
        new Response(stream, {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    };
    const client = new Client({
      baseUrl: "http://mock",
      token: "aod_test",
      fetch: fetchFn,
      timeoutMs: 50,
    });
    const start = Date.now();
    await expect(client.agents.list()).rejects.toMatchObject({ name: "AbortError" });
    expect(Date.now() - start).toBeLessThan(2000);
  });

  it("strips trailing slash from baseUrl", async () => {
    const server = new MockServer();
    server.json("GET", "/health", 200, { status: "ok" });
    const client = new Client({
      baseUrl: "http://mock/",
      token: "aod_test",
      fetch: server.fetch,
    });
    await client.health();
    expect(server.requests[0]?.path).toBe("/health");
  });

  // Catches the easy mistake of bumping `package.json` without bumping the
  // exported `VERSION` constant (the README's release checklist requires both,
  // and the publish workflow only checks the tag against `package.json`).
  it("exports a VERSION matching package.json", () => {
    const pkg = JSON.parse(
      readFileSync(new URL("../package.json", import.meta.url), "utf-8"),
    ) as { version: string };
    expect(VERSION).toBe(pkg.version);
  });
});

describe("module exports", () => {
  // Catches the easy mistake of bumping `package.json` without bumping the
  // exported `VERSION` constant (the README's release checklist requires both,
  // and the publish workflow only checks the tag against `package.json`).
  it("exports a VERSION matching package.json", () => {
    const pkg = JSON.parse(
      readFileSync(new URL("../package.json", import.meta.url), "utf-8"),
    ) as { version: string };
    expect(VERSION).toBe(pkg.version);
  });
});
