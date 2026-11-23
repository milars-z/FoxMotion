import { useEffect, useState } from "react";
import { ImagePlus, X } from "lucide-react";
import { Asset, Caps, Options, Role, roleName } from "./types";

function Preview({ asset, remove }: { asset: Asset; remove: () => void }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    const u = URL.createObjectURL(asset.file);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [asset.file]);
  return (
    <div className="asset">
      {asset.file.type.startsWith("image/") ? (
        <img src={url} alt={asset.file.name} />
      ) : asset.file.type.startsWith("video/") ? (
        <video src={url} controls preload="metadata" />
      ) : (
        <audio src={url} controls />
      )}
      <span title={asset.file.name}>{asset.file.name}</span>
      <button
        type="button"
        className="icon"
        aria-label={"移除 " + asset.file.name}
        onClick={remove}
      >
        <X size={14} />
      </button>
    </div>
  );
}
export function Uploads({
  options,
  assets,
  setAssets,
  caps,
  onError,
}: {
  options: Options;
  assets: Asset[];
  setAssets: (a: Asset[]) => void;
  caps: Caps;
  onError: (e: string) => void;
}) {
  return (
    <>
      {Object.entries(options.slots).map(([key, slot]) => {
        const role = key as Role,
          items = assets.filter((a) => a.role === role);
        const kind =
          role === "ref_video"
            ? "video"
            : role === "ref_audio"
              ? "audio"
              : "image";
        return (
          <section className="upload" key={role}>
            <div className="fieldhead">
              <span>{roleName[role]}</span>
              <small>
                {slot.min ? "必填" : "可选"} · {items.length}/{slot.max}
              </small>
            </div>
            {items.map((a) => (
              <Preview
                key={assets.indexOf(a)}
                asset={a}
                remove={() => setAssets(assets.filter((it) => it !== a))}
              />
            ))}
            {items.length < slot.max && (
              <label className="drop">
                <ImagePlus size={16} />
                <span>选择{roleName[role]}</span>
                <input
                  type="file"
                  aria-label={roleName[role]}
                  multiple={slot.max > 1}
                  accept={Object.keys(caps.media[kind]).join(",")}
                  onChange={(e) => {
                    const files = Array.from(e.target.files ?? []);
                    e.target.value = "";
                    if (files.length + items.length > slot.max)
                      return onError("所选素材数量超过上限");
                    if (
                      files.some(
                        (f) =>
                          f.size > caps.max_file_mb * 1024 ** 2 ||
                          !Object.values(caps.media[kind]).includes(caps.mime_aliases?.[f.type] ?? f.type),
                      )
                    )
                      return onError(
                        "文件类型不支持或超过 " + caps.max_file_mb + " MB",
                      );
                    setAssets([
                      ...assets,
                      ...files.map((file) => ({ role, file })),
                    ]);
                    onError("");
                  }}
                />
              </label>
            )}
          </section>
        );
      })}
    </>
  );
}
