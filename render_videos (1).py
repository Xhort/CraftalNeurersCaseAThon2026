"""
render_videos.py — render every roadmap/scenario video to MP4 from the LIVE site data.

It opens the page that contains the roadmap section, waits for the section to
load /api/downloads/timeline.csv, then draws every frame of every video with the
same code the page uses and encodes them with ffmpeg. Frame-exact, faster than
real time, no screen recording.

Setup (once):
    pip install playwright imageio-ffmpeg
    python -m playwright install chromium

Run:
    python render_videos.py --url http://localhost:8000/ --out videos
    python render_videos.py --url https://antydata.com/ --out videos
    python render_videos.py --url ... --only stale,combined     # subset
    python render_videos.py --url ... --split                   # with the split-screen parkour clip
    python render_videos.py --url ... --no-music                # silent

Then copy the MP4s next to fallback_demo.mp4 so FastAPI serves them from
/api/downloads/.
"""
import argparse
import base64
import os
import tempfile
import shutil
import subprocess
import sys

from playwright.sync_api import sync_playwright


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found: install it, or `pip install imageio-ffmpeg`.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="page that contains the roadmap section")
    ap.add_argument("--out", default="videos")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--only", default="", help="comma-separated program ids (default: all)")
    ap.add_argument("--split", action="store_true", help="add the split-screen parkour clip (bottom half)")
    ap.add_argument("--no-music", action="store_true", help="render without background music")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ff = ffmpeg_exe()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(a.url, wait_until="networkidle")
        page.wait_for_function("window.RoadmapVideo && window.RoadmapVideo.ready", timeout=30000)
        page.evaluate("RoadmapVideo.ready")
        page.evaluate("document.fonts ? document.fonts.ready.then(() => 1) : 1")
        programs = page.evaluate("RoadmapVideo.programs()")
        if len(programs) <= 1:
            print("Only the overview is available — timeline.csv did not load, so no scenario videos.")
        if a.only:
            keep = {x.strip() for x in a.only.split(",")}
            programs = [pr for pr in programs if pr["id"] in keep]
        for pr in programs:
            out = os.path.join(a.out, pr["file"] + ("_split" if a.split else "") + ".mp4")
            n = int(pr["duration"] * a.fps)
            wav = None
            if not a.no_music:
                b64 = page.evaluate("id => RoadmapVideo.musicWav(id)", pr["id"])
                if b64:
                    wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                    wav.write(base64.b64decode(b64)); wav.close()
            cmd = [ff, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(a.fps), "-c:v", "mjpeg", "-i", "-"]
            if wav:
                cmd += ["-i", wav.name, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "160k", "-shortest"]
            cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", out]
            enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            for k in range(0, n, 30):
                frames = page.evaluate(
                    """([id, k, n, fps, split]) => {
                        const c = document.createElement('canvas'); c.width = RoadmapVideo.W; c.height = RoadmapVideo.H * (split ? 2 : 1);
                        const x = c.getContext('2d'), out = [];
                        for (let i = k; i < Math.min(k + 30, n); i++) {
                          RoadmapVideo.drawFrame(x, i / fps, id, split); out.push(c.toDataURL('image/jpeg', 0.93));
                        }
                        return out; }""", [pr["id"], k, n, a.fps, a.split])
                for u in frames:
                    enc.stdin.write(base64.b64decode(u.split(",", 1)[1]))
            enc.stdin.close()
            enc.wait()
            if wav:
                os.unlink(wav.name)
            print(f"✓ {out}  ({pr['title']}, {pr['duration']} s)")
        browser.close()


if __name__ == "__main__":
    main()
