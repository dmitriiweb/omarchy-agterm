import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const adapterSource = await readFile(
  new URL("../integrations/omp-agterm-status.ts", import.meta.url),
  "utf8",
);
let moduleSequence = 0;
const environmentKeys = [
  "AGTERM_SESSION_ID",
  "AGTERM_STATUS_COMMAND",
  "AGTERM_STATUS_CLI",
];

async function createHarness({ sessionId = "test-session", agentKind, isIdle = () => false, failExec = false } = {}) {
  const previousEnvironment = new Map(
    environmentKeys.map((key) => [key, Object.hasOwn(process.env, key) ? process.env[key] : undefined]),
  );
  process.env.AGTERM_SESSION_ID = sessionId;
  process.env.AGTERM_STATUS_COMMAND = "/isolated/fake-status-command";
  process.env.AGTERM_STATUS_CLI = "/isolated/fake-agtermctl";

  const realSetTimeout = globalThis.setTimeout;
  const timers = [];
  globalThis.setTimeout = (callback, delay, ...args) => {
    const timer = { callback: () => callback(...args), delay, fired: false };
    timers.push(timer);
    return timer;
  };

  const handlers = new Map();
  const calls = [];
  const context = {
    agent: agentKind ? { kind: agentKind } : { kind: "root" },
    isIdle,
  };
  const pi = {
    on(name, callback) {
      const callbacks = handlers.get(name) ?? [];
      callbacks.push(callback);
      handlers.set(name, callbacks);
    },
    async exec(...args) {
      calls.push(args);
      if (failExec) throw new Error("isolated reporter failure");
      return { code: 0 };
    },
  };
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(adapterSource).toString("base64")}#${moduleSequence++}`;
  const { default: install } = await import(moduleUrl);
  install(pi);

  return {
    calls,
    context,
    timers,
    async emit(name, event = {}, ctx = context) {
      for (const callback of handlers.get(name) ?? []) callback(event, ctx);
      // report() intentionally does not block the lifecycle event.
      await Promise.resolve();
      await Promise.resolve();
    },
    runNextTimer() {
      const timer = timers.find((candidate) => !candidate.fired);
      assert.ok(timer, "expected an outstanding fake timer");
      timer.fired = true;
      timer.callback();
    },
    restore() {
      globalThis.setTimeout = realSetTimeout;
      for (const [key, value] of previousEnvironment) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    },
  };
}

function states(harness) {
  return harness.calls.map(([, args]) => args[2]);
}

test("root lifecycle reports active, approval/ask blocked, resumed, settled, and shutdown", async () => {
  const h = await createHarness();
  try {
    await h.emit("agent_start");
    assert.deepEqual(states(h), ["active"]);

    await h.emit("tool_execution_start", { toolName: "bash" });
    assert.deepEqual(states(h), ["active"], "ordinary tools must not block the root agent");

    await h.emit("tool_approval_requested");
    assert.deepEqual(states(h), ["active", "blocked"]);
    assert.deepEqual(h.calls.at(-1)[1].slice(3), ["--blink"]);
    await h.emit("tool_approval_resolved");
    assert.deepEqual(states(h), ["active", "blocked", "active"]);

    await h.emit("tool_execution_start", { toolName: "ask" });
    assert.deepEqual(states(h).at(-1), "blocked");
    await h.emit("tool_execution_end", { toolName: "ask" });
    assert.deepEqual(states(h).at(-1), "active");

    await h.emit("agent_settled");
    assert.deepEqual(states(h).at(-1), "completed");
    assert.deepEqual(h.calls.at(-1)[1].slice(3), ["--auto-reset"]);
    await h.emit("session_shutdown");
    assert.deepEqual(states(h).at(-1), "idle");
    assert.equal(h.timers.length, 0);
  } finally {
    h.restore();
  }
});

test("willContinue and non-idle retry/compaction never report completed", async () => {
  let idle = false;
  const h = await createHarness({ isIdle: () => idle });
  try {
    await h.emit("agent_end", { willContinue: true });
    assert.equal(h.timers.length, 0);

    await h.emit("agent_end", { willContinue: false });

    h.runNextTimer();
    assert.deepEqual(states(h), []);
    h.runNextTimer();
    assert.deepEqual(states(h), []);

    idle = true;
    h.runNextTimer();
    assert.deepEqual(states(h), ["completed"]);
    assert.deepEqual(h.calls.at(-1)[1].slice(3), ["--auto-reset"]);
  } finally {
    h.restore();
  }
});

test("next root generation cancels delayed completion", async () => {
  const h = await createHarness({ isIdle: () => true });
  try {
    await h.emit("agent_end", { willContinue: false });
    await h.emit("agent_start");
    assert.deepEqual(states(h), ["active"]);
    h.runNextTimer();
    assert.deepEqual(states(h), ["active"]);
  } finally {
    h.restore();
  }
});

test("reload reconciles a running root session and leaves an idle one untouched", async () => {
  let idle = false;
  const h = await createHarness({ isIdle: () => idle });
  try {
    await h.emit("session_start");
    assert.deepEqual(states(h), ["active"]);
    idle = true;
    await h.emit("session_start");
    assert.deepEqual(states(h), ["active"]);
  } finally {
    h.restore();
  }
});

test("outside agterm and subagents with inherited session IDs never call reporter", async () => {
  const outside = await createHarness({ sessionId: "" });
  try {
    await outside.emit("session_start");
    await outside.emit("agent_start");
    await outside.emit("tool_approval_requested");
    await outside.emit("tool_execution_start", { toolName: "ask" });
    await outside.emit("agent_settled");
    await outside.emit("session_shutdown");
    assert.deepEqual(outside.calls, []);
  } finally {
    outside.restore();
  }

  const subagent = await createHarness({ agentKind: "sub" });
  try {
    await subagent.emit("session_start");
    await subagent.emit("agent_start");
    await subagent.emit("tool_approval_requested");
    await subagent.emit("tool_execution_start", { toolName: "ask" });
    await subagent.emit("agent_settled");
    await subagent.emit("session_shutdown");
    assert.deepEqual(subagent.calls, []);
  } finally {
    subagent.restore();
  }
});

test("reporter failure is swallowed and cannot escape a lifecycle callback", async () => {
  const h = await createHarness({ failExec: true });
  try {
    await assert.doesNotReject(h.emit("agent_start"));
    await assert.doesNotReject(h.emit("tool_approval_requested"));
    assert.deepEqual(states(h), ["active", "blocked"]);
  } finally {
    h.restore();
  }
});
