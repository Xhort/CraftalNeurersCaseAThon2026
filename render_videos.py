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
    python render_videos.py --url http://localhost:8000/roadmap-section.html --out lade_eta_lab/demo/videos
    python render_videos.py --url https://antydata.com/roadmap-section.html --out lade_eta_lab/demo/videos
    python render_videos.py --url ... --only stale,combined     # subset

The default output directory is already served by FastAPI and copied into the
Cloudflare static bundle at /lade_eta_lab/demo/videos/.
"""
import argparse
import base64
import os
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
    ap.add_argument("--url", default="http://localhost:8000/roadmap-section.html",
                    help="standalone roadmap page (default: local FastAPI route)")
    ap.add_argument("--out", default="lade_eta_lab/demo/videos")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--only", default="", help="comma-separated program ids (default: all)")
    ap.add_argument("--chrome", default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    help="installed Chrome/Chromium path; falls back to Playwright Chromium")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ff = ffmpeg_exe()

    with sync_playwright() as p:
        launch = {"headless": True}
        if a.chrome and os.path.exists(a.chrome):
            launch["executable_path"] = a.chrome
        browser = p.chromium.launch(**launch)
        page = browser.new_page()
        page.goto(a.url, wait_until="networkidle")
        page.wait_for_function("window.RoadmapVideo && window.RoadmapVideo.ready", timeout=30000)
        page.evaluate("RoadmapVideo.ready")
        page.evaluate("document.fonts ? document.fonts.ready.then(() => 1) : 1")
        programs = page.evaluate("RoadmapVideo.programs()")
        if a.only:
            keep = {x.strip() for x in a.only.split(",")}
            programs = [pr for pr in programs if pr["id"] in keep]
        if len(programs) <= 1:
            print("Only the overview is available — timeline.csv did not load, so no scenario videos.")
        for pr in programs:
            out = os.path.join(a.out, pr["file"] + ".mp4")
            n = int(pr["duration"] * a.fps)
            enc = subprocess.Popen(
                [ff, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(a.fps),
                 "-c:v", "mjpeg", "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-crf", "20", "-movflags", "+faststart", out],
                stdin=subprocess.PIPE)
            for k in range(0, n, 30):
                frames = page.evaluate(
                    """([id, k, n, fps]) => {
                        const c = document.createElement('canvas'); c.width = RoadmapVideo.W; c.height = RoadmapVideo.H;
                        const x = c.getContext('2d'), out = [];
                        for (let i = k; i < Math.min(k + 30, n); i++) {
                          RoadmapVideo.drawFrame(x, i / fps, id); out.push(c.toDataURL('image/jpeg', 0.93));
                        }
                        return out; }""", [pr["id"], k, n, a.fps])
                for u in frames:
                    enc.stdin.write(base64.b64decode(u.split(",", 1)[1]))
            enc.stdin.close()
            enc.wait()
            print(f"✓ {out}  ({pr['title']}, {pr['duration']} s)")
        browser.close()


if __name__ == "__main__":
    main()
