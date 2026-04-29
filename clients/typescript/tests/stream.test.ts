import { describe, expect, it } from "vitest";

import { AodHTTPError, Client } from "../src/index.js";
import { MockServer, uuid } from "./helpers.js";

function newClient(server: MockServer): Client {
  return new Client({
    baseUrl: "http://mock",
    token: "aod_test",
    fetch: server.fetch,
  });
}

describe("stream", () => {
  it("parses SSE data lines into StreamEvents", async () => {
    const server = new MockServer();
    const sid = uuid();
    server.register("GET", `/sessions/${sid}/stream`, () => {
      const body =
        'data: {"type":"start","session_id":"' +
        sid +
        '"}\n\n' +
        'data: {"type":"output","id":1,"stream":"stdout","data":"hi"}\n\n';
      return new Response(body, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });

    const stream = await newClient(server).sessions.stream(sid);
    const collected: string[] = [];
    for await (const event of stream) {
      collected.push(event.type);
    }
    await stream.close();

    expect(collected).toEqual(["start", "output"]);
  });

  it("splits events that arrive across multiple chunks", async () => {
    const server = new MockServer();
    const sid = uuid();
    server.register("GET", `/sessions/${sid}/stream`, () => {
      const chunks = [
        "data: {\"type\":",
        '"start"}\n\ndata: {"type":"output",',
        '"id":7}\n\n',
      ];
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          const encoder = new TextEncoder();
          for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
          controller.close();
        },
      });
      return new Response(stream, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });

    const stream = await newClient(server).sessions.stream(sid);
    const events = [];
    for await (const event of stream) events.push(event);
    await stream.close();

    expect(events.map((e) => e.type)).toEqual(["start", "output"]);
    expect(events[1]?.id).toBe(7);
  });

  it("captures unknown keys in extra", async () => {
    const server = new MockServer();
    const sid = uuid();
    server.register("GET", `/sessions/${sid}/stream`, () => {
      const body = 'data: {"type":"output","id":1,"stream":"stdout","data":"hi"}\n\n';
      return new Response(body, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });

    const stream = await newClient(server).sessions.stream(sid);
    const events = [];
    for await (const event of stream) events.push(event);
    await stream.close();

    expect(events[0]?.extra).toEqual({ stream: "stdout", data: "hi" });
  });

  it("raises AodHTTPError on 4xx responses", async () => {
    const server = new MockServer();
    const sid = uuid();
    server.register("GET", `/sessions/${sid}/stream`, () => {
      return new Response(JSON.stringify({ detail: "nope" }), {
        status: 404,
        headers: { "content-type": "application/json" },
      });
    });

    const stream = await newClient(server).sessions.stream(sid);
    const iterate = async () => {
      for await (const _ of stream) {
        // drain
      }
    };
    await expect(iterate()).rejects.toBeInstanceOf(AodHTTPError);
    await stream.close();
  });

  it("aborts the underlying fetch when the iteration ends without close()", async () => {
    // Pre-fix, breaking out of `for await` only released the reader's lock —
    // the fetch's underlying connection stayed open until socket idle-out.
    // Pin: the iterator's finally aborts the controller, which propagates
    // the abort to the request signal observed by the server.
    const server = new MockServer();
    const sid = uuid();
    let abortObserved = false;
    server.register("GET", `/sessions/${sid}/stream`, (req) => {
      req.signal.addEventListener("abort", () => {
        abortObserved = true;
      });
      const body =
        'data: {"type":"start"}\n\n' +
        'data: {"type":"output","id":1,"stream":"stdout","data":"hi"}\n\n';
      return new Response(body, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });
    const stream = await newClient(server).sessions.stream(sid);
    for await (const event of stream) {
      if (event.type === "output") break;
    }
    expect(abortObserved).toBe(true);
  });

  it("aborts the fetch even when response.text() throws on the error branch", async () => {
    // Pin: if reading the error body throws, the outer try/finally still
    // fires onDone() (and therefore controller.abort()). Without the outer
    // finally, the throw would skip the abort and leak the connection.
    const server = new MockServer();
    const sid = uuid();
    let abortObserved = false;
    server.register("GET", `/sessions/${sid}/stream`, (req) => {
      req.signal.addEventListener("abort", () => {
        abortObserved = true;
      });
      const response = new Response(null, {
        status: 503,
        headers: { "content-type": "application/json" },
      });
      Object.defineProperty(response, "text", {
        value: () => Promise.reject(new Error("body read failed")),
      });
      return response;
    });
    const stream = await newClient(server).sessions.stream(sid);
    const iterate = async () => {
      for await (const _ of stream) {
        // drain
      }
    };
    await expect(iterate()).rejects.toThrow("body read failed");
    expect(abortObserved).toBe(true);
  });

  it("ignores non-data lines", async () => {
    const server = new MockServer();
    const sid = uuid();
    server.register("GET", `/sessions/${sid}/stream`, () => {
      const body =
        ": keep-alive\n\n" +
        'data: {"type":"start"}\n\n' +
        "event: ignored\n\n";
      return new Response(body, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });

    const stream = await newClient(server).sessions.stream(sid);
    const events = [];
    for await (const event of stream) events.push(event);
    await stream.close();

    expect(events.map((e) => e.type)).toEqual(["start"]);
  });
});
