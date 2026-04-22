import { describe, expect, it } from "vitest";

import { Client } from "../src/index.js";
import { MockServer, makeEnvironment } from "./helpers.js";

function newClient(server: MockServer): Client {
  return new Client({
    baseUrl: "http://mock",
    token: "aod_test",
    fetch: server.fetch,
  });
}

describe("environments", () => {
  it("creates environments with encrypted env_vars in the payload", async () => {
    const server = new MockServer();
    server.json("POST", "/environments", 201, makeEnvironment());
    await newClient(server).environments.create({
      name: "prod",
      packages: { apt: ["jq"] },
      env_vars: { OPENAI_API_KEY: "sk-..." },
      networking: { type: "limited", allowed_hosts: ["api.github.com"] },
    });
    expect(server.requests[0]?.body).toEqual({
      name: "prod",
      packages: { apt: ["jq"] },
      env_vars: { OPENAI_API_KEY: "sk-..." },
      networking: { type: "limited", allowed_hosts: ["api.github.com"] },
    });
  });

  it("updates with version and merges fields", async () => {
    const server = new MockServer();
    server.json("PUT", "/environments/e1", 200, makeEnvironment({ id: "e1", version: 2 }));
    await newClient(server).environments.update("e1", {
      version: 1,
      name: "renamed",
    });
    expect(server.requests[0]?.body).toEqual({ version: 1, name: "renamed" });
  });

  it("delete calls the /delete endpoint", async () => {
    const server = new MockServer();
    server.register("DELETE", "/environments/e1/delete", () => new Response(null, { status: 204 }));
    await newClient(server).environments.delete("e1");
    expect(server.requests[0]?.method).toBe("DELETE");
    expect(server.requests[0]?.path).toBe("/environments/e1/delete");
  });

  it("lists versions", async () => {
    const server = new MockServer();
    server.json("GET", "/environments/e1/versions", 200, {
      data: [makeEnvironment({ id: "e1" })],
    });
    const versions = await newClient(server).environments.versions("e1");
    expect(versions).toHaveLength(1);
  });
});
