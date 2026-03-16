#!/usr/bin/env python3
"""
macOS Problem Steps Recorder (PSR)
Similar to Windows PSR — records screenshots on every mouse click/Enter
and generates an HTML report with annotated steps.

Includes on-screen annotation tools (rectangle, arrow, freehand, highlight)
via a transparent overlay window.

Usage:
    python3 psr.py [--output DIR] [--delay SECONDS] [--pdf] [--no-ai]

Controls — Recording mode:
    Mouse click / Enter  — Capture a step
    ESC                  — Stop recording and generate report

Controls — Annotation tools:
    F1  — Rectangle tool
    F2  — Arrow tool
    F3  — Freehand tool
    F4  — Highlight tool
    F5  — Clear all annotations
    F6  — Undo last annotation
    F7  — Toggle color (red → blue → green → yellow → red)
    F8  — Exit draw mode (back to recording)

    While a tool is active, drag on screen to draw.
    Click without dragging to pass through to the app below.

Requires macOS Accessibility + Screen Recording permissions.
"""

import argparse
import base64
import math
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import objc
from AppKit import (
    NSApplication,
    NSApp,
    NSWindow,
    NSView,
    NSColor,
    NSBezierPath,
    NSFont,
    NSAttributedString,
    NSForegroundColorAttributeName,
    NSFontAttributeName,
    NSScreen,
    NSBorderlessWindowMask,
    NSEvent,
    NSKeyDownMask,
    NSMakeRect,
    NSMakePoint,
)
from Foundation import NSObject
from Quartz import (
    CGGetDisplaysWithPoint,
    CGDisplayBounds,
    CGMainDisplayID,
    CGPoint,
    CGEventCreate,
    CGEventGetLocation,
)
from PIL import Image, ImageDraw, ImageFont

TOOLS = {
    "rectangle": "F1",
    "arrow": "F2",
    "freehand": "F3",
    "highlight": "F4",
}

COLORS = [
    ("Red", NSColor.redColor()),
    ("Blue", NSColor.blueColor()),
    ("Green", NSColor.greenColor()),
    ("Yellow", NSColor.yellowColor()),
]


class Annotation:
    """A single drawn annotation."""
    def __init__(self, tool: str, color, points: list, screen_frame=None):
        self.tool = tool
        self.color = color
        self.points = points  # list of (x, y) in screen coords
        self.screen_frame = screen_frame


class OverlayView(NSView):
    """Custom NSView that renders annotations."""

    def initWithFrame_(self, frame):
        self = objc.super(OverlayView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.annotations = []
        self.current_annotation = None
        self.active_tool = None
        self.active_color = COLORS[0][1]
        self.drag_start = None
        self.is_drawing = False
        self.recorder = None  # back-reference to StepRecorder
        self.status_text = ""
        return self

    def drawRect_(self, rect):
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(rect)
        all_items = list(self.annotations)
        if self.current_annotation:
            all_items.append(self.current_annotation)
        for ann in all_items:
            self._draw_annotation(ann)
        if self.status_text:
            self._draw_status(self.status_text)

    def _draw_status(self, text):
        """Draw a status bar at the top center of the screen."""
        attrs = {
            NSForegroundColorAttributeName: NSColor.whiteColor(),
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(14),
        }
        astr = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        size = astr.size()
        frame = self.frame()
        x = (frame.size.width - size.width) / 2 - 12
        y = frame.size.height - size.height - 16
        bg_rect = NSMakeRect(x - 10, y - 6, size.width + 24, size.height + 12)
        bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bg_rect, 8, 8)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0, 0, 0, 0.75).set()
        bg.fill()
        astr.drawAtPoint_(NSMakePoint(x + 2, y))

    def _draw_annotation(self, ann):
        if not ann.points or len(ann.points) < 1:
            return
        color = ann.color
        if ann.tool == "rectangle":
            self._draw_rect(ann.points, color)
        elif ann.tool == "arrow":
            self._draw_arrow(ann.points, color)
        elif ann.tool == "freehand":
            self._draw_freehand(ann.points, color)
        elif ann.tool == "highlight":
            self._draw_highlight(ann.points, color)

    def _draw_rect(self, points, color):
        if len(points) < 2:
            return
        x1, y1 = points[0]
        x2, y2 = points[-1]
        r = NSMakeRect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
        path = NSBezierPath.bezierPathWithRect_(r)
        path.setLineWidth_(3.0)
        color.set()
        path.stroke()

    def _draw_arrow(self, points, color):
        if len(points) < 2:
            return
        x1, y1 = points[0]
        x2, y2 = points[-1]
        path = NSBezierPath.bezierPath()
        path.moveToPoint_(NSMakePoint(x1, y1))
        path.lineToPoint_(NSMakePoint(x2, y2))
        path.setLineWidth_(3.0)
        color.set()
        path.stroke()
        # Arrowhead
        angle = math.atan2(y2 - y1, x2 - x1)
        head_len = 20
        for offset in [2.5, -2.5]:
            a = angle + math.pi - math.radians(offset * 12)
            hx = x2 + head_len * math.cos(a)
            hy = y2 + head_len * math.sin(a)
            hp = NSBezierPath.bezierPath()
            hp.moveToPoint_(NSMakePoint(x2, y2))
            hp.lineToPoint_(NSMakePoint(hx, hy))
            hp.setLineWidth_(3.0)
            hp.stroke()

    def _draw_freehand(self, points, color):
        if len(points) < 2:
            return
        path = NSBezierPath.bezierPath()
        path.moveToPoint_(NSMakePoint(*points[0]))
        for p in points[1:]:
            path.lineToPoint_(NSMakePoint(*p))
        path.setLineWidth_(3.0)
        color.set()
        path.stroke()

    def _draw_highlight(self, points, color):
        if len(points) < 2:
            return
        x1, y1 = points[0]
        x2, y2 = points[-1]
        r = NSMakeRect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
        path = NSBezierPath.bezierPathWithRect_(r)
        highlight_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            1.0, 1.0, 0.0, 0.3
        )
        highlight_color.set()
        path.fill()
        color.set()
        path.setLineWidth_(2.0)
        path.stroke()

    def mouseDown_(self, event):
        if not self.active_tool:
            return
        loc = event.locationInWindow()
        self.drag_start = (loc.x, loc.y)
        self.is_drawing = False
        if self.active_tool == "freehand":
            self.current_annotation = Annotation(
                self.active_tool, self.active_color, [(loc.x, loc.y)]
            )

    def mouseDragged_(self, event):
        if not self.active_tool or not self.drag_start:
            return
        loc = event.locationInWindow()
        self.is_drawing = True
        if self.active_tool == "freehand":
            if self.current_annotation:
                self.current_annotation.points.append((loc.x, loc.y))
        else:
            self.current_annotation = Annotation(
                self.active_tool, self.active_color,
                [self.drag_start, (loc.x, loc.y)]
            )
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        if not self.active_tool:
            return
        if not self.is_drawing:
            # No drag happened — pass click through to the app below
            self.drag_start = None
            self.current_annotation = None
            return
        if self.current_annotation and len(self.current_annotation.points) >= 2:
            self.annotations.append(self.current_annotation)
        self.current_annotation = None
        self.drag_start = None
        self.is_drawing = False
        self.setNeedsDisplay_(True)

    def acceptsFirstResponder(self):
        return True


class AnnotationOverlay:
    """Manages transparent overlay windows on all screens."""

    def __init__(self):
        self.windows = []
        self.views = []
        self.active_tool = None
        self.color_index = 0
        self.draw_mode = False

    def setup(self):
        """Create overlay windows for all screens."""
        for screen in NSScreen.screens():
            frame = screen.frame()
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                frame,
                NSBorderlessWindowMask,
                2,  # NSBackingStoreBuffered
                False,
            )
            window.setLevel_(1000)  # above everything
            window.setOpaque_(False)
            window.setBackgroundColor_(NSColor.clearColor())
            window.setIgnoresMouseEvents_(True)  # click-through by default
            window.setHasShadow_(False)
            window.setCollectionBehavior_(
                1 << 0 | 1 << 4  # canJoinAllSpaces | fullScreenAuxiliary
            )

            view = OverlayView.alloc().initWithFrame_(frame)
            window.setContentView_(view)
            window.orderFrontRegardless()

            self.windows.append(window)
            self.views.append(view)

    def enter_draw_mode(self, tool: str):
        """Enable drawing with the given tool."""
        self.draw_mode = True
        self.active_tool = tool
        color = COLORS[self.color_index][1]
        color_name = COLORS[self.color_index][0]
        for i, w in enumerate(self.windows):
            w.setIgnoresMouseEvents_(False)
            w.makeKeyAndOrderFront_(None)
            self.views[i].active_tool = tool
            self.views[i].active_color = color
            self.views[i].status_text = (
                f"Draw Mode: {tool.capitalize()} | Color: {color_name} | "
                f"F1-F4: Tools  F5: Clear  F6: Undo  F7: Color  F8: Done"
            )
            self.views[i].setNeedsDisplay_(True)

    def exit_draw_mode(self):
        """Return to recording mode (click-through)."""
        self.draw_mode = False
        self.active_tool = None
        for i, w in enumerate(self.windows):
            w.setIgnoresMouseEvents_(True)
            self.views[i].active_tool = None
            self.views[i].status_text = ""
            self.views[i].setNeedsDisplay_(True)

    def clear_all(self):
        for v in self.views:
            v.annotations.clear()
            v.setNeedsDisplay_(True)

    def undo_last(self):
        for v in self.views:
            if v.annotations:
                v.annotations.pop()
                v.setNeedsDisplay_(True)

    def cycle_color(self):
        self.color_index = (self.color_index + 1) % len(COLORS)
        color = COLORS[self.color_index][1]
        color_name = COLORS[self.color_index][0]
        for v in self.views:
            v.active_color = color
            if self.draw_mode and self.active_tool:
                v.status_text = (
                    f"Draw Mode: {self.active_tool.capitalize()} | Color: {color_name} | "
                    f"F1-F4: Tools  F5: Clear  F6: Undo  F7: Color  F8: Done"
                )
            v.setNeedsDisplay_(True)

    def refresh(self):
        for v in self.views:
            v.setNeedsDisplay_(True)


class StepRecorder:
    def __init__(self, output_dir: str, click_delay: float = 0.3, window_only: bool = False):
        self.output_dir = Path(output_dir)
        self.screenshots_dir = self.output_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.click_delay = click_delay
        self.window_only = window_only
        self.steps: list[dict] = []
        self.step_count = 0
        self.recording = True
        self.start_time = datetime.now()
        self.last_click_time = 0.0
        self.overlay: AnnotationOverlay | None = None

    def _get_display_for_point(self, x: int, y: int):
        point = CGPoint(x, y)
        max_displays = 16
        err, display_ids, count = CGGetDisplaysWithPoint(point, max_displays, None, None)
        if err == 0 and count > 0:
            did = display_ids[0]
            bounds = CGDisplayBounds(did)
            return did, bounds
        did = CGMainDisplayID()
        bounds = CGDisplayBounds(did)
        return did, bounds

    def _get_frontmost_window_id(self) -> int | None:
        """Get the window ID of the frontmost application's front window."""
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
        if not windows:
            return None
        # Find frontmost non-overlay window (layer 0 = normal windows)
        for w in windows:
            if w.get("kCGWindowLayer", 999) == 0 and w.get("kCGWindowOwnerName", "") != "psr":
                bounds = w.get("kCGWindowBounds")
                if bounds and bounds.get("Width", 0) > 50:
                    return w.get("kCGWindowNumber")
        return None

    def capture_screenshot(self, x: int, y: int) -> tuple[str | None, dict]:
        self.step_count += 1
        filename = f"step_{self.step_count:04d}.png"
        filepath = self.screenshots_dir / filename
        try:
            if self.window_only:
                wid = self._get_frontmost_window_id()
                if wid:
                    subprocess.run(
                        ["screencapture", "-C", "-l", str(wid), str(filepath)],
                        check=True, capture_output=True,
                    )
                    # For window capture, click pos is relative to screen;
                    # we need the window bounds to calculate relative position
                    from Quartz import (
                        CGWindowListCopyWindowInfo,
                        kCGWindowListOptionIncludingWindow,
                        kCGNullWindowID,
                    )
                    wins = CGWindowListCopyWindowInfo(kCGWindowListOptionIncludingWindow, wid)
                    if wins and len(wins) > 0:
                        b = wins[0].get("kCGWindowBounds", {})
                        display_info = {
                            "origin_x": int(b.get("X", 0)),
                            "origin_y": int(b.get("Y", 0)),
                            "logical_w": int(b.get("Width", 1)),
                            "logical_h": int(b.get("Height", 1)),
                        }
                    else:
                        display_info = {"origin_x": 0, "origin_y": 0, "logical_w": 1, "logical_h": 1}
                    return str(filepath), display_info

            # Full screen capture (default or fallback)
            display_id, bounds = self._get_display_for_point(x, y)
            origin_x = int(bounds.origin.x)
            origin_y = int(bounds.origin.y)
            width = int(bounds.size.width)
            height = int(bounds.size.height)
            rect = f"{origin_x},{origin_y},{width},{height}"
            subprocess.run(
                ["screencapture", "-C", "-R", rect, str(filepath)],
                check=True, capture_output=True,
            )
            display_info = {
                "origin_x": origin_x, "origin_y": origin_y,
                "logical_w": width, "logical_h": height,
            }
            return str(filepath), display_info
        except Exception:
            return None, {}

    def annotate_screenshot(self, filepath: str, x: int, y: int, display_info: dict) -> str:
        img = Image.open(filepath)
        logical_w = display_info.get("logical_w", img.width)
        scale = img.width / logical_w if logical_w > 0 else 1
        rel_x = x - display_info.get("origin_x", 0)
        rel_y = y - display_info.get("origin_y", 0)
        sx, sy = int(rel_x * scale), int(rel_y * scale)
        draw = ImageDraw.Draw(img)
        radius = int(22 * scale)
        draw.ellipse(
            [sx - radius, sy - radius, sx + radius, sy + radius],
            outline="red", width=int(4 * scale),
        )
        cross = int(8 * scale)
        lw = int(2 * scale)
        draw.line([sx - cross, sy, sx + cross, sy], fill="red", width=lw)
        draw.line([sx, sy - cross, sx, sy + cross], fill="red", width=lw)
        font_size = int(18 * scale)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
        except Exception:
            font = ImageFont.load_default()
        label = f"Step {self.step_count}"
        label_x = sx + radius + int(6 * scale)
        label_y = sy - font_size // 2
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

    def _get_mouse_position(self) -> tuple[int, int]:
        event = CGEventCreate(None)
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
            "x": x, "y": y,
            "trigger": trigger,
            "window": window_info,
            "screenshot": os.path.relpath(filepath, self.output_dir),
        }
        self.steps.append(step)
        print(f"  [{step['timestamp']}] Step {step['number']}: {trigger} ({x}, {y}) in {window_info}")

    def generate_report(self):
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

    def _ai_describe_step(self, step: dict) -> str:
        """Use Claude Vision to describe what the user clicked on."""
        try:
            import anthropic
        except ImportError:
            return "(anthropic SDK not installed — skipping AI description)"

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return "(ANTHROPIC_API_KEY not set — skipping AI description)"

        screenshot_path = self.output_dir / step["screenshot"]
        if not screenshot_path.exists():
            return "(Screenshot not found)"

        # Resize for API efficiency (max 1200px wide)
        try:
            img = Image.open(screenshot_path)
            if img.width > 1200:
                ratio = 1200 / img.width
                img = img.resize((1200, int(img.height * ratio)), Image.LANCZOS)
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            return f"(Error reading screenshot: {e})"

        client = anthropic.Anthropic(api_key=api_key)
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": img_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"This screenshot was taken during a screen recording session. "
                                f"The user performed a '{step['trigger']}' at position "
                                f"({step['x']}, {step['y']}) in the window '{step['window']}'. "
                                f"The red circle with crosshair marks the exact click position.\n\n"
                                f"Describe in 1-3 concise sentences IN GERMAN what the user "
                                f"clicked on and what action they likely intended. "
                                f"Focus on the UI element at the marked position."
                            ),
                        },
                    ],
                }],
            )
            return response.content[0].text
        except Exception as e:
            return f"(AI analysis failed: {e})"

    def generate_pdf(self, use_ai: bool = True) -> str:
        """Generate a PDF report with one page per step and optional AI descriptions."""
        from fpdf import FPDF

        pdf_path = self.output_dir / "report.pdf"
        timestamp = self.start_time.strftime("%Y-%m-%d %H:%M:%S")
        duration = (datetime.now() - self.start_time).total_seconds()

        pdf = FPDF(orientation="L", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=False)

        # --- Title page ---
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 28)
        pdf.ln(40)
        pdf.cell(0, 15, "macOS Problem Steps Recorder", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 14)
        pdf.ln(10)
        pdf.cell(0, 10, f"Recording: {timestamp}", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 10, f"Duration: {duration:.0f}s  |  Steps: {len(self.steps)}", align="C", new_x="LMARGIN", new_y="NEXT")
        if use_ai:
            pdf.ln(15)
            pdf.set_font("Helvetica", "I", 11)
            pdf.cell(0, 10, "AI-powered step descriptions included", align="C", new_x="LMARGIN", new_y="NEXT")

        # --- Step pages ---
        for i, step in enumerate(self.steps):
            print(f"  Generating PDF page {i+1}/{len(self.steps)}...", end="")

            # Get AI description
            ai_text = ""
            if use_ai:
                print(" (analyzing with AI...)", end="")
                ai_text = self._ai_describe_step(step)
            print()

            pdf.add_page()

            # Header bar
            pdf.set_fill_color(30, 30, 60)
            pdf.rect(0, 0, 297, 18, style="F")
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(255, 255, 255)
            pdf.set_xy(8, 3)
            pdf.cell(40, 12, f"Step {step['number']}")
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(60, 12, f"{step['timestamp']}  ({step['elapsed']})")
            pdf.cell(80, 12, f"{step['trigger']} at ({step['x']}, {step['y']})")
            pdf.cell(0, 12, step["window"], align="R")

            pdf.set_text_color(0, 0, 0)

            # Screenshot
            screenshot_path = str(self.output_dir / step["screenshot"])
            if os.path.exists(screenshot_path):
                # Calculate image dimensions to fit page
                img = Image.open(screenshot_path)
                img_w, img_h = img.size
                max_w = 277  # mm, with margins
                max_h = 145 if ai_text else 175  # leave room for AI text
                ratio = min(max_w / img_w, max_h / img_h, 1.0)
                w_mm = img_w * ratio
                h_mm = img_h * ratio
                x_offset = (297 - w_mm) / 2

                pdf.image(screenshot_path, x=x_offset, y=20, w=w_mm, h=h_mm)
                text_y = 22 + h_mm
            else:
                text_y = 25

            # AI description
            if ai_text:
                pdf.set_xy(10, text_y + 2)
                pdf.set_fill_color(240, 240, 255)
                desc_h = pdf.get_string_width(ai_text) / 270 * 7 + 14
                desc_h = max(desc_h, 16)
                desc_h = min(desc_h, 50)
                pdf.rect(8, text_y, 281, desc_h, style="F")

                pdf.set_xy(12, text_y + 2)
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_text_color(80, 80, 120)
                pdf.cell(20, 5, "KI-Analyse: ")
                pdf.set_font("Helvetica", "", 9)
                pdf.set_text_color(40, 40, 40)
                pdf.set_xy(12, text_y + 7)
                pdf.multi_cell(270, 5, ai_text)

        pdf.output(str(pdf_path))
        return str(pdf_path)


class AppDelegate(NSObject):
    """NSApplication delegate that coordinates overlay and recording."""

    def applicationDidFinishLaunching_(self, notification):
        self.overlay = AnnotationOverlay()
        self.overlay.setup()
        self.recorder = self.recorder_ref
        self.recorder.overlay = self.overlay

        # Global key monitor for hotkeys (works even when our window isn't focused)
        NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSKeyDownMask, self.handleGlobalKey_
        )
        # Local key monitor (when our overlay is focused)
        NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSKeyDownMask, self.handleLocalKey_
        )

        # Start pynput listeners in background thread
        threading.Thread(target=self._run_listeners, daemon=True).start()

        self._print_banner()

    def _print_banner(self):
        mode = "Window" if self.recorder.window_only else "Screen"
        pdf_info = " + PDF" if getattr(self.recorder, 'pdf_enabled', False) else ""
        ai_info = " + AI" if getattr(self.recorder, 'ai_enabled', True) and pdf_info else ""
        print()
        print("╔═══════════════════════════════════════════════════════╗")
        print("║        macOS Problem Steps Recorder (PSR)             ║")
        print("╠═══════════════════════════════════════════════════════╣")
        print(f"║  Recording ({mode}{pdf_info}{ai_info})")
        print("║                                                       ║")
        print("║  Mouse click / Enter  — Capture step                  ║")
        print("║  Ctrl+1 / F1: Rectangle   Ctrl+2 / F2: Arrow           ║")
        print("║  Ctrl+3 / F3: Freehand    Ctrl+4 / F4: Highlight      ║")
        print("║  Ctrl+5 / F5: Clear all   Ctrl+6 / F6: Undo           ║")
        print("║  Ctrl+7 / F7: Color       Ctrl+8 / F8: Done           ║")
        print("║  ESC — Stop recording                                 ║")
        print("╚═══════════════════════════════════════════════════════╝")
        print()
        print(f"  Output: {self.recorder.output_dir}")
        print()

    def _run_listeners(self):
        from pynput import mouse as pmouse

        def on_click(x, y, button, pressed):
            if not pressed or not self.recorder.recording:
                return
            if self.overlay.draw_mode:
                return  # drawing, not recording
            now = time.time()
            if now - self.recorder.last_click_time < self.recorder.click_delay:
                return
            self.recorder.last_click_time = now
            trigger = str(button).replace("Button.", "") + " click"
            threading.Thread(
                target=self.recorder._record_step, args=(x, y, trigger), daemon=True
            ).start()

        self.mouse_listener = pmouse.Listener(on_click=on_click)
        self.mouse_listener.start()
        self.mouse_listener.join()

    def _handle_key(self, event):
        kc = event.keyCode()
        flags = event.modifierFlags()
        ctrl = bool(flags & (1 << 18))  # NSEventModifierFlagControl
        chars = event.charactersIgnoringModifiers() or ""
        print(f"  [KEY] keyCode={kc} ctrl={ctrl} char='{chars}' drawMode={self.overlay.draw_mode}")

        # Ctrl+1..8 for tools, ESC/Enter without modifiers
        # Key codes: 1=18, 2=19, 3=20, 4=21, 5=23, 6=22, 7=26, 8=28
        if ctrl and kc == 18:  # Ctrl+1 — Rectangle
            self.overlay.enter_draw_mode("rectangle")
        elif ctrl and kc == 19:  # Ctrl+2 — Arrow
            self.overlay.enter_draw_mode("arrow")
        elif ctrl and kc == 20:  # Ctrl+3 — Freehand
            self.overlay.enter_draw_mode("freehand")
        elif ctrl and kc == 21:  # Ctrl+4 — Highlight
            self.overlay.enter_draw_mode("highlight")
        elif ctrl and kc == 23:  # Ctrl+5 — Clear all
            self.overlay.clear_all()
        elif ctrl and kc == 22:  # Ctrl+6 — Undo
            self.overlay.undo_last()
        elif ctrl and kc == 26:  # Ctrl+7 — Cycle color
            self.overlay.cycle_color()
        elif ctrl and kc == 28:  # Ctrl+8 — Exit draw mode
            self.overlay.exit_draw_mode()
        # Also support F1-F8 (with fn key)
        elif not ctrl and kc == 122:  # F1
            self.overlay.enter_draw_mode("rectangle")
        elif not ctrl and kc == 120:  # F2
            self.overlay.enter_draw_mode("arrow")
        elif not ctrl and kc == 99:  # F3
            self.overlay.enter_draw_mode("freehand")
        elif not ctrl and kc == 118:  # F4
            self.overlay.enter_draw_mode("highlight")
        elif not ctrl and kc == 96:  # F5
            self.overlay.clear_all()
        elif not ctrl and kc == 97:  # F6
            self.overlay.undo_last()
        elif not ctrl and kc == 98:  # F7
            self.overlay.cycle_color()
        elif not ctrl and kc == 100:  # F8
            self.overlay.exit_draw_mode()
        elif kc == 53:  # ESC
            if self.overlay.draw_mode:
                self.overlay.exit_draw_mode()
            else:
                self._stop_recording()
        elif kc in (36, 76) and not ctrl:  # Enter / numpad Enter
            if not self.overlay.draw_mode and self.recorder.recording:
                now = time.time()
                if now - self.recorder.last_click_time >= self.recorder.click_delay:
                    self.recorder.last_click_time = now
                    x, y = self.recorder._get_mouse_position()
                    threading.Thread(
                        target=self.recorder._record_step,
                        args=(x, y, "Enter key"), daemon=True
                    ).start()

    def handleGlobalKey_(self, event):
        self._handle_key(event)

    def handleLocalKey_(self, event):
        self._handle_key(event)
        return event

    def _stop_recording(self):
        self.recorder.recording = False
        if hasattr(self, 'mouse_listener'):
            self.mouse_listener.stop()
        time.sleep(0.5)

        print()
        print(f"  Recording stopped. {len(self.recorder.steps)} steps captured.")

        if self.recorder.steps:
            report = self.recorder.generate_report()
            print(f"  HTML Report: {report}")

            if getattr(self.recorder, 'pdf_enabled', False):
                use_ai = getattr(self.recorder, 'ai_enabled', True)
                print()
                print("  Generating PDF report...")
                pdf_path = self.recorder.generate_pdf(use_ai=use_ai)
                print(f"  PDF Report: {pdf_path}")
                print()
                subprocess.run(["open", pdf_path])
            else:
                print()
                subprocess.run(["open", report])
        else:
            print("  No steps recorded.")

        NSApp.terminate_(None)


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
    parser.add_argument(
        "--pdf", action="store_true", default=False,
        help="Generate PDF report (in addition to HTML)",
    )
    parser.add_argument(
        "--no-ai", action="store_true", default=False,
        help="Skip AI-generated step descriptions in PDF",
    )
    parser.add_argument(
        "--fullscreen", action="store_true", default=False,
        help="Capture the full screen instead of only the active window (default: active window)",
    )
    args = parser.parse_args()

    if args.output:
        output_dir = args.output
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.expanduser(f"~/Desktop/PSR_{ts}")

    recorder = StepRecorder(output_dir, click_delay=args.delay, window_only=not args.fullscreen)
    recorder.pdf_enabled = args.pdf
    recorder.ai_enabled = not args.no_ai

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    delegate = AppDelegate.alloc().init()
    delegate.recorder_ref = recorder
    app.setDelegate_(delegate)

    # Graceful Ctrl+C
    def sigint_handler(sig, frame):
        recorder.recording = False
        NSApp.terminate_(None)

    signal.signal(signal.SIGINT, sigint_handler)

    app.run()


if __name__ == "__main__":
    main()
