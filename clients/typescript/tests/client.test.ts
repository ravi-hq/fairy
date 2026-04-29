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
