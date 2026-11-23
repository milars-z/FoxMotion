import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Controls } from "./Controls";
import { Caps, Options, validAssets, elapsed, Job } from "./types";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const options: Options = {
  durations: [3],
  resolutions: ["640x360"],
  steps: [8],
  presets: ["Mock Standard"],
  slots: {},
};
const caps: Caps = {
  backend: "mock",
  max_file_mb: 64,
  max_request_mb: 192,
  media: {},
  modes: [
    { mode: "text", family: "FL2VA", ready: true, reason: "", options },
    {
      mode: "image",
      family: "FL2VA",
      ready: false,
      reason: "NOT_CONFIGURED",
      options: null,
    },
  ],
};
describe("生成表单", () => {
  it("preserves manual sampling choices when selecting LoRA and clears incompatible selections", () => {
    const candidate = {
      ...caps.modes[0],
      options: {
        ...options,
        steps: [8, 16, 18],
        samplers: ["euler"],
        schedulers: ["simple", "beta", "normal"],
        shifts: [8, 12],
        turbo_options: [false],
      },
    };
    const catalog = {
      default_profile_id: "legacy",
      profiles: [
        { id: "legacy", title: "Existing", modes: ["text" as const] },
        { id: "candidate", title: "Candidate", modes: ["text" as const] },
      ],
      loras: [
        {
          id: "test_lora",
          title: "Test LoRA",
          preferred_model_profile: "candidate",
          default_strength: 0.8,
          strength_min: 0,
          strength_max: 1.5,
          author_reference_strength: [0.6, 1],
          strength_source: "第三方",
          sampling_source: "作者",
          recommended: {
            sampler: "euler",
            scheduler: "beta",
            steps: 18,
            shift: 12,
            turbo_enabled: false,
          },
        },
      ],
      routes: [null, "test_lora"].map((lora_id) => ({
        mode: "text" as const,
        model_profile_id: "candidate",
        lora_id,
        capability: candidate,
        status: "unverified",
        reason: "等待 GPU 验收",
      })),
    };
    render(<Controls caps={{ ...caps, catalog }} online onJob={() => {}} />);
    const combo = (name: string) =>
      screen.getByRole("combobox", { name }) as HTMLSelectElement;
    fireEvent.change(combo("Checkpoint profile"), {
      target: { value: "candidate" },
    });
    fireEvent.change(combo("推理步数"), { target: { value: "16" } });
    fireEvent.change(combo("Scheduler"), { target: { value: "normal" } });
    fireEvent.change(combo("LoRA"), { target: { value: "test_lora" } });
    expect(combo("推理步数").value).toBe("16");
    expect(combo("Scheduler").value).toBe("normal");
    fireEvent.click(screen.getByRole("button", { name: "应用推荐参数" }));
    expect(combo("推理步数").value).toBe("18");
    expect(combo("Scheduler").value).toBe("beta");
    fireEvent.change(combo("Checkpoint profile"), {
      target: { value: "legacy" },
    });
    expect(combo("LoRA").value).toBe("");
    expect(combo("LoRA").options[1].disabled).toBe(true);
  });
  it("disables unconfigured modes", () => {
    render(<Controls caps={caps} online onJob={() => {}} />);
    fireEvent.click(screen.getByText("图生视频"));
    expect(screen.getByText("NOT_CONFIGURED")).toBeTruthy();
    expect(screen.queryByText("生成视频")).toBeNull();
  });
  it("posts multipart using capability parameters", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ job_id: "1" }),
    });
    vi.stubGlobal("fetch", fetcher);
    const received = vi.fn();
    render(<Controls caps={caps} online onJob={received} />);
    const button = screen.getByText("生成视频") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("场景提示词"), {
      target: { value: "山间镜头" },
    });
    fireEvent.click(button);
    await waitFor(() => expect(received).toHaveBeenCalledOnce());
    const payload = JSON.parse(fetcher.mock.calls[0][1].body.get("payload"));
    expect(payload.parameters).toEqual({
      duration: 3,
      resolution: "640x360",
      steps: 8,
      preset: "Mock Standard",
      seed: -1,
    });
  });
  it("enforces verified reference combinations", () => {
    const opt = {
      ...options,
      slots: { ref_image: { min: 0, max: 2 } },
      combinations: [{ ref_image: 2 }],
    };
    const file = { role: "ref_image" as const, file: new File(["x"], "x.png") };
    expect(validAssets(opt, [], "reference")).toBe(false);
    expect(validAssets(opt, [file], "reference")).toBe(false);
    expect(validAssets(opt, [file, file], "reference")).toBe(true);
  });
  it("freezes elapsed time on completion", () => {
    expect(
      elapsed({
        started_at: "2026-01-01T00:00:00Z",
        finished_at: "2026-01-01T00:01:03Z",
      } as Job),
    ).toBe("01:03");
  });
});
