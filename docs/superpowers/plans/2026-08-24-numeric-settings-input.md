# Numeric Settings Input and Concurrency Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow all three numeric Ark settings to be cleared and retyped, prevent blank submissions, and raise the supported task concurrency range to 1–500.

**Architecture:** Keep transient numeric input text separate from the parsed Ark settings object so an empty field remains representable in React. Parse and validate all numeric drafts at the form submission boundary, then send the existing numeric API shape. Enforce the same concurrency upper bound in the FastAPI schema and TaskRunner snapshot handling.

**Tech Stack:** React 19, TypeScript, TanStack Query, Vitest, Testing Library, MSW, FastAPI, Pydantic, asyncio, pytest

---

## File map

- Modify frontend/src/features/settings/SettingsPage.tsx: own numeric draft strings, validate them, build the numeric save payload, and disable blank submission.
- Modify frontend/src/features/settings/SettingsPage.test.tsx: reproduce clear/retype behavior, assert blank submission blocking, validate ranges, and inspect the saved payload.
- Modify backend/app/schemas.py: accept concurrency values through 500 at the API boundary.
- Modify backend/app/services/runner.py: accept concurrency through 500 in constructor and task snapshots.
- Modify backend/tests/api/test_settings.py: cover API acceptance of 500 and rejection of 501.
- Modify backend/tests/services/test_runner.py: cover constructor and task-snapshot boundaries.

### Task 1: Enforce the 1–500 backend concurrency contract

**Files:**
- Modify: backend/tests/api/test_settings.py:77-91
- Modify: backend/tests/services/test_runner.py:199-223
- Modify: backend/app/schemas.py:9-19
- Modify: backend/app/services/runner.py:39-66,209-213

- [ ] **Step 1: Write failing API boundary tests**

Insert the acceptance test before the existing out-of-range test and change the rejected value from 6 to 501:

~~~python
def test_accepts_maximum_concurrency(app_harness) -> None:
    response = app_harness.client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed",
            "concurrency": 500,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )

    assert response.status_code == 200
    assert response.json()["concurrency"] == 500


def test_rejects_out_of_range_concurrency(app_harness) -> None:
    response = app_harness.client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed",
            "concurrency": 501,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )

    assert response.status_code == 422
~~~

- [ ] **Step 2: Write failing TaskRunner boundary tests**

Add these tests after test_runner_never_exceeds_configured_concurrency:

~~~python
def test_runner_accepts_concurrency_through_500(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)

    runner = TaskRunner(fixture.sessions, FakeInspector(), concurrency=500)

    assert runner.concurrency == 500
    with pytest.raises(ValueError, match="concurrency must be between 1 and 500"):
        TaskRunner(fixture.sessions, FakeInspector(), concurrency=501)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_concurrency", "expected_max_active"),
    [(500, 6), (501, 1)],
)
async def test_runner_bounds_task_snapshot_concurrency(
    tmp_path,
    configured_concurrency: int,
    expected_max_active: int,
) -> None:
    fixture = make_task_fixture(
        tmp_path,
        image_count=6,
        configured_concurrency=configured_concurrency,
    )
    inspector = FakeInspector(delay=0.02)
    runner = TaskRunner(fixture.sessions, inspector, concurrency=1)

    await runner.run_task(fixture.task_id)

    assert inspector.max_active == expected_max_active
~~~

- [ ] **Step 3: Run the new backend tests and verify RED**

Run:

~~~bash
backend/.venv/bin/pytest backend/tests/api/test_settings.py backend/tests/services/test_runner.py -k "concurrency or bounds_task_snapshot" -v
~~~

Expected: test_accepts_maximum_concurrency fails with HTTP 422, the constructor test fails on concurrency 500, and the snapshot case for 500 observes the fallback concurrency of 1.

- [ ] **Step 4: Raise the API schema limit**

In ArkSettingsWrite, replace the concurrency field with:

~~~python
concurrency: int = Field(default=3, ge=1, le=500)
~~~

- [ ] **Step 5: Raise both TaskRunner limits from one module constant**

Add the constant above TaskRunner:

~~~python
MAX_TASK_CONCURRENCY = 500
~~~

Replace the constructor guard with:

~~~python
if not 1 <= concurrency <= MAX_TASK_CONCURRENCY:
    raise ValueError("concurrency must be between 1 and 500")
~~~

Replace _task_concurrency with:

~~~python
def _task_concurrency(self, task_id: str) -> int:
    with self.sessions() as session:
        task = TaskRepository(session).get_task(task_id)
        configured = int(task.model_snapshot_json.get("concurrency", self.concurrency))
    return (
        configured
        if 1 <= configured <= MAX_TASK_CONCURRENCY
        else self.concurrency
    )
~~~

- [ ] **Step 6: Run the backend tests and verify GREEN**

Run:

~~~bash
backend/.venv/bin/pytest backend/tests/api/test_settings.py backend/tests/services/test_runner.py -k "concurrency or bounds_task_snapshot" -v
~~~

Expected: all selected tests pass.

- [ ] **Step 7: Commit the backend contract**

~~~bash
git add backend/app/schemas.py backend/app/services/runner.py backend/tests/api/test_settings.py backend/tests/services/test_runner.py
git commit -m "feat: raise task concurrency limit to 500"
~~~

### Task 2: Support empty numeric drafts and validated submission

**Files:**
- Modify: frontend/src/features/settings/SettingsPage.test.tsx:35-98
- Modify: frontend/src/features/settings/SettingsPage.tsx:15-75,150-205

- [ ] **Step 1: Instrument the existing MSW handlers**

Replace useDefaultHandlers with this version so tests can prove that blank or invalid forms make neither request:

~~~tsx
function useDefaultHandlers() {
  const statusHandler = vi.fn(() => HttpResponse.json(feishuStatus));
  const saveHandler = vi.fn();
  const testHandler = vi.fn();
  server.use(
    http.get("*/api/settings/ark", () => HttpResponse.json(arkSettings)),
    http.put("*/api/settings/ark", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      saveHandler(body);
      return HttpResponse.json({ ...arkSettings, ...body, api_key_configured: true });
    }),
    http.post("*/api/settings/ark/test", () => {
      testHandler();
      return HttpResponse.json({
        ok: true,
        protocol: "responses",
        model: "seed-vision",
        latency_ms: 81,
      });
    }),
    http.get("*/api/auth/feishu/status", statusHandler),
  );
  return { statusHandler, saveHandler, testHandler };
}
~~~

- [ ] **Step 2: Write the failing clear-and-retype tests**

Replace the old concurrency-only validation test with these tests:

~~~tsx
test.each([
  ["并发数", "500", 500],
  ["超时（秒）", "60", 60],
  ["自动重试", "0", 0],
] as const)(
  "allows %s to be cleared before typing a replacement",
  async (label, replacement, expected) => {
    const { saveHandler, testHandler } = useDefaultHandlers();
    const user = userEvent.setup();
    renderSettings();
    const input = await screen.findByLabelText(label);
    const submit = screen.getByRole("button", { name: "测试并保存" });

    await user.clear(input);

    expect(input).toHaveValue(null);
    expect(submit).toBeDisabled();
    await user.click(submit);
    expect(saveHandler).not.toHaveBeenCalled();
    expect(testHandler).not.toHaveBeenCalled();

    await user.type(input, replacement);

    expect(input).toHaveValue(expected);
    expect(submit).toBeEnabled();
  },
);

test("submits parsed numeric settings after clearing and retyping", async () => {
  const { saveHandler, testHandler } = useDefaultHandlers();
  const user = userEvent.setup();
  renderSettings();
  await screen.findByDisplayValue("seed-vision");

  for (const [label, value] of [
    ["并发数", "500"],
    ["超时（秒）", "60"],
    ["自动重试", "0"],
  ] as const) {
    const input = screen.getByLabelText(label);
    await user.clear(input);
    await user.type(input, value);
  }
  await user.click(screen.getByRole("button", { name: "测试并保存" }));

  await waitFor(() =>
    expect(saveHandler).toHaveBeenCalledWith(
      expect.objectContaining({
        concurrency: 500,
        timeout_seconds: 60,
        max_retries: 0,
      }),
    ),
  );
  expect(testHandler).toHaveBeenCalledTimes(1);
});

test.each([
  ["并发数", "501", "并发数必须是 1 到 500 之间的整数"],
  ["并发数", "1.5", "并发数必须是 1 到 500 之间的整数"],
  ["超时（秒）", "601", "超时必须是 1 到 600 之间的整数"],
  ["自动重试", "6", "自动重试必须是 0 到 5 之间的整数"],
] as const)(
  "rejects invalid %s value %s",
  async (label, value, message) => {
    const { saveHandler, testHandler } = useDefaultHandlers();
    const user = userEvent.setup();
    renderSettings();
    const input = await screen.findByLabelText(label);

    await user.clear(input);
    await user.type(input, value);
    await user.click(screen.getByRole("button", { name: "测试并保存" }));

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(saveHandler).not.toHaveBeenCalled();
    expect(testHandler).not.toHaveBeenCalled();
  },
);
~~~

- [ ] **Step 3: Run the settings tests and verify RED**

Run:

~~~bash
npm --prefix frontend test -- src/features/settings/SettingsPage.test.tsx
~~~

Expected: clear-and-retype cases see 0 instead of an empty input, the blank submit button remains enabled, 500 is rejected, and timeout/retry range errors are absent.

- [ ] **Step 4: Add numeric draft types and parsing helpers**

Replace the defaultArk declaration and add the helpers immediately above errorMessage:

~~~tsx
type ArkSettingsForm = Omit<ArkSettings, "api_key_configured">;

type NumericDrafts = {
  concurrency: string;
  timeout_seconds: string;
  max_retries: string;
};

const defaultArk: ArkSettingsForm = {
  base_url: "https://ark.cn-beijing.volces.com/api/v3",
  protocol: "responses",
  model: "",
  concurrency: 3,
  timeout_seconds: 120,
  max_retries: 2,
  thinking: "disabled",
};

function toNumericDrafts(
  settings: Pick<ArkSettingsForm, "concurrency" | "timeout_seconds" | "max_retries">,
): NumericDrafts {
  return {
    concurrency: String(settings.concurrency),
    timeout_seconds: String(settings.timeout_seconds),
    max_retries: String(settings.max_retries),
  };
}

function parseBoundedInteger(value: string, minimum: number, maximum: number): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= minimum && parsed <= maximum ? parsed : null;
}
~~~

- [ ] **Step 5: Store numeric drafts and refresh them from loaded settings**

Add numericDrafts beside arkForm, derive the blank state, and replace the existing Ark settings effect:

~~~tsx
const [arkForm, setArkForm] = useState(defaultArk);
const [numericDrafts, setNumericDrafts] = useState<NumericDrafts>(() =>
  toNumericDrafts(defaultArk),
);
const [arkKey, setArkKey] = useState("");
const [arkMessage, setArkMessage] = useState<string | null>(null);
const [arkError, setArkError] = useState<string | null>(null);
const hasBlankNumericField = Object.values(numericDrafts).some(
  (value) => value.trim() === "",
);

useEffect(() => {
  if (!ark.data) return;
  const { api_key_configured: _configured, ...publicSettings } = ark.data;
  setArkForm(publicSettings);
  setNumericDrafts(toNumericDrafts(publicSettings));
}, [ark.data]);
~~~

- [ ] **Step 6: Parse and validate drafts at submission**

Replace saveArk and submitArk with:

~~~tsx
const saveArk = useMutation({
  mutationFn: async (settings: ArkSettingsForm) => {
    await api.saveArkSettings({ ...settings, api_key: arkKey || null });
    return api.testArk();
  },
  onSuccess: (result) => {
    setArkKey("");
    setArkError(null);
    setArkMessage(`连接测试通过，耗时 ${result.latency_ms} ms`);
    void queryClient.invalidateQueries({ queryKey: ["settings", "ark"] });
  },
  onError: (error) => {
    setArkMessage(null);
    setArkError(errorMessage(error));
  },
});

function submitArk(event: FormEvent<HTMLFormElement>) {
  event.preventDefault();
  setArkMessage(null);
  if (hasBlankNumericField) {
    setArkError("请填写并发数、超时和自动重试");
    return;
  }

  const concurrency = parseBoundedInteger(numericDrafts.concurrency, 1, 500);
  if (concurrency === null) {
    setArkError("并发数必须是 1 到 500 之间的整数");
    return;
  }
  const timeoutSeconds = parseBoundedInteger(numericDrafts.timeout_seconds, 1, 600);
  if (timeoutSeconds === null) {
    setArkError("超时必须是 1 到 600 之间的整数");
    return;
  }
  const maxRetries = parseBoundedInteger(numericDrafts.max_retries, 0, 5);
  if (maxRetries === null) {
    setArkError("自动重试必须是 0 到 5 之间的整数");
    return;
  }

  const settings = {
    ...arkForm,
    concurrency,
    timeout_seconds: timeoutSeconds,
    max_retries: maxRetries,
  };
  setArkForm(settings);
  setArkError(null);
  saveArk.mutate(settings);
}
~~~

- [ ] **Step 7: Bind the three inputs to raw draft strings**

Replace the three numeric inputs with:

~~~tsx
<label className="field">
  <span>并发数</span>
  <input
    type="number"
    min={1}
    max={500}
    step={1}
    required
    value={numericDrafts.concurrency}
    onChange={(event) =>
      setNumericDrafts((current) => ({
        ...current,
        concurrency: event.target.value,
      }))
    }
  />
</label>
<label className="field">
  <span>超时（秒）</span>
  <input
    type="number"
    min={1}
    max={600}
    step={1}
    required
    value={numericDrafts.timeout_seconds}
    onChange={(event) =>
      setNumericDrafts((current) => ({
        ...current,
        timeout_seconds: event.target.value,
      }))
    }
  />
</label>
<label className="field">
  <span>自动重试</span>
  <input
    type="number"
    min={0}
    max={5}
    step={1}
    required
    value={numericDrafts.max_retries}
    onChange={(event) =>
      setNumericDrafts((current) => ({
        ...current,
        max_retries: event.target.value,
      }))
    }
  />
</label>
~~~

Replace the submit button with:

~~~tsx
<button
  className="button primary-button"
  disabled={saveArk.isPending || hasBlankNumericField}
  type="submit"
>
  {saveArk.isPending ? "正在测试..." : "测试并保存"}
</button>
~~~

- [ ] **Step 8: Run the settings tests and verify GREEN**

Run:

~~~bash
npm --prefix frontend test -- src/features/settings/SettingsPage.test.tsx
~~~

Expected: all SettingsPage tests pass with no warnings.

- [ ] **Step 9: Commit the frontend behavior**

~~~bash
git add frontend/src/features/settings/SettingsPage.tsx frontend/src/features/settings/SettingsPage.test.tsx
git commit -m "fix: allow clearing numeric settings"
~~~

### Task 3: Verify the complete change and refresh the running app

**Files:**
- Verify: backend/app/schemas.py
- Verify: backend/app/services/runner.py
- Verify: frontend/src/features/settings/SettingsPage.tsx
- Verify: frontend/src/features/settings/SettingsPage.test.tsx

- [ ] **Step 1: Run formatting and static checks**

Run:

~~~bash
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
npm --prefix frontend run build
~~~

Expected: each command exits 0 with no lint, type, or build errors.

- [ ] **Step 2: Run the complete backend and frontend test suites**

Run:

~~~bash
backend/.venv/bin/pytest backend/tests -v
npm --prefix frontend test
~~~

Expected: both suites exit 0 with zero failed tests.

- [ ] **Step 3: Run the end-to-end flow with ports 8000 and 4173 free**

Run:

~~~bash
npm --prefix frontend run e2e
~~~

Expected: inspection-flow.spec.ts reports 1 passed.

- [ ] **Step 4: Inspect the final diff and worktree**

Run:

~~~bash
git diff --check HEAD~2..HEAD
git status --short
git log -3 --oneline
~~~

Expected: diff check exits 0; only the pre-existing untracked Feishu QR images remain; the log shows the design commit plus the backend and frontend implementation commits.

- [ ] **Step 5: Rebuild and restart the local application**

Run:

~~~bash
./scripts/start.sh
~~~

Expected: migrations complete, the frontend build succeeds, and the server listens on http://127.0.0.1:8000. On the settings page, each numeric input can be cleared, the button disables while blank, 500 is accepted for concurrency, and 0 is accepted for automatic retry.
