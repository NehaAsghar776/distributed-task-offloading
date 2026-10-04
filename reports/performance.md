# Performance Analysis

## Method

Use `benchmarks/benchmark.py` to compare a local CPU encode with a remote encode using the same source and output resolution/bitrate. Record the exact client and worker CPUs, GPU/driver, FFmpeg version/build, network type/link rate, codec and preset, source duration/dimensions/size, and ambient workload. Run each condition at least three times; report median and range, and disclose whether file-system caches or warm-up runs were used.

The runner measures local encode duration; upload duration; remote submit-to-completion interval; output download duration; remote end-to-end duration; file sizes; calculated render-only and end-to-end speedup; and periodic worker CPU, RAM, GPU-utilization, and GPU-memory samples (when `nvidia-smi` is available). Client host CPU count and RAM and worker platform/capabilities are recorded. These are sampled benchmark observations, not a controlled profiler; collect `nvidia-smi dmon` (or an equivalent vendor tool) alongside runs for a higher-resolution utilization trace.

$$
S_{render} = \frac{T_{local}}{T_{worker}}, \qquad
S_{end-to-end} = \frac{T_{local}}{T_{upload} + T_{worker} + T_{download} + T_{API/polling}}
$$

`remote_overhead_seconds` is `remote_end_to_end_seconds - remote_render_seconds`; it approximates upload plus download time. Polling is included in the measured worker wait/render interval, while small API/scheduling boundaries are not timed independently. Upload and download are recorded separately.

## Results

No measured values are checked in yet. Populate this table from actual runs; do not estimate or fabricate numbers.

| Source size | Source duration | Output | Local codec/preset | Worker codec/preset | Local (s) | Upload (s) | Worker (s) | Download (s) | End-to-end (s) | End-to-end speedup |
| ---: | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| pending | pending | pending | pending | pending | pending | pending | pending | pending | pending | pending |

## Discussion Checklist

- Compare small, medium, and large inputs at more than one resolution.
- Explain when transfer overhead dominates and when remote rendering wins.
- Distinguish render-only speedup from end-to-end speedup.
- State whether the remote codec actually used NVENC; attach the capability output and a GPU utilization trace.
- Discuss network variability, different codecs/presets, thermal/power behavior, and repeated-run spread.
- Include the generated JSON/CSV outputs and identify the exact test hardware/software.