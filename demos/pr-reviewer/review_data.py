"""
Mock PR data and pre-authored review findings for the PR Reviewer demo.
"""

PULL_REQUESTS = {
    "pr-1": {
        "id": "pr-1",
        "number": 142,
        "title": "Add rate limiting to authentication middleware",
        "description": (
            "Adds brute-force protection to the auth middleware by tracking failed login "
            "attempts per username+IP combination. Blocks further attempts after 5 failures "
            "within a 5-minute window. Also adds a verify_token helper for token-based auth."
        ),
        "author": "jsmith",
        "language": "Python",
        "files_changed": 1,
        "additions": 24,
        "deletions": 0,
        "diff": """\
diff --git a/auth/middleware.py b/auth/middleware.py
--- a/auth/middleware.py
+++ b/auth/middleware.py
@@ -12,6 +12,24 @@ class AuthMiddleware:
     def __init__(self, app, secret_key):
         self.app = app
         self.secret_key = secret_key
+        self.failed_attempts = {}
+
+    def check_rate_limit(self, username, ip):
+        key = f"{username}:{ip}"
+        attempts = self.failed_attempts.get(key, [])
+        attempts = [t for t in attempts if time.time() - t < 300]
+        if len(attempts) >= 5:
+            return False
+        return True
+
+    def record_failure(self, username, ip):
+        key = f"{username}:{ip}"
+        if key not in self.failed_attempts:
+            self.failed_attempts[key] = []
+        self.failed_attempts[key].append(time.time())
+
+    def verify_token(self, token):
+        query = f"SELECT * FROM users WHERE token = '{token}'"
+        return db.execute(query).fetchone()""",
    },
    "pr-2": {
        "id": "pr-2",
        "number": 187,
        "title": "Refactor user dashboard to use React hooks",
        "description": (
            "Replaces the old class-based Dashboard component with a functional component "
            "using useState and useEffect. Adds real-time updates via WebSocket and an "
            "export button that triggers a server-side data export."
        ),
        "author": "alopez",
        "language": "TypeScript",
        "files_changed": 1,
        "additions": 30,
        "deletions": 15,
        "diff": """\
diff --git a/src/components/Dashboard.tsx b/src/components/Dashboard.tsx
--- a/src/components/Dashboard.tsx
+++ b/src/components/Dashboard.tsx
@@ -1,30 +1,45 @@
+import React, { useState, useEffect } from 'react';
+
 export function Dashboard({ userId }: { userId: string }) {
-  return <div>Loading...</div>
+  const [data, setData] = useState(null);
+  const [ws, setWs] = useState(null);
+
+  useEffect(() => {
+    fetch(`/api/users/${userId}/dashboard`)
+      .then(r => r.json())
+      .then(setData);
+
+    const socket = new WebSocket(`ws://localhost:8080/live/${userId}`);
+    setWs(socket);
+    socket.onmessage = (e) => setData(JSON.parse(e.data));
+  }, []);
+
+  const handleExport = async () => {
+    const res = await fetch('/api/export', {
+      method: 'POST',
+      body: JSON.stringify({ userId, data, timestamp: Date.now() })
+    });
+    window.location.href = res.url;
+  };""",
    },
    "pr-3": {
        "id": "pr-3",
        "number": 203,
        "title": "Add concurrent job processing to worker",
        "description": (
            "Rewrites the Processor to fan out jobs concurrently using goroutines, "
            "collecting results into a shared slice protected by a mutex. Adds a new "
            "process() method that fetches job details from the database."
        ),
        "author": "mchen",
        "language": "Go",
        "files_changed": 1,
        "additions": 28,
        "deletions": 5,
        "diff": """\
diff --git a/worker/processor.go b/worker/processor.go
--- a/worker/processor.go
+++ b/worker/processor.go
@@ -8,15 +8,38 @@ type Processor struct {
     queue chan Job
 }
+
+func (p *Processor) ProcessAll(jobs []Job) []Result {
+    results := []Result{}
+    var mu sync.Mutex
+
+    for _, job := range jobs {
+        go func(j Job) {
+            result, err := p.process(j)
+            if err != nil {
+                log.Printf("job failed: %s", err)
+            }
+            mu.Lock()
+            results = append(results, result)
+            mu.Unlock()
+        }(j)
+    }
+
+    return results
+}
+
+func (p *Processor) process(j Job) (Result, error) {
+    conn := p.dbPool.Get()
+    rows, _ := conn.Query("SELECT * FROM jobs WHERE id = " + j.ID)
+    defer rows.Close()
+    return processRows(rows), nil
+}""",
    },
}

# ---------------------------------------------------------------------------
# Review content: opening analysis lines, findings, and summary per PR
# ---------------------------------------------------------------------------

REVIEW_DATA = {
    "pr-1": {
        "opening": [
            "Analyzing PR #142 — Add rate limiting to authentication middleware",
            "Reading diff... 1 file changed, 24 insertions(+)",
            "File: auth/middleware.py",
            "Scanning for security patterns...",
            "Checking concurrency safety...",
            "Evaluating performance characteristics...",
        ],
        "findings": [
            # Security
            "CATEGORY:Security",
            "CRITICAL|Security|SQL Injection in verify_token()|"
            "The `token` parameter is interpolated directly into the SQL string: "
            "`query = f\"SELECT * FROM users WHERE token = '{token}'\"`. "
            "An attacker can supply a token like `' OR '1'='1` to bypass authentication entirely. "
            "Fix: use a parameterized query — `db.execute(\"SELECT * FROM users WHERE token = ?\", (token,))`.",
            "HIGH|Security|Timing attack on token comparison|"
            "If `verify_token` compares the result's token field with `==`, the comparison is not "
            "constant-time and leaks information via response timing. "
            "Use `hmac.compare_digest(stored_token, supplied_token)` for all secret comparisons.",
            # Correctness
            "CATEGORY:Correctness",
            "MEDIUM|Correctness|Rate-limit state is in-process only|"
            "`self.failed_attempts` is a plain dict on the middleware instance. "
            "State resets on every worker restart and is not shared across multiple processes or "
            "pods. Under a typical multi-worker deployment the limit offers no real protection. "
            "Consider a Redis-backed store with TTL-keyed counters (e.g. `INCR` + `EXPIRE`).",
            "MEDIUM|Correctness|Race condition on failed_attempts dict|"
            "`record_failure` reads, checks, and writes `self.failed_attempts[key]` without any "
            "locking. Under concurrent requests (threads or async) two workers can both pass the "
            "check and both append, causing the list to grow beyond the cap or losing entries. "
            "Protect with `threading.Lock` or switch to an atomic backend.",
            # Performance
            "CATEGORY:Performance",
            "LOW|Performance|Linear scan on every rate-limit check|"
            "`check_rate_limit` rebuilds a filtered list from scratch on every call: "
            "`[t for t in attempts if time.time() - t < 300]`. "
            "For a hot auth path this is wasteful. A `collections.deque(maxlen=5)` per key with "
            "only the five most recent timestamps avoids the full scan and caps memory per key.",
            # Style
            "CATEGORY:Style",
            "GOOD|Style|Sensible rate-limit window default|"
            "The 300-second (5-minute) window is a reasonable industry default for brute-force "
            "protection. Consider exposing it as a constructor parameter "
            "`(window_seconds=300, max_attempts=5)` so callers can tune it without a code change.",
        ],
        "summary": (
            "SUMMARY|2 critical/high security issues, 2 medium correctness bugs, 1 performance note|"
            "This PR introduces two serious security vulnerabilities that must be fixed before merge: "
            "a SQL injection in verify_token() and a potential timing attack. The rate-limiting "
            "logic also has correctness problems under concurrent or multi-process deployments. "
            "The overall approach is sound — address the security issues and move state to Redis "
            "before landing."
        ),
    },
    "pr-2": {
        "opening": [
            "Analyzing PR #187 — Refactor user dashboard to use React hooks",
            "Reading diff... 1 file changed, 30 insertions(+), 15 deletions(-)",
            "File: src/components/Dashboard.tsx",
            "Scanning useEffect dependency arrays...",
            "Checking WebSocket lifecycle...",
            "Auditing fetch error handling...",
        ],
        "findings": [
            # Correctness
            "CATEGORY:Correctness",
            "HIGH|Correctness|WebSocket is never closed — memory leak|"
            "The WebSocket is created inside `useEffect` but there is no cleanup function. "
            "When the component unmounts (or `userId` changes) the socket stays open and "
            "`onmessage` continues calling `setData` on an unmounted component, causing a React "
            "state-update-on-unmounted-component warning and a slow connection leak. "
            "Fix: `return () => { socket.close(); };` at the end of the effect body.",
            "HIGH|Correctness|Missing userId in useEffect dependency array|"
            "The dependency array is `[]` (run once), but the effect closes over `userId`. "
            "If the parent re-renders with a different `userId` the dashboard silently continues "
            "showing the old user's data. Change to `[userId]` and ensure the WebSocket is "
            "also torn down and recreated (the cleanup fix above handles this automatically).",
            # Security
            "CATEGORY:Security",
            "MEDIUM|Security|Hardcoded ws://localhost:8080 in production code|"
            "The WebSocket URL is hard-coded to `ws://localhost:8080/live/${userId}`. "
            "This will silently fail in any non-local environment and forces an unencrypted "
            "`ws://` connection even when the page is served over HTTPS (which browsers block). "
            "Use an environment variable (`process.env.REACT_APP_WS_URL`) or derive the URL "
            "from `window.location` with a `wss://` scheme when the page is secure.",
            # Correctness (continued)
            "MEDIUM|Correctness|handleExport navigates to res.url unconditionally|"
            "`window.location.href = res.url` runs regardless of whether the request succeeded. "
            "If the server returns a 4xx/5xx, `res.url` is just the original request URL and "
            "the user gets silently redirected nowhere useful. Check `res.ok` first and show "
            "an error message on failure.",
            "MEDIUM|Correctness|Stale data snapshot in handleExport|"
            "`data` captured in the `handleExport` closure is whatever was in state at click "
            "time — it may be several seconds stale if the WebSocket has been delivering updates. "
            "If export consistency matters, either fetch fresh data inside `handleExport` before "
            "posting, or document explicitly that the export is a point-in-time snapshot.",
            # Performance
            "CATEGORY:Performance",
            "LOW|Performance|handleExport not memoized|"
            "If `handleExport` is passed to a child wrapped in `React.memo`, it will trigger "
            "re-renders on every parent render because a new function reference is created each "
            "time. Wrap in `useCallback([userId])` to stabilise the reference.",
        ],
        "summary": (
            "SUMMARY|2 high correctness bugs, 3 medium issues, 1 performance note|"
            "The refactor is mostly clean but has two bugs that will appear immediately in "
            "non-trivial usage: a WebSocket leak and stale-closure behaviour when userId changes. "
            "Fix the cleanup return and add userId to the dependency array before merging. "
            "The hardcoded localhost URL is a deploy-blocker for any non-local environment."
        ),
    },
    "pr-3": {
        "opening": [
            "Analyzing PR #203 — Add concurrent job processing to worker",
            "Reading diff... 1 file changed, 28 insertions(+), 5 deletions(-)",
            "File: worker/processor.go",
            "Checking goroutine synchronisation...",
            "Auditing database connection lifecycle...",
            "Scanning for SQL injection patterns...",
        ],
        "findings": [
            # Correctness
            "CATEGORY:Correctness",
            "CRITICAL|Correctness|ProcessAll returns before goroutines finish — data race|"
            "`ProcessAll` launches all goroutines and then immediately executes `return results`. "
            "There is no `sync.WaitGroup` (or channel rendezvous) so the function returns an "
            "empty or partially-filled slice every time. All writes to `results` after the "
            "return are a data race on the now-escaped slice. "
            "Fix: add `var wg sync.WaitGroup`, call `wg.Add(1)` before each `go func`, "
            "`wg.Done()` at the end of the closure, and `wg.Wait()` before returning.",
            # Security
            "CATEGORY:Security",
            "HIGH|Security|SQL injection via string concatenation|"
            '`conn.Query("SELECT * FROM jobs WHERE id = " + j.ID)` concatenates `j.ID` '
            "directly. If `j.ID` is ever sourced from user input or an external queue, this "
            "is exploitable. "
            'Use a parameterized query: `conn.Query("SELECT * FROM jobs WHERE id = $1", j.ID)`.',
            # Correctness (continued)
            "CATEGORY:Correctness",
            "HIGH|Correctness|Database connection leaked on every job|"
            "`p.dbPool.Get()` acquires a connection but there is no corresponding `Put` or "
            "`Close`. Each call to `process()` permanently borrows a connection from the pool. "
            "After enough jobs the pool is exhausted and all subsequent calls block forever. "
            "Fix: `defer p.dbPool.Put(conn)` immediately after the `Get()` call.",
            "MEDIUM|Correctness|Failed job result silently added to output|"
            "When `p.process(j)` returns an error, the code logs it but still appends the "
            "zero-value `Result{}` to the slice. Callers receive a result slice where success "
            "and failure are indistinguishable. Either omit failed results from the slice and "
            "return them separately, or add an `Error` field to `Result`.",
            # Performance
            "CATEGORY:Performance",
            "LOW|Performance|Unbounded goroutine fan-out|"
            "One goroutine is spawned per job with no upper bound. For a batch of 10 000 jobs "
            "this creates 10 000 goroutines simultaneously, exhausts the DB pool immediately "
            "(compounding the connection-leak bug), and can OOM the process. "
            "Use a semaphore (`make(chan struct{}, N)`) or a fixed worker-pool pattern to cap "
            "concurrency at a safe level (typically 2–4× the DB pool size).",
        ],
        "summary": (
            "SUMMARY|1 critical correctness bug, 2 high severity issues, 2 further concerns|"
            "This PR has a fundamental correctness bug: ProcessAll never actually waits for its "
            "goroutines, so it always returns empty results. There are also two resource-safety "
            "issues (SQL injection, connection leak) that would be production incidents on their "
            "own. All three must be fixed before merge. The unbounded concurrency is a follow-up "
            "concern once the correctness bugs are resolved."
        ),
    },
}
