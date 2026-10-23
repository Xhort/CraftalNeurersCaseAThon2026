"""Build a silent, captioned fallback MP4 from the verified presentation frames."""

from __future__ import annotations

from pathlib import Path

import imageio_ffmpeg
from PIL import Image


PROJECT = Path(__file__).resolve().parents[1]
FRAME_DIR = PROJECT / "demo" / "fallback_frames"
OUTPUT = PROJECT / "demo" / "fallback_demo.mp4"
FPS = 12
SECONDS_PER_SLIDE = 7.5


def main() -> None:
    frames = sorted(FRAME_DIR.glob("slide-*.png"))
    if len(frames) != 10:
        raise SystemExit(f"Expected 10 slide frames, found {len(frames)}")
    first = Image.open(frames[0]).convert("RGB")
    size = first.size
    writer = imageio_ffmpeg.write_frames(
        str(OUTPUT),
        size,
        fps=FPS,
        codec="libx264",
        pix_fmt_in="rgb24",
        pix_fmt_out="yuv420p",
        output_params=["-movflags", "+faststart", "-crf", "22"],
    )
    writer.send(None)
    repeats = int(FPS * SECONDS_PER_SLIDE)
    try:
        for frame_path in frames:
            image = Image.open(frame_path).convert("RGB")
            if image.size != size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            payload = image.tobytes()
            for _ in range(repeats):
                writer.send(payload)
    finally:
        writer.close()
    print(f"Wrote {OUTPUT} ({len(frames) * SECONDS_PER_SLIDE:.0f} seconds, silent with on-screen captions)")


if __name__ == "__main__":
    main()
