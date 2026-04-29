import { describe, expect, it } from "vitest";

import { Client, ConflictError, NotFoundError } from "../src/index.js";
import { MockServer, makeAgent } from "./helpers.js";

function newClient(server: MockServer): Client {
  return new Client({
    baseUrl: "http://mock",
    token: "aod_test",
    fetch: server.fetch,
  });
}

describe("agents", () => {
  it("lists agents", async () => {
    const server = new MockServer();
    const agent = makeAgent({ name: "listed" });
    server.json("GET", "/agents", 200, { data: [agent] });
    const agents = await newClient(server).agents.list();
    expect(agents.map((a) => a.name)).toEqual(["listed"]);
  });

  it("creates agents with only the set fields", async () => {
    const server = new MockServer();
    server.json("POST", "/agents", 201, makeAgent({ name: "new" }));
    await newClient(server).agents.create({
      name: "new",
      model: "anthropic/claude-sonnet-4-6",
      runtime: "claude",
      system: "be helpful",
    });
    expect(server.requests[0]?.body).toEqual({
      name: "new",
      model: "anthropic/claude-sonnet-4-6",
      runtime: "claude",
      system: "be helpful",
    });
  });

  it("updates agents with version", async () => {
    const server = new MockServer();
    const agentId = "a1";
    server.json("PUT", `/agents/${agentId}`, 200, makeAgent({ id: agentId, version: 2 }));
    const result = await newClient(server).agents.update(agentId, {
      version: 1,
      name: "renamed",
    });
    expect(result.version).toBe(2);
    expect(server.requests[0]?.body).toEqual({ version: 1, name: "renamed" });
  });

  it("surfaces 409 as ConflictError on stale version", async () => {
    const server = new MockServer();
    server.json("PUT", "/agents/a1", 409, { detail: "stale version" });
    await expect(
      newClient(server).agents.update("a1", { version: 1, name: "x" }),
    ).rejects.toBeInstanceOf(ConflictError);
  });

  it("surfaces 404 as NotFoundError", async () => {
    const server = new MockServer();
    server.json("GET", "/agents/missing", 404, { detail: "not found" });
    await expect(newClient(server).agents.get("missing")).rejects.toBeInstanceOf(
      NotFoundError,
    );
  });

  it("archives agents", async () => {
    const server = new MockServer();
    server.json("POST", "/agents/a1/archive", 200, makeAgent({ id: "a1" }));
    const agent = await newClient(server).agents.archive("a1");
    expect(agent.id).toBe("a1");
  });

  it("lists versions", async () => {
    const server = new MockServer();
    server.json("GET", "/agents/a1/versions", 200, {
      data: [makeAgent({ id: "a1", version: 1 })],
    });
    const versions = await newClient(server).agents.versions("a1");
    expect(versions).toHaveLength(1);
  });
});
