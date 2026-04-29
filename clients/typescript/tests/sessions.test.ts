import { describe, expect, it } from "vitest";

import { Client, ConflictError, RateLimitError } from "../src/index.js";
import { MockServer, makeSession, uuid } from "./helpers.js";

function newClient(server: MockServer): Client {
  return new Client({
    baseUrl: "http://mock",
    token: "aod_test",
    fetch: server.fetch,
  });
}

describe("sessions", () => {
  it("creates a session with resources", async () => {
    const server = new MockServer();
    const sid = uuid();
    server.json("POST", "/sessions", 201, { id: sid, status: "pending" });
    const ack = await newClient(server).sessions.create({
      agent_id: "a1",
      prompt: "do a thing",
      resources: [
        { type: "github_repository", url: "https://github.com/me/repo" },
      ],
    });
    expect(ack.id).toBe(sid);
    expect(ack.status).toBe("pending");
    expect(server.requests[0]?.body).toEqual({
      agent_id: "a1",
      prompt: "do a thing",
      resources: [{ type: "github_repository", url: "https://github.com/me/repo" }],
    });
  });

  it("forwards authorization_token on a private-repo resource", async () => {
    const server = new MockServer();
    const sid = uuid();
    server.json("POST", "/sessions", 201, { id: sid, status: "pending" });
    await newClient(server).sessions.create({
      agent_id: "a1",
      prompt: "do a thing",
      resources: [
        {
          type: "github_repository",
          url: "https://github.com/me/private",
          authorization_token: "ghp_secret",
        },
      ],
    });
    expect(server.requests[0]?.body).toEqual({
      agent_id: "a1",
      prompt: "do a thing",
      resources: [
        {
          type: "github_repository",
          url: "https://github.com/me/private",
          authorization_token: "ghp_secret",
        },
      ],
    });
  });

  it("409 on prompt to a terminal session", async () => {
    const server = new MockServer();
    server.json("POST", "/sessions/s1/prompt", 409, { detail: "terminal" });
    await expect(
      newClient(server).sessions.prompt("s1", { prompt: "hi" }),
    ).rejects.toBeInstanceOf(ConflictError);
  });

  it("429 yields RateLimitError with limit/active", async () => {
    const server = new MockServer();
    server.json("POST", "/sessions", 429, {
      detail: "limit reached",
      limit: 5,
      active: 5,
    });
    const err = await newClient(server)
      .sessions.create({ agent_id: "a1", prompt: "go" })
      .catch((e) => e);
    expect(err).toBeInstanceOf(RateLimitError);
    expect((err as RateLimitError).limit).toBe(5);
    expect((err as RateLimitError).active).toBe(5);
  });

  it("lists turns", async () => {
    const server = new MockServer();
    server.json("GET", "/sessions/s1/turns", 200, {
      data: [
        {
          turn_number: 1,
          prompt: "hi",
          status: "completed",
          exit_code: 0,
          created_at: new Date().toISOString(),
          started_at: null,
          ended_at: null,
        },
      ],
    });
    const turns = await newClient(server).sessions.turns("s1");
    expect(turns).toHaveLength(1);
    expect(turns[0]?.turn_number).toBe(1);
  });

  it("get returns full session", async () => {
    const server = new MockServer();
    const session = makeSession({ id: "s1", status: "running" });
    server.json("GET", "/sessions/s1", 200, session);
    const got = await newClient(server).sessions.get("s1");
    expect(got.status).toBe("running");
  });

  it("terminate returns ack", async () => {
    const server = new MockServer();
    server.json("POST", "/sessions/s1/terminate", 200, {
      id: "s1",
      status: "terminated",
    });
    const ack = await newClient(server).sessions.terminate("s1");
    expect(ack.status).toBe("terminated");
  });

  it("passes since through as a query param on stream", async () => {
    const server = new MockServer();
    const sid = uuid();
    server.register("GET", `/sessions/${sid}/stream`, () => {
      const body = 'data: {"type":"start"}\n\n';
      return new Response(body, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });

    const stream = await newClient(server).sessions.stream(sid, { since: 42 });
    for await (const _ of stream) {
      break;
    }
    await stream.close();

    expect(server.requests[0]?.query.get("since")).toBe("42");
  });
});
