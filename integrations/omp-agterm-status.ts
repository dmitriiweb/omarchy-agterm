// Opt-in OMP extension: report root-agent lifecycle state to agterm.
// Install manually in ~/.omp/agent/extensions; never copied by install.sh.
export default function (pi) {
  const statusCommand = process.env.AGTERM_STATUS_COMMAND ||
    `${process.env.HOME}/.config/agterm/agent-status/agterm-agent-status.sh`;
  const statusCli = process.env.AGTERM_STATUS_CLI || "agtermctl";
  let settledHookSeen = false;
  let generation = 0;

  function isRoot(ctx) {
    return !!process.env.AGTERM_SESSION_ID && ctx?.agent?.kind !== "sub";
  }

  async function report(ctx, state, ...args) {
    if (!isRoot(ctx)) return;
    try {
      await pi.exec("env", [`AGTERMCTL=${statusCli}`, statusCommand, state, ...args], { timeout: 1_000 });
    } catch {
      // Status reporting must never fail or delay the agent turn.
    }
  }

  pi.on("session_start", (_event, ctx) => {
    generation += 1;
    if (isRoot(ctx) && ctx?.isIdle?.() === false) void report(ctx, "active");
  });

  pi.on("agent_start", (_event, ctx) => {
    generation += 1;
    void report(ctx, "active");
  });

  pi.on("tool_approval_requested", (_event, ctx) => {
    void report(ctx, "blocked", "--blink");
  });

  pi.on("tool_approval_resolved", (_event, ctx) => {
    void report(ctx, "active");
  });

  pi.on("tool_execution_start", (event, ctx) => {
    if (event?.toolName === "ask") void report(ctx, "blocked", "--blink");
  });

  pi.on("tool_execution_end", (event, ctx) => {
    if (event?.toolName === "ask") void report(ctx, "active");
  });

  pi.on("agent_settled", (_event, ctx) => {
    settledHookSeen = true;
    void report(ctx, "completed", "--auto-reset");
  });

  pi.on("agent_end", (event, ctx) => {
    if (settledHookSeen || event?.willContinue === true || !isRoot(ctx)) return;
    const endedGeneration = generation;
    const checkIdle = (delay) => {
      setTimeout(() => {
        if (endedGeneration !== generation || settledHookSeen) return;
        try {
          if (ctx?.isIdle?.() === true) {
            void report(ctx, "completed", "--auto-reset");
            return;
          }
        } catch {
          return;
        }
        if (delay < 250) checkIdle(Math.min(delay * 2, 250));
      }, delay);
    };
    checkIdle(25);
  });

  pi.on("session_shutdown", (_event, ctx) => {
    void report(ctx, "idle");
  });
}
