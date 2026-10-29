# SPDX-License-Identifier: Apache-2.0
# Modified for sprite-gen-agent (text files read and written as UTF-8; see NOTICE).
# The IFNet below follows Practical-RIFE's IFNet_HDv3 and warplayer (MIT, Copyright (c) 2021 hzwer; NOTICE).
# The augmentation loop follows rife-ncnn-vulkan 20221029's RIFE::process_v4 (MIT, Copyright (c) 2020 nihui; NOTICE).
"""rife-v4.6 in PyTorch: the torch backend of `rife.Rife` (SPRITE_GEN_RIFE_BACKEND=torch, docs/loop-repair.md section 1).

The one module of the engine that imports torch, and it is imported only where the torch backend is chosen
(`rife.Rife()`), so the engine runs without PyTorch and is refused for it only there (`rife.RifeNotInstalled`).

The network is rife-v4.6's IFNet (Practical-RIFE f6b5132, `train_log/IFNet_HDv3.py`, `model/warplayer.py`) written
again here with the same parameter names — four IFBlocks of two strided 3x3 convolutions, eight residual convolutions and
a transposed convolution, the warp a bilinear `grid_sample` at the border — rather than imported: the official module
makes its warp grid on a device it picks itself at import.

Its weights are the binary's own model (`rife.MODEL_SHA256`), read without ncnn (`state_from_ncnn`): the 44
convolutions of `flownet.param` in file order, their float16 weights widened to float32 and their float32 biases; a
residual convolution's scale (`beta`) is folded into its convolution there, so it is 1 here; a transposed convolution's
weights are stored (out, in, ...) and read (in, out, ...). The same weights as the binary, as rounded for it.

What the binary's augmentation does inside one call is done here the same way (rife-ncnn-vulkan 20221029
`RIFE::process_v4`, `-x` and `-z`): the pair, padded with zeros to a multiple of 32, is turned its eight ways (`_geo`: 0
as is, 1 left-right, 2 both, 3 up-down, 4 transposed, 5-7 transposed then 1-3) and run both ways in time (b to a at
1 - t); at each of the four levels every way's flows and mask are turned back to the pair's own frame (`_to_canon`; the
backward ones swapped, `_swap`), averaged, and handed back to every way (`_from_canon`) before the next level; the eight
ways' frames, each the mean of its forward and backward one, are turned back and averaged; the frame is rounded as the
binary rounds it, floor(v * 255 + 0.5). From the upload to the uint8 frame everything runs on the device.

On CUDA every frame runs with cuDNN deterministic, its benchmark off and its convolutions in IEEE float32 (no TF32,
which PyTorch turns on by default): the same frame twice is the same bytes on one GPU, driver and PyTorch, and what
they are is in the runner's identity. On the CPU it is the same bytes whatever the thread count.

On a machine with the GPU for a host without one, `serve` is the executor's side of `rife.RemoteRunner`: a request's
bytes to its answer's, made by the torch backend of this process, loaded once (`rife.answer`).
"""

from __future__ import annotations

import hashlib
import math
import platform
import re
import shutil
import subprocess
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

from sprite_gen._deps import np
from sprite_gen.video import rife

# The network code this process loaded (the runner's identity, `net`), read at import as rife.CODE_SHA256 is.
CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SCALES = (8, 4, 2, 1)
BLOCKS = ((7, 192), (12, 128), (12, 96), (12, 64))  # (input planes, width) of each IFBlock
# The official state dict's convolutions in the order the ncnn model holds them: each block's two strided ones, its
# eight residual ones and its transposed one.
LAYERS = tuple(f"block{i}.{name}" for i in range(len(BLOCKS))
               for name in ("conv0.0.0", "conv0.1.0", *(f"convblock.{j}.conv" for j in range(8)), "lastconv.0"))
NCNN_FLOAT16 = 0x01306B47  # ncnn's tag before a float16 weight blob; a float32 one is tagged 0
NVIDIA_VERSION = Path("/proc/driver/nvidia/version")
# A device that fails, as the network is put on it or as a frame runs on it, is a RIFE that fails (`rife.RifeUnavailable`),
# PyTorch's words kept: out of its memory (`torch.OutOfMemoryError`), its driver failing on a call (`torch.AcceleratorError`)
# and CUDA that does not start are each a RuntimeError; a CUDA call queued before CUDA started that failed when it did is
# `torch.cuda.DeferredCudaCallError`, an Exception. Anything else (a TypeError, an AttributeError) is a fault of this code,
# raised as it is.
DEVICE_FAILURES = (RuntimeError, torch.cuda.DeferredCudaCallError)


def _conv(cin: int, cout: int) -> nn.Sequential:
    return nn.Sequential(nn.Conv2d(cin, cout, 3, 2, 1, bias=True), nn.LeakyReLU(0.2, True))


class ResConv(nn.Module):
    def __init__(self, c: int):
        super().__init__()
        self.conv = nn.Conv2d(c, c, 3, 1, 1)
        self.beta = nn.Parameter(torch.ones((1, c, 1, 1)))
        self.relu = nn.LeakyReLU(0.2, True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.relu(self.conv(x) * self.beta + x)


class IFBlock(nn.Module):
    def __init__(self, in_planes: int, c: int):
        super().__init__()
        self.conv0 = nn.Sequential(_conv(in_planes, c // 2), _conv(c // 2, c))
        self.convblock = nn.Sequential(*(ResConv(c) for _ in range(8)))
        self.lastconv = nn.Sequential(nn.ConvTranspose2d(c, 4 * 6, 4, 2, 1), nn.PixelShuffle(2))

    def raw(self, x: torch.Tensor, flow: torch.Tensor | None, scale: int) -> torch.Tensor:
        """The official IFBlock.forward up to its output at the block's own scale, before it is scaled back up."""
        x = F.interpolate(x, scale_factor=1.0 / scale, mode="bilinear", align_corners=False)
        if flow is not None:
            flow = F.interpolate(flow, scale_factor=1.0 / scale, mode="bilinear", align_corners=False) * 1.0 / scale
            x = torch.cat((x, flow), 1)
        return self.lastconv(self.convblock(self.conv0(x)))


class IFNet(nn.Module):
    def __init__(self):
        super().__init__()
        for i, (cin, c) in enumerate(BLOCKS):
            setattr(self, f"block{i}", IFBlock(cin, c))


def _ncnn_layers(param: bytes, weights: bytes) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """The convolutions of an ncnn model in file order: (layer type, weights, biases) as float32."""
    layers, pos = [], 0
    for line in param.decode("utf-8").split("\n")[2:]:
        parts = line.split()
        if len(parts) < 4 or parts[0] not in ("Convolution", "Deconvolution"):
            continue
        n_in, n_out = int(parts[2]), int(parts[3])
        kv = dict(p.split("=", 1) for p in parts[4 + n_in + n_out:])
        num_output, size = int(kv["0"]), int(kv["6"])
        tag = int.from_bytes(weights[pos:pos + 4], "little")
        pos += 4
        if tag == NCNN_FLOAT16:
            w = np.frombuffer(weights, np.float16, size, pos).astype(np.float32)
            pos += (size * 2 + 3) // 4 * 4
        elif tag == 0:
            w = np.frombuffer(weights, np.float32, size, pos).copy()
            pos += size * 4
        else:
            raise ValueError(f"{parts[1]}: weight tag {tag:#x}")
        if int(kv.get("5", 0)) != 1:
            raise ValueError(f"{parts[1]}: no bias")
        b = np.frombuffer(weights, np.float32, num_output, pos).copy()
        pos += num_output * 4
        layers.append((parts[0], w, b))
    if pos != len(weights):
        raise ValueError(f"read {pos} of the model's {len(weights)} bytes")
    return layers


def state_from_ncnn(files: dict[str, bytes]) -> dict[str, torch.Tensor]:
    """The official state dict of IFNet, every name and shape, filled from the ncnn model's files (`rife.model_bytes`)."""
    shapes = {k: v.shape for k, v in IFNet().state_dict().items()}
    layers = _ncnn_layers(files["flownet.param"], files["flownet.bin"])
    if len(layers) != len(LAYERS):
        raise ValueError(f"{len(layers)} convolutions in the model, {len(LAYERS)} in the network")
    state = {}
    for name, (kind, w, b) in zip(LAYERS, layers):
        shape = shapes[name + ".weight"]
        if kind == "Deconvolution":
            weight = torch.from_numpy(w).reshape(shape[1], shape[0], *shape[2:]).transpose(0, 1).contiguous()
        else:
            weight = torch.from_numpy(w).reshape(shape)
        state[name + ".weight"], state[name + ".bias"] = weight, torch.from_numpy(b).reshape(shapes[name + ".bias"])
        if ".convblock." in name:
            beta = name.removesuffix(".conv") + ".beta"
            state[beta] = torch.ones(shapes[beta])
    return state


def _geo(x: torch.Tensor, g: int) -> torch.Tensor:
    """An (N, C, H, W) tensor turned the binary's way `g` (rife_preproc_tta)."""
    if g >= 4:
        x = x.transpose(2, 3)
    f = g % 4
    return x.flip(3) if f == 1 else x.flip(2, 3) if f == 2 else x.flip(2) if f == 3 else x


def _geo_back(x: torch.Tensor, g: int) -> torch.Tensor:
    """`_geo` undone."""
    f = g % 4
    x = x.flip(3) if f == 1 else x.flip(2, 3) if f == 2 else x.flip(2) if f == 3 else x
    return x.transpose(2, 3) if g >= 4 else x


def _signs(g: int) -> tuple[int, int]:
    """The signs way `g` puts on a flow's x and y: a flip left-right turns x, up-down turns y."""
    f = g % 4
    return (-1 if f in (1, 2) else 1), (-1 if f in (2, 3) else 1)


def _to_canon(t: torch.Tensor, g: int) -> torch.Tensor:
    """A way's flows and mask (N, 5, h, w: flow 0 x y, flow 1 x y, mask) in the pair's own frame (rife_v4_flow_tta_avg)."""
    t = _geo_back(t, g)
    sx, sy = _signs(g)
    x0, y0, x1, y1, m = t.unbind(1)
    v = (sx * x0, sy * y0, sx * x1, sy * y1, m) if g < 4 else (sy * y0, sx * x0, sy * y1, sx * x1, m)  # transposed: x is y
    return torch.stack(v, 1)


def _from_canon(c: torch.Tensor, g: int) -> torch.Tensor:
    """`_to_canon` undone: flows and mask in the pair's own frame, as way `g` sees them."""
    sx, sy = _signs(g)
    dx0, dy0, dx1, dy1, m = c.unbind(1)
    v = (sx * dx0, sy * dy0, sx * dx1, sy * dy1, m) if g < 4 else (sx * dy0, sy * dx0, sx * dy1, sy * dx1, m)
    return _geo(torch.stack(v, 1), g)


def _swap(c: torch.Tensor) -> torch.Tensor:
    """A backward run's flows and mask as the forward run's (rife_v4_flow_tta_temporal_avg): flows swapped, mask negated."""
    return torch.cat([c[:, 2:4], c[:, 0:2], -c[:, 4:5]], 1)


def _driver() -> str:
    """The NVIDIA driver's version: from /proc/driver/nvidia/version, else nvidia-smi's; '' where neither reads."""
    try:
        found = re.search(r"Kernel Module(?: for \S+)?\s+(\d+(?:\.\d+)+)", NVIDIA_VERSION.read_text(encoding="utf-8").splitlines()[0])
        if found:
            return found.group(1)
    except (OSError, IndexError):
        pass
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=driver_version", "--format=csv,noheader"], capture_output=True,
                                 text=True, timeout=30)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip().splitlines()[0].strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return ""


def _deterministic() -> dict[str, object]:
    """cuDNN set to make the same frame twice the same bytes, through PyTorch's newer precision setting only (the two
    kinds of setting do not mix), and read back as it now stands."""
    cudnn = torch.backends.cudnn
    cudnn.deterministic, cudnn.benchmark = True, False
    cudnn.conv.fp32_precision = "ieee"
    return {"cudnn_deterministic": cudnn.deterministic, "cudnn_benchmark": cudnn.benchmark,
            "cudnn_conv_fp32_precision": cudnn.conv.fp32_precision}


class TorchRunner:
    """rife-v4.6 on a torch device, as the runner of a `rife.Rife`: the model in `model` (`rife.model_bytes`, checked
    before it is read) loaded once, a frame its two runs and a read its sixteen passes as well (`rife.frame_runs`,
    `rife.pass_runs`). What its frames depend on is read once, as it loaded and set them, and stays for its life
    (`identity`): the weights' bytes, this network's code, PyTorch's version, and on CUDA its CUDA and cuDNN, the GPU,
    the driver and the settings that make a frame the same bytes; on the CPU the instructions PyTorch runs on.
    `cuda` that is not there is refused, by why, and a device that fails as the network is put on it, or as a frame runs,
    is a RIFE that fails in PyTorch's words (`DEVICE_FAILURES`, `rife.RifeUnavailable`)."""

    def __init__(self, model: Path, device: str):
        if device not in rife.DEVICES:
            raise rife.RifeUnavailable(f"rife-torch: device {device!r}: name cuda or cpu")
        if device == "cuda" and not torch.cuda.is_available():
            wheel = ("a PyTorch built for the CPU only (torch.version.cuda is None)" if torch.version.cuda is None else
                     f"PyTorch {torch.__version__} built for CUDA {torch.version.cuda}, which finds no GPU it can use")
            raise rife.RifeUnavailable(f"SPRITE_GEN_RIFE_DEVICE=cuda, and CUDA is not available here: {wheel}; nothing runs "
                                       "on another device in its place — name SPRITE_GEN_RIFE_DEVICE=cpu to run on the CPU")
        files = rife.model_bytes(model)
        try:
            net = IFNet()
            net.load_state_dict(state_from_ncnn(files), strict=True)
        except (ValueError, RuntimeError) as exc:
            raise rife.RifeUnavailable(f"rife-torch: the model {model} does not load: {exc}") from exc
        self.model, self.device = model, device
        ident: dict[str, object] = {"backend": "torch", "device": device, "net": CODE_SHA256, "torch": torch.__version__,
                                    "weights": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
        try:
            self.net = net.to(device).eval()
            if device == "cuda":
                ident.update(cuda=torch.version.cuda, cudnn=torch.backends.cudnn.version(), gpu=torch.cuda.get_device_name(),
                             capability=list(torch.cuda.get_device_capability()), driver=_driver(), determinism=_deterministic())
            else:
                # a frame made on the CPU is made by its instructions; one made on a GPU is not, and its host's CPU is no
                # part of it
                ident.update(cpu=torch.backends.cpu.get_cpu_capability(), arch=platform.machine())
        except DEVICE_FAILURES as exc:
            raise rife.RifeUnavailable(f"rife-torch on {device} failed to set up: {exc}") from exc
        self.blocks = tuple(getattr(self.net, f"block{i}") for i in range(len(BLOCKS)))
        self._grids: dict[tuple, torch.Tensor] = {}
        self._identity = ident

    def identity(self) -> dict[str, object]:
        return dict(self._identity)

    def describe(self) -> dict[str, str]:
        return {"kind": "rife-torch", "device": self.device, "model": self.model.name}

    def frame(self, colour: list[np.ndarray], cover: list[np.ndarray], t: float) -> tuple[np.ndarray, np.ndarray]:
        return rife.frame_runs(self.run, colour, cover, t)

    def read(self, colour: list[np.ndarray], cover: list[np.ndarray], t: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return (*rife.frame_runs(self.run, colour, cover, t), rife.pass_runs(self.run, colour, cover, t))

    def run(self, x: np.ndarray, y: np.ndarray, t: float, modes: tuple[str, ...] | None) -> np.ndarray:
        """One run, as one call of the binary makes it (`rife.Run`): `t` as the binary is handed it (`-s`, four
        decimals), the augmentation `modes` (None: `rife.tta()`); on CUDA under the settings that make it the same bytes,
        set again for each run (other code in the process may set them), whoever asks for it — a frame, a read, or an
        executor's request (`serve`). A run that fails on the device (out of its memory) is a RIFE that fails
        (`DEVICE_FAILURES`, `rife.RifeUnavailable`)."""
        modes = rife.tta() if modes is None else modes
        try:
            if self.device == "cuda":
                _deterministic()
            return self._run(x, y, float(f"{t:.4f}"), tuple(modes))
        except DEVICE_FAILURES as exc:
            raise rife.RifeUnavailable(f"rife-torch on {self.device} failed: {exc}") from exc

    @torch.inference_mode()
    def _run(self, x: np.ndarray, y: np.ndarray, t: float, modes: tuple[str, ...]) -> np.ndarray:
        h, w = x.shape[:2]
        pads = [self._pad(im) for im in (x, y)]
        ways = [(g, d) for g in (range(8) if "-x" in modes else (0,)) for d in ((0, 1) if "-z" in modes else (0,))]
        frames, _ = self._infer(pads, t, ways)
        v = torch.stack(frames).mean(0)[0, :, :h, :w]
        v = torch.clamp(torch.floor(v * 255.0 + 0.5), 0, 255).to(torch.uint8)
        return v.permute(1, 2, 0).contiguous().cpu().numpy()

    def _pad(self, im: np.ndarray) -> torch.Tensor:
        """An H x W x 3 uint8 image on the device, 0..1, padded with zeros at the bottom and right to a multiple of 32."""
        h, w = im.shape[:2]
        x = torch.zeros((1, 3, math.ceil(h / 32) * 32, math.ceil(w / 32) * 32), dtype=torch.float32, device=self.device)
        x[0, :, :h, :w] = torch.from_numpy(np.ascontiguousarray(im.transpose(2, 0, 1))).to(self.device).float() / 255.0
        return x

    def _warp(self, img: torch.Tensor, flow: torch.Tensor) -> torch.Tensor:
        """The official warp: `img` sampled where `flow` points (bilinear, the border repeated), on the flow's device."""
        k = (str(flow.device), flow.dtype, tuple(flow.shape))
        if k not in self._grids:
            n, _, h, w = flow.shape
            gx = torch.linspace(-1.0, 1.0, w, device=flow.device, dtype=flow.dtype).view(1, 1, 1, w).expand(n, -1, h, -1)
            gy = torch.linspace(-1.0, 1.0, h, device=flow.device, dtype=flow.dtype).view(1, 1, h, 1).expand(n, -1, -1, w)
            self._grids[k] = torch.cat([gx, gy], 1)
        flow = torch.cat([flow[:, 0:1] / ((img.shape[3] - 1.0) / 2.0), flow[:, 1:2] / ((img.shape[2] - 1.0) / 2.0)], 1)
        return F.grid_sample(img, (self._grids[k] + flow).permute(0, 2, 3, 1), mode="bilinear", padding_mode="border",
                             align_corners=True)

    def _infer(self, pads: list[torch.Tensor], t: float, ways: list[tuple[int, int]], *,
               flows: bool = False) -> tuple[list[torch.Tensor], list[torch.Tensor] | None]:
        """The four levels over `ways` — (g, d): turned way g, forward (d 0: the first to the second at t) or backward
        (d 1: the second to the first at 1 - t) — of a padded pair, their flows averaged at every level where there is
        more than one: each way's frame turned back (1 x 3 x H x W) and, with `flows`, its last flows and mask in the
        pair's own frame as the forward run sees them (1 x 5 x H x W)."""
        groups = []
        for transposed in (False, True):  # the ways a transpose turns are another shape: a batch of their own
            items = [(g, d) for g, d in ways if (g >= 4) == transposed]
            if items:
                img0 = torch.cat([_geo(pads[d], g) for g, d in items])
                img1 = torch.cat([_geo(pads[1 - d], g) for g, d in items])
                ts = torch.cat([torch.full_like(img0[k:k + 1, :1], t if d == 0 else 1.0 - t) for k, (g, d) in enumerate(items)])
                groups.append({"items": items, "img0": img0, "img1": img1, "ts": ts, "flow": None, "mask": None})
        many = len(ways) > 1
        for block, scale in zip(self.blocks, SCALES):
            raws = []
            for gr in groups:
                if gr["flow"] is None:
                    raws.append(block.raw(torch.cat((gr["img0"], gr["img1"], gr["ts"]), 1), None, scale)[:, :5].float())
                else:
                    x = torch.cat((gr["w0"], gr["w1"], gr["ts"], gr["mask"]), 1)
                    raws.append(block.raw(x, gr["flow"], scale)[:, :5].float())
            if many:
                canon = [(_swap if d else (lambda c: c))(_to_canon(raw[k:k + 1], g))
                         for gr, raw in zip(groups, raws) for k, (g, d) in enumerate(gr["items"])]
                avg = torch.stack(canon).mean(0)
                raws = [torch.cat([_from_canon(_swap(avg) if d else avg, g) for g, d in gr["items"]]) for gr in groups]
            for gr, raw in zip(groups, raws):
                up = F.interpolate(raw, scale_factor=scale, mode="bilinear", align_corners=False)
                f0, m0 = up[:, :4] * scale, up[:, 4:5]
                gr["flow"] = f0 if gr["flow"] is None else gr["flow"] + f0
                gr["mask"] = m0 if gr["mask"] is None else gr["mask"] + m0
                gr["w0"] = self._warp(gr["img0"], gr["flow"][:, :2])
                gr["w1"] = self._warp(gr["img1"], gr["flow"][:, 2:4])
        frames = []
        for gr in groups:
            m = torch.sigmoid(gr["mask"])
            merged = (gr["w0"] * m + gr["w1"] * (1 - m)).float()
            frames += [_geo_back(merged[k:k + 1], g) for k, (g, d) in enumerate(gr["items"])]
        if not flows:
            return frames, None
        return frames, [(_swap if d else (lambda c: c))(_to_canon(torch.cat([gr["flow"][k:k + 1], gr["mask"][k:k + 1]], 1), g))
                        for gr in groups for k, (g, d) in enumerate(gr["items"])]


# The torch backend an executor serves (`serve`): loaded by the first request, from the model and on the device this
# process names (`rife.locate_model()`, `rife.device()`), and kept for every request after.
_served: TorchRunner | None = None


def serve(request: bytes) -> bytes:
    """The executor's side of `rife.RemoteRunner`, on the machine with the GPU: a request's bytes to its answer's
    (`rife.answer`), the frame or the read made by the torch backend of this process. It is loaded once, by the first
    request — the probe a RIFE sends as it is made, which so takes the cold start — and a backend that cannot load is
    answered in its words, to be loaded again by the next. A host reaches it with the send it names
    (SPRITE_GEN_RIFE_EXECUTOR), the same sprite-gen on both sides."""
    return rife.answer(request, _loaded)


def _loaded() -> TorchRunner:
    global _served
    if _served is None:
        _served = TorchRunner(rife.locate_model(), rife.device())
    return _served
