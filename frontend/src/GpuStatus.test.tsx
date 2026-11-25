import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { GpuStatus } from "./GpuStatus";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("disables release when the queued job has not entered Comfy yet", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      device: "Test GPU", models: [], busy: false, releasing: false,
      queued_or_running: true, total: 2 ** 30, used: 0, cgroup: null,
    }),
  }));
  render(<GpuStatus />);
  await screen.findByText("Test GPU");
  expect((screen.getByRole("button", { name: "释放显存" }) as HTMLButtonElement).disabled).toBe(true);
});
