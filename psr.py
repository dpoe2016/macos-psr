#!/usr/bin/env python3
"""
macOS Problem Steps Recorder (PSR)
Similar to Windows PSR — records screenshots on every mouse click
and generates an HTML report with annotated steps.

Usage:
    python3 psr.py [--output DIR] [--delay SECONDS]

Controls:
    - Press ESC to stop recording and generate the report.
    - Each mouse click captures a screenshot with a red circle at the click position.

Requires macOS Accessibility permissions for the terminal/IDE running this script.
"""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pynput import mouse, keyboard


class StepRecorder:
    def __init__(self, output_dir: str, click_delay: float = 0.3):
        self.output_dir = Path(output_dir)
        self.screenshots_dir = self.output_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.click_delay = click_delay
        self.steps: list[dict] = []
        self.step_count = 0
        self.recording = True
        self.start_time = datetime.now()
        self.last_click_time = 0.0
        self.mouse_listener = None
        self.keyboard_listener = None

    def _get_display_for_point(self, x: int, y: int):
        """Find which display contains the given point and return (displayID, bounds)."""
        from Quartz import (
            CGGetDisplaysWithPoint,
            CGDisplayBounds,
            CGMainDisplayID,
            CGPoint,
        )

        point = CGPoint(x, y)
        max_displays = 16
        err, display_ids, count = CGGetDisplaysWithPoint(point, max_displays, None, None)
        if err == 0 and count > 0:
            did = display_ids[0]
            bounds = CGDisplayBounds(did)
            return did, bounds

        # Fallback: main display
        did = CGMainDisplayID()
        bounds = CGDisplayBounds(did)
        return did, bounds

    def capture_screenshot(self, x: int, y: int) -> tuple[str | None, dict]:
        """Capture a screenshot of the display where the mouse is located."""
        self.step_count += 1
        filename = f"step_{self.step_count:04d}.png"
        filepath = self.screenshots_dir / filename

        try:
            display_id, bounds = self._get_display_for_point(x, y)
            origin_x = int(bounds.origin.x)
            origin_y = int(bounds.origin.y)
            width = int(bounds.size.width)
            height = int(bounds.size.height)

            # screencapture -C includes cursor, -R captures a specific rect: x,y,w,h
            rect = f"{origin_x},{origin_y},{width},{height}"
            subprocess.run(
                ["screencapture", "-C", "-R", rect, str(filepath)],
                check=True,
                capture_output=True,
            )
            display_info = {
                "origin_x": origin_x,
                "origin_y": origin_y,
                "logical_w": width,
                "logical_h": height,
            }
            return str(filepath), display_info
        except Exception:
            return None, {}

    def annotate_screenshot(self, filepath: str, x: int, y: int, display_info: dict) -> str:
        """Draw a red circle at the click position on the screenshot."""
        img = Image.open(filepath)

        # Compute scale factor (Retina displays have 2x pixel density)
        logical_w = display_info.get("logical_w", img.width)
        scale = img.width / logical_w if logical_w > 0 else 1

        # Click position relative to this display's origin
        rel_x = x - display_info.get("origin_x", 0)
        rel_y = y - display_info.get("origin_y", 0)
        sx, sy = int(rel_x * scale), int(rel_y * scale)
        draw = ImageDraw.Draw(img)
        radius = int(22 * scale)
        draw.ellipse(
            [sx - radius, sy - radius, sx + radius, sy + radius],
            outline="red",
            width=int(4 * scale),
        )
        # Crosshair
        cross = int(8 * scale)
        lw = int(2 * scale)
        draw.line([sx - cross, sy, sx + cross, sy], fill="red", width=lw)
        draw.line([sx, sy - cross, sx, sy + cross], fill="red", width=lw)

        # Step number label
        font_size = int(18 * scale)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
        except Exception:
            font = ImageFont.load_default()
        label = f"Step {self.step_count}"
        label_x = sx + radius + int(6 * scale)
        label_y = sy - font_size // 2
        # Background
        bbox = draw.textbbox((label_x, label_y), label, font=font)
        pad = int(4 * scale)
        draw.rectangle(
            [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad],
            fill="red",
        )
        draw.text((label_x, label_y), label, fill="white", font=font)

        img.save(filepath)
        return filepath

    def get_window_info(self) -> str:
        """Get the title of the frontmost window."""
        script = '''
        tell application "System Events"
            set frontApp to name of first application process whose frontmost is true
            try
                set winTitle to name of front window of (first application process whose frontmost is true)
                return frontApp & " — " & winTitle
            on error
                return frontApp
            end try
        end tell
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3,
            )
            return result.stdout.strip() or "Unknown"
        except Exception:
            return "Unknown"

    def on_click(self, x: int, y: int, button, pressed: bool):
        if not pressed or not self.recording:
            return

        now = time.time()
        if now - self.last_click_time < self.click_delay:
            return
        self.last_click_time = now

        # Capture in a thread to avoid blocking the listener
        trigger = str(button).replace("Button.", "") + " click"
        threading.Thread(target=self._record_step, args=(x, y, trigger), daemon=True).start()

    def _get_mouse_position(self) -> tuple[int, int]:
        """Get current mouse cursor position via Quartz."""
        from Quartz import CGEventCreate
        event = CGEventCreate(None)
        from Quartz import CGEventGetLocation
        loc = CGEventGetLocation(event)
        return int(loc.x), int(loc.y)

    def _record_step(self, x: int, y: int, trigger: str):
        window_info = self.get_window_info()
        filepath, display_info = self.capture_screenshot(x, y)
        if not filepath:
            return
        self.annotate_screenshot(filepath, x, y, display_info)
        elapsed = (datetime.now() - self.start_time).total_seconds()
        step = {
            "number": self.step_count,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "elapsed": f"{elapsed:.1f}s",
            "x": x,
            "y": y,
            "trigger": trigger,
            "window": window_info,
            "screenshot": os.path.relpath(filepath, self.output_dir),
        }
        self.steps.append(step)
        print(f"  [{step['timestamp']}] Step {step['number']}: {trigger} ({x}, {y}) in {window_info}")

    def on_key_release(self, key):
        if key == keyboard.Key.esc:
            self.recording = False
            return False  # Stop keyboard listener

        if not self.recording:
            return

        # Enter/Return triggers a recording step
        if key == keyboard.Key.enter:
            now = time.time()
            if now - self.last_click_time < self.click_delay:
                return
            self.last_click_time = now
            x, y = self._get_mouse_position()
            threading.Thread(target=self._record_step, args=(x, y, "Enter key"), daemon=True).start()

    def generate_report(self):
        """Generate an HTML report of all recorded steps."""
        report_path = self.output_dir / "report.html"
        timestamp = self.start_time.strftime("%Y-%m-%d %H:%M:%S")
        duration = (datetime.now() - self.start_time).total_seconds()

        steps_html = ""
        for step in self.steps:
            steps_html += f"""
        <div class="step">
            <div class="step-header">
                <span class="step-num">Step {step['number']}</span>
                <span class="step-time">{step['timestamp']} ({step['elapsed']})</span>
                <span class="step-info">{step['trigger']} at ({step['x']}, {step['y']})</span>
            </div>
            <div class="step-window">Window: {step['window']}</div>
            <a href="{step['screenshot']}" target="_blank">
                <img src="{step['screenshot']}" alt="Step {step['number']}">
            </a>
        </div>
"""

        html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>PSR Report — {timestamp}</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
           background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
    h1 {{ text-align: center; padding: 20px 0; color: #fff; }}
    .meta {{ text-align: center; color: #888; margin-bottom: 30px; }}
    .step {{ background: #16213e; border-radius: 12px; margin: 20px auto; max-width: 1200px;
             padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.3); }}
    .step-header {{ display: flex; align-items: center; gap: 16px; margin-bottom: 8px; }}
    .step-num {{ background: #e94560; color: white; padding: 4px 12px; border-radius: 6px;
                 font-weight: 700; font-size: 14px; }}
    .step-time {{ color: #888; font-size: 13px; }}
    .step-info {{ color: #aaa; font-size: 13px; }}
    .step-window {{ color: #64dfdf; font-size: 13px; margin-bottom: 12px; }}
    .step img {{ width: 100%; border-radius: 8px; border: 1px solid #333;
                 cursor: pointer; transition: transform 0.2s; }}
    .step img:hover {{ transform: scale(1.01); }}
    .summary {{ text-align: center; padding: 30px; color: #888; }}
</style>
</head>
<body>
<h1>macOS Problem Steps Recorder</h1>
<div class="meta">
    Recording started: {timestamp} &bull; Duration: {duration:.0f}s &bull; Steps: {len(self.steps)}
</div>
{steps_html}
<div class="summary">
    End of recording &mdash; {len(self.steps)} steps captured in {duration:.0f} seconds.
</div>
</body>
</html>"""

        report_path.write_text(html, encoding="utf-8")
        return str(report_path)

    def start(self):
        print()
        print("╔══════════════════════════════════════════════╗")
        print("║     macOS Problem Steps Recorder (PSR)       ║")
        print("╠══════════════════════════════════════════════╣")
        print("║  Recording...                                ║")
        print("║  Every mouse click captures a screenshot.    ║")
        print("║  Press ESC to stop and generate the report.  ║")
        print("╚══════════════════════════════════════════════╝")
        print()
        print(f"  Output: {self.output_dir}")
        print()

        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.keyboard_listener = keyboard.Listener(on_release=self.on_key_release)

        self.mouse_listener.start()
        self.keyboard_listener.start()

        self.keyboard_listener.join()  # Block until ESC
        self.recording = False
        self.mouse_listener.stop()

        # Wait for pending capture threads
        time.sleep(0.5)

        print()
        print(f"  Recording stopped. {len(self.steps)} steps captured.")

        if self.steps:
            report = self.generate_report()
            print(f"  Report: {report}")
            print()
            subprocess.run(["open", report])
        else:
            print("  No steps recorded.")


def main():
    parser = argparse.ArgumentParser(description="macOS Problem Steps Recorder")
    parser.add_argument(
        "-o", "--output",
        help="Output directory (default: ~/Desktop/PSR_<timestamp>)",
    )
    parser.add_argument(
        "-d", "--delay",
        type=float, default=0.3,
        help="Minimum delay between captures in seconds (default: 0.3)",
    )
    args = parser.parse_args()

    if args.output:
        output_dir = args.output
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.expanduser(f"~/Desktop/PSR_{ts}")

    recorder = StepRecorder(output_dir, click_delay=args.delay)

    # Graceful exit on Ctrl+C
    def sigint_handler(sig, frame):
        recorder.recording = False
        if recorder.keyboard_listener:
            recorder.keyboard_listener.stop()

    signal.signal(signal.SIGINT, sigint_handler)
    recorder.start()


if __name__ == "__main__":
    main()
