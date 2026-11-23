import { useEffect, useRef } from "react";

// Procedural monitor calibration image. No external textures or media requests.
export function Signal({ playing }: { playing: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null),
    phase = useRef(0);
  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    let frame = 0,
      last = 0;
    const draw = (time: number) => {
      if (!last || time - last > 65) {
        if (playing && last) phase.current += (time - last) * 0.00012;
        last = time;
        const w = 960,
          h = 540,
          t = phase.current;
        const sky = ctx.createLinearGradient(0, 0, 0, h);
        sky.addColorStop(0, "#101c1e");
        sky.addColorStop(0.43, "#294347");
        sky.addColorStop(0.7, "#68807a");
        sky.addColorStop(1, "#293f3d");
        ctx.fillStyle = sky;
        ctx.fillRect(0, 0, w, h);
        const sx = w * 0.65,
          sy = h * 0.36;
        const glow = ctx.createRadialGradient(sx, sy, 16, sx, sy, 170);
        glow.addColorStop(0, "rgba(230,237,168,.29)");
        glow.addColorStop(1, "rgba(222,242,164,0)");
        ctx.fillStyle = glow;
        ctx.fillRect(0, 0, w, h);
        ctx.fillStyle = "#dce6a8";
        ctx.beginPath();
        ctx.arc(sx, sy, 37, 0, Math.PI * 2);
        ctx.fill();
        for (let i = 0; i < 8; i++) {
          ctx.fillStyle = "rgba(70,94,85,.28)";
          ctx.fillRect(sx - 40, sy + i * 4, 80, 1);
        }
        for (let z = 0; z < 115; z++) {
          const depth = z / 114;
          ctx.beginPath();
          for (let x = -4; x <= w + 4; x += 4) {
            const a = x / w,
              base = 238 + depth * 315;
            const ridge = (a: number, b: number) =>
              Math.sin(a) * 0.56 +
              Math.sin(b) * 0.31 +
              Math.sin(a * 2.1 + b * 0.3) * 0.13;
            const y =
              base +
              ridge(a * 6.8 + depth * 4.7 + t, a * 14 - depth * 8) *
                (34 + depth * 63) +
              Math.cos(a * 11 + depth * 5.5 + t * 0.3) * (18 + depth * 21);
            if (x === -4) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          }
          ctx.lineTo(w + 4, h + 70);
          ctx.lineTo(-4, h + 70);
          ctx.closePath();
          const v = Math.round(35 - depth * 22);
          ctx.fillStyle = "rgb(" + v + "," + (v + 18) + "," + (v + 20) + ")";
          ctx.fill();
          const shine =
            0.16 + 0.48 * Math.pow(0.5 + 0.5 * Math.sin(depth * 18 + 1.3), 2);
          ctx.strokeStyle = "rgba(171,218,192," + shine + ")";
          ctx.lineWidth = 0.9;
          ctx.stroke();
        }
        ctx.fillStyle = "rgba(10,16,16,.08)";
        for (let y = 1; y < h; y += 3) ctx.fillRect(0, y, w, 1);
      }
      if (playing) frame = requestAnimationFrame(draw);
    };
    draw(performance.now());
    return () => cancelAnimationFrame(frame);
  }, [playing]);
  return (
    <canvas
      ref={ref}
      width={960}
      height={540}
      role="img"
      aria-label="监看测试画面：日轮与绿色等高线山丘"
    />
  );
}
