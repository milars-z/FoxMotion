#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
bash "$here/check_env.sh"
lock_runtime
if active; then die "Stop the Studio services before updating their environment"; fi
mkdir -p "$RUNTIME_DIR"/{logs,cache,comfy/{temp,input,output,user}} "$RESULT_DIR" "$BACKUP_DIR" "$WORKFLOW_DIR/bindings"
for folder in diffusion_models text_encoders vae loras embeddings; do mkdir -p "$COMFYUI_MODEL_DIR/$folder"; done
cd "$project"
if [[ ! -x $studio ]]; then "$BASE_PYTHON" -m venv "$RUNTIME_DIR/studio"; fi
lockfile="$here/studio-linux.lock"
recipe=$(sha256sum "$lockfile" requirements.txt constraints.txt)
if [[ ! -f $RUNTIME_DIR/studio/recipe || $(cat "$RUNTIME_DIR/studio/recipe") != "$recipe" ]] || ! "$studio" "$here/check.py" installed "$lockfile"; then
    lockhash=$(sha256sum "$lockfile" | cut -d' ' -f1)
    wheelhouse="$RUNTIME_DIR/cache/studio-wheels/$lockhash"
    mkdir -p "$wheelhouse"
    "$studio" -m pip download --no-index --no-deps --only-binary=:all: -r "$lockfile" -d "$wheelhouse"
    "$studio" -m pip install --no-index --only-binary=:all: --find-links "$wheelhouse" -r requirements.txt supervisor setuptools
    printf '%s\n' "$recipe" > "$RUNTIME_DIR/studio/recipe"
fi
"$studio" -m pip check
if ! command -v npm >/dev/null || ! node -e 'process.exit(+process.versions.node.split(".")[0] < 22 ? 1 : 0)' 2>/dev/null; then
    [[ $(uname -m) == x86_64 ]] || die "Install Node 22+ and npm for this CPU"
    asset=node-v22.18.0-linux-x64
    base=https://nodejs.org/dist/v22.18.0
    stage=$(mktemp -d "$TMPDIR/node.XXXXXXXX")
    trap 'rm -rf -- "$stage"' EXIT
    curl -fsSL "$base/SHASUMS256.txt" -o "$stage/checksums"
    curl -fsSL "$base/$asset.tar.xz" -o "$stage/$asset.tar.xz"
    (cd "$stage"; awk -v name="$asset.tar.xz" '$2==name' checksums | sha256sum -c -)
    mkdir -p "$RUNTIME_DIR/node"
    tar -xJf "$stage/$asset.tar.xz" --strip-components=1 -C "$RUNTIME_DIR/node"
fi
export npm_config_cache="$RUNTIME_DIR/cache/npm"
deps_key=$({ sha256sum frontend/package{,-lock}.json; node --version; npm --version; } | sha256sum)
stamp=frontend/node_modules/.foxmotion-deps
if [[ ! -f $stamp || $(cat "$stamp") != "$deps_key" ]] || ! npm --prefix frontend ls --depth=0 >/dev/null 2>&1; then
    npm --prefix frontend ci --include=dev --no-audit --no-fund
    echo "$deps_key" > "$stamp"
fi
npm --prefix frontend run build
space
if [[ $INFERENCE_BACKEND == comfyui ]]; then
    [[ ${COMFYUI_REV:-} =~ ^[a-f0-9]{40}$ ]] || die "Set COMFYUI_REV to a reviewed official Git commit"
    if [[ ! -e $COMFYUI_HOME ]]; then
        mkdir -p "$(dirname "$COMFYUI_HOME")"
        # Fetch to a local sibling and publish only a successful checkout.
        clone=$(mktemp -d "$(dirname "$COMFYUI_HOME")/.foxmotion-comfy.XXXXXXXX")
        git -C "$clone" init -q
        git -C "$clone" remote add origin https://github.com/Comfy-Org/ComfyUI.git
        git -C "$clone" fetch --depth=1 origin "$COMFYUI_REV"
        git -C "$clone" checkout --detach FETCH_HEAD
        mv -T -- "$clone" "$COMFYUI_HOME"
    fi
    [[ -f $COMFYUI_HOME/main.py && $(git -C "$COMFYUI_HOME" rev-parse HEAD) == "$COMFYUI_REV" ]] || die "Existing ComfyUI differs from COMFYUI_REV. Select the reviewed checkout."
    git -C "$COMFYUI_HOME" diff --quiet HEAD -- || die "Review local changes to ComfyUI before proceeding"
    if [[ ! -x $comfy ]]; then
        "$BASE_PYTHON" -m venv --system-site-packages "$RUNTIME_DIR/comfy-venv"
    fi
    # Some images put torch in a parent venv rather than the system site.
    base_site=$("$BASE_PYTHON" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
    comfy_site=$("$comfy" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
    echo "$base_site" > "$comfy_site/runpod-image.pth"
    same_torch() {
        local probe='import torch, torchvision, torchaudio; print(torch.__file__, torchvision.__file__, torchaudio.__file__)'
        local original inherited
        original=$("$BASE_PYTHON" -c "$probe") || die "Image must supply working torch, torchvision and torchaudio"
        inherited=$("$comfy" -c "$probe") || die "ComfyUI venv cannot import the image GPU stack"
        [[ $inherited == "$original" ]] || die "ComfyUI is shadowing the image GPU libraries"
    }
    same_torch
    "$comfy" "$here/check.py" constraints > "$RUNTIME_DIR/gpu-constraints.txt"
    plan="$RUNTIME_DIR/comfy-plan.json"
    wheels="$RUNTIME_DIR/comfy-wheels.txt"
    "$comfy" -m pip install --only-binary=:all: --dry-run --report "$plan" -c "$RUNTIME_DIR/gpu-constraints.txt" -r "$COMFYUI_HOME/requirements.txt"
    "$comfy" "$here/check.py" plan "$plan" "$wheels"
    space
    "$comfy" -m pip install --require-hashes --no-deps --only-binary=:all: -r "$wheels"
    same_torch
    "$comfy" -m pip check
    "$comfy" "$here/check.py" gpu
fi
space
echo "Bootstrap complete. On a rebuilt Pod restore your snapshot BEFORE the first start."
