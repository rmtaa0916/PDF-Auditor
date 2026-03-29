import os
import io
import math
import tempfile
import threading
import traceback
import base64

# -------------------------------------------------------------------
# Python 3.10+ compatibility patch for older reportlab builds
# -------------------------------------------------------------------
if not hasattr(base64, "decodestring"):
    base64.decodestring = base64.decodebytes
if not hasattr(base64, "encodestring"):
    base64.encodestring = base64.encodebytes

import cv2
import numpy as np
from PIL import Image
from pypdf import PdfReader, PdfWriter

try:
    import fitz  # Optional desktop-only fallback renderer.
except Exception:
    fitz = None

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

# Android-safe patch: force ReportLab to use the pure-Python PDF escaping path.
try:
    from reportlab.lib import rl_accel as _rl_accel
    _py_escape_pdf = getattr(_rl_accel, "_py_escapePDF", None)
    if callable(_py_escape_pdf):
        try:
            _rl_accel.escapePDF = _py_escape_pdf
        except Exception:
            pass
        try:
            canvas.escapePDF = _py_escape_pdf
        except Exception:
            pass
except Exception:
    pass

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

APP_TITLE = "IMMUNE Audit APK"
APP_VERSION = "0.1.0"
RASTER_ZOOM = 2.0
INNER_DARK_THRESHOLD = 0.18


def safe_name(text):
    text = str(text or "").strip()
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_", ".", " "):
            out.append(ch)
        else:
            out.append("_")
    cleaned = "".join(out).strip().strip(".")
    return cleaned or "file"


class LocalFileStore:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def build_local_path(self, display_name=None, default_name="selected_input", required_suffix=None):
        raw = os.path.basename(str(display_name or default_name or "selected_input").strip())
        root, ext = os.path.splitext(raw)
        root = safe_name(root or default_name)
        ext = required_suffix or ext or ""
        if required_suffix and not str(ext).lower().startswith("."):
            ext = "." + str(ext)
        if not ext:
            ext = ""
        return os.path.join(self.base_dir, root + ext)

    def save_bytes(self, payload, display_name=None, default_name="selected_input", required_suffix=None):
        path = self.build_local_path(display_name=display_name, default_name=default_name, required_suffix=required_suffix)
        with open(path, "wb") as fh:
            fh.write(bytes(payload or b""))
        return path


ANDROID_JAVA_AVAILABLE = False
AndroidPythonActivity = None
AndroidIntent = None
AndroidUri = None
AndroidByteArrayOutputStream = None
AndroidUriCopyHelper = None

if platform == "android":
    try:
        from android import activity
        from jnius import autoclass

        AndroidPythonActivity = autoclass("org.kivy.android.PythonActivity")
        AndroidIntent = autoclass("android.content.Intent")
        AndroidUri = autoclass("android.net.Uri")
        AndroidByteArrayOutputStream = autoclass("java.io.ByteArrayOutputStream")
        try:
            AndroidUriCopyHelper = autoclass("org.formalchemist.formalchemist.UriCopyHelper")
        except Exception:
            AndroidUriCopyHelper = None
        ANDROID_JAVA_AVAILABLE = True
    except Exception:
        activity = None
        autoclass = None


class AndroidDocumentPickerService:
    def __init__(self, import_store, status_callback=None):
        self.import_store = import_store
        self.status_callback = status_callback

    def _status(self, message):
        if callable(self.status_callback):
            self.status_callback(message)

    def _coerce_binary_payload(self, payload):
        if payload is None:
            return b""
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, bytearray):
            return bytes(payload)
        if isinstance(payload, memoryview):
            return payload.tobytes()
        try:
            return bytes(payload)
        except Exception:
            return b""

    def _resolve_display_name_native(self, context_obj, uri_string, default_name):
        if platform != "android" or AndroidUriCopyHelper is None:
            return None
        try:
            name = AndroidUriCopyHelper.resolveDisplayName(context_obj, uri_string, default_name or "")
            if name:
                return str(name)
        except Exception:
            pass
        return None

    def _copy_uri_to_local_file_native(self, context_obj, uri_string, out_path):
        if platform != "android" or AndroidUriCopyHelper is None:
            return False
        try:
            return bool(AndroidUriCopyHelper.copyUriToPath(context_obj, uri_string, out_path))
        except Exception:
            return False

    def write_bytes_to_uri(self, uri, payload, mode="w"):
        if platform != "android" or not ANDROID_JAVA_AVAILABLE:
            raise RuntimeError("Android document saver is unavailable.")
        if uri is None:
            raise ValueError("Android returned no destination URI.")

        data = self._coerce_binary_payload(payload)
        uri_string = str(uri.toString()).strip() if not isinstance(uri, str) else uri.strip()
        if not uri_string:
            raise ValueError("Android returned an empty destination URI.")

        uri_obj = AndroidUri.parse(uri_string)
        activity_obj = AndroidPythonActivity.mActivity
        resolver = activity_obj.getContentResolver()
        pfd = None
        fd = None
        try:
            pfd = resolver.openFileDescriptor(uri_obj, mode or "w")
            if pfd is None:
                raise IOError("Android could not open the save destination.")
            fd = pfd.detachFd()
            try:
                os.ftruncate(fd, 0)
            except Exception:
                pass

            total = 0
            chunk_size = 65536
            while total < len(data):
                chunk = data[total: total + chunk_size]
                written = os.write(fd, chunk)
                if written is None or written <= 0:
                    raise IOError("Android write returned 0 bytes.")
                total += written
            try:
                os.fsync(fd)
            except Exception:
                pass
            return total
        finally:
            try:
                if fd is not None:
                    os.close(fd)
            except Exception:
                pass
            try:
                if pfd is not None:
                    pfd.close()
            except Exception:
                pass

    def copy_uri_to_local_file(self, uri, default_name="selected_input", required_suffix=None):
        if platform != "android" or not ANDROID_JAVA_AVAILABLE:
            raise RuntimeError("Android document picker is unavailable.")
        if uri is None:
            raise ValueError("Android returned no document URI.")

        activity_obj = AndroidPythonActivity.mActivity
        resolver = activity_obj.getContentResolver()
        uri_string = str(uri.toString()).strip() if not isinstance(uri, str) else uri.strip()
        if not uri_string:
            raise ValueError("Android returned an empty document URI.")

        display_name = self._resolve_display_name_native(activity_obj, uri_string, default_name)
        target_path = self.import_store.build_local_path(
            display_name=display_name,
            default_name=default_name,
            required_suffix=required_suffix,
        )

        native_ok = self._copy_uri_to_local_file_native(activity_obj, uri_string, target_path)
        if not native_ok:
            raise ValueError("Android could not copy the selected file.")

        if required_suffix and required_suffix.lower() == ".pdf":
            with open(target_path, "rb") as fh:
                head = fh.read(1024)
            pos = head.find(b"%PDF-")
            if pos == -1:
                raise ValueError("Selected file does not look like a valid PDF.")

        return target_path

    def open_document_picker(self, request_code, mime_type="*/*", title="document", on_picked=None, cancel_message=None, required_suffix=None):
        if platform != "android" or not ANDROID_JAVA_AVAILABLE:
            self._status("Android system document picker is unavailable.")
            return

        def _on_activity_result(request_code_result, result_code, intent):
            if request_code_result != request_code:
                return
            activity.unbind(on_activity_result=_on_activity_result)

            if result_code != -1 or intent is None:
                Clock.schedule_once(lambda dt, m=(cancel_message or f"{title.capitalize()} selection cancelled."): self._status(m), 0)
                return

            try:
                uri = intent.getData()
                if uri is None:
                    raise ValueError(f"No {title} URI was returned by Android.")
                local_path = self.copy_uri_to_local_file(uri, default_name=safe_name(title), required_suffix=required_suffix)
                if callable(on_picked):
                    Clock.schedule_once(lambda dt, p=local_path: on_picked(p), 0)
            except Exception as e:
                traceback.print_exc()
                Clock.schedule_once(lambda dt, m=f"{title.capitalize()} error: {e}": self._status(m), 0)

        activity.bind(on_activity_result=_on_activity_result)
        intent = AndroidIntent(AndroidIntent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(AndroidIntent.CATEGORY_OPENABLE)
        intent.addFlags(AndroidIntent.FLAG_GRANT_READ_URI_PERMISSION)
        intent.addFlags(AndroidIntent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
        intent.setType(mime_type or "*/*")
        AndroidPythonActivity.mActivity.startActivityForResult(intent, request_code)

    def open_create_document(self, request_code, suggested_name, mime_type="application/pdf", on_picked=None, cancel_message="Save cancelled."):
        if platform != "android" or not ANDROID_JAVA_AVAILABLE:
            raise RuntimeError("Android save picker is unavailable.")

        def _on_activity_result(request_code_result, result_code, intent):
            if request_code_result != request_code:
                return
            activity.unbind(on_activity_result=_on_activity_result)
            if result_code != -1 or intent is None:
                Clock.schedule_once(lambda dt, m=cancel_message: self._status(m), 0)
                return
            try:
                uri = intent.getData()
            except Exception:
                uri = None
            if uri is None:
                Clock.schedule_once(lambda dt, m=cancel_message: self._status(m), 0)
                return
            if callable(on_picked):
                Clock.schedule_once(lambda dt, picked_uri=uri: on_picked(picked_uri), 0)

        activity.bind(on_activity_result=_on_activity_result)
        intent = AndroidIntent(AndroidIntent.ACTION_CREATE_DOCUMENT)
        intent.addCategory(AndroidIntent.CATEGORY_OPENABLE)
        intent.addFlags(AndroidIntent.FLAG_GRANT_READ_URI_PERMISSION)
        intent.addFlags(AndroidIntent.FLAG_GRANT_WRITE_URI_PERMISSION)
        intent.setType(mime_type or "*/*")
        try:
            intent.putExtra(AndroidIntent.EXTRA_TITLE, str(suggested_name or "output"))
        except Exception:
            pass
        AndroidPythonActivity.mActivity.startActivityForResult(intent, request_code)


class PdfRendererBridge:
    def render_page(self, pdf_path, page_index=0, zoom=2.0):
        if platform == "android":
            if not ANDROID_JAVA_AVAILABLE:
                raise RuntimeError("Android PDF renderer is unavailable.")
            try:
                AndroidPdfRenderHelper = autoclass("org.formalchemist.formalchemist.PdfRenderHelper")
                fd, tmp_path = tempfile.mkstemp(prefix="immune_audit_preview_", suffix=".png")
                os.close(fd)
                try:
                    out_path = AndroidPdfRenderHelper.renderPageToPng(pdf_path, int(page_index), float(zoom), tmp_path)
                    out_path = str(out_path or tmp_path)
                    img = cv2.imread(out_path, cv2.IMREAD_COLOR)
                    if img is None:
                        raise ValueError("Android PDF renderer returned an unreadable preview image.")
                    return img
                finally:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
            except Exception as e:
                raise RuntimeError(f"Android PDF render failed: {e}")

        if fitz is None:
            raise RuntimeError("Desktop rendering requires PyMuPDF (fitz), but it is not installed.")

        doc = fitz.open(pdf_path)
        try:
            page = doc[page_index]
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        finally:
            doc.close()


class AuditEngine:
    def __init__(self, renderer=None):
        self.renderer = renderer or PdfRendererBridge()

    def run_audit(self, input_path, progress_callback=None):
        if not input_path or not os.path.exists(input_path):
            raise FileNotFoundError("Input PDF not found.")

        reader = PdfReader(input_path)
        writer = PdfWriter()
        summary_entries = []

        total_pages = len(reader.pages)
        for page_index, page in enumerate(reader.pages):
            self._progress(progress_callback, f"Analyzing page {page_index + 1} / {total_pages}...")

            img = self.renderer.render_page(input_path, page_index=page_index, zoom=RASTER_ZOOM)
            page_result = self._detect_page_issues(img)

            page_width = float(page.mediabox.width)
            page_height = float(page.mediabox.height)
            overlay_bytes = self._build_overlay_pdf(page_width, page_height, page_result)
            if overlay_bytes:
                overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
                page.merge_page(overlay_reader.pages[0])

            writer.add_page(page)
            summary_entries.extend(self._page_entries(page_index, page_result))

        self._progress(progress_callback, "Building consolidated summary page...")
        summary_pdf = self._build_summary_pdf(summary_entries)
        summary_reader = PdfReader(io.BytesIO(summary_pdf))
        for page in summary_reader.pages:
            writer.add_page(page)

        out_buffer = io.BytesIO()
        writer.write(out_buffer)
        return {
            "pdf_bytes": out_buffer.getvalue(),
            "issue_count": len(summary_entries),
            "summary_entries": summary_entries,
            "page_count": total_pages,
        }

    def _progress(self, callback, message):
        if callable(callback):
            callback(str(message))

    def _page_entries(self, page_index, result):
        entries = []
        for _ in result["blank_rects"]:
            entries.append(f"Page {page_index + 1}: Blank underscore / answer line detected")
        for _ in result["radio_group_rects"]:
            entries.append(f"Page {page_index + 1}: Unanswered radio group detected")
        for _ in result["checkbox_group_rects"]:
            entries.append(f"Page {page_index + 1}: Unanswered checkbox group detected")
        return entries

    def _detect_page_issues(self, img_bgr):
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, bw_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        blank_rects = self._detect_blank_lines(bw_inv)
        circles, squares = self._detect_small_controls(bw_inv)
        radio_group_rects = self._find_unanswered_groups(gray, circles)
        checkbox_group_rects = self._find_unanswered_groups(gray, squares)

        return {
            "blank_rects": blank_rects,
            "radio_group_rects": radio_group_rects,
            "checkbox_group_rects": checkbox_group_rects,
        }

    def _detect_blank_lines(self, bw_inv):
        img_h, img_w = bw_inv.shape[:2]
        kernel_w = max(24, int(img_w * 0.04))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 1))
        opened = cv2.morphologyEx(bw_inv, cv2.MORPH_OPEN, kernel, iterations=1)
        contours, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        rects = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w < max(36, int(img_w * 0.035)):
                continue
            if h > max(8, int(img_h * 0.012)):
                continue
            if x < 8 or (x + w) > (img_w - 8):
                continue
            if y < int(img_h * 0.03) or y > int(img_h * 0.97):
                continue
            rects.append((x, y, x + w, y + h))
        return self._dedupe_rects(rects, merge_gap=8)

    def _detect_small_controls(self, bw_inv):
        img_h, img_w = bw_inv.shape[:2]
        contours, _ = cv2.findContours(bw_inv, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        circles = []
        squares = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 20:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            if w < 8 or h < 8:
                continue
            if w > max(70, int(img_w * 0.08)) or h > max(70, int(img_h * 0.08)):
                continue
            aspect = w / float(max(h, 1))
            if not (0.75 <= aspect <= 1.25):
                continue
            peri = cv2.arcLength(cnt, True)
            if peri <= 0:
                continue
            circularity = 4.0 * math.pi * area / max(peri * peri, 1e-6)
            approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)
            fill = area / float(max(w * h, 1))
            rect = (x, y, x + w, y + h)

            if len(approx) in (4, 5, 6) and 0.10 <= fill <= 0.90:
                squares.append(rect)
            elif circularity >= 0.45 and 0.10 <= fill <= 0.90:
                circles.append(rect)

        circles = self._dedupe_rects(circles, merge_gap=5)
        squares = self._dedupe_rects(squares, merge_gap=5)
        return circles, squares

    def _find_unanswered_groups(self, gray, rects):
        groups = self._group_rects_by_row(rects, row_tol=26)
        unanswered = []
        for group in groups:
            if len(group) <= 1:
                continue
            selected_found = False
            for rect in group:
                if self._is_control_selected(gray, rect):
                    selected_found = True
                    break
            if not selected_found:
                unanswered.append(self._union_rects(group))
        return unanswered

    def _is_control_selected(self, gray, rect):
        x0, y0, x1, y1 = [int(v) for v in rect]
        w = max(1, x1 - x0)
        h = max(1, y1 - y0)
        pad_x = max(1, int(w * 0.22))
        pad_y = max(1, int(h * 0.22))
        inner = gray[y0 + pad_y: y1 - pad_y, x0 + pad_x: x1 - pad_x]
        if inner.size == 0:
            inner = gray[y0:y1, x0:x1]
        if inner.size == 0:
            return False
        dark_ratio = float(np.sum(inner < 120)) / float(inner.size)
        return dark_ratio > INNER_DARK_THRESHOLD

    def _group_rects_by_row(self, rects, row_tol=26):
        ordered = sorted(rects, key=lambda r: ((r[1] + r[3]) / 2.0, r[0]))
        groups = []
        current = []
        current_y = None
        for rect in ordered:
            cy = (rect[1] + rect[3]) / 2.0
            if not current:
                current = [rect]
                current_y = cy
                continue
            if abs(cy - current_y) <= row_tol:
                current.append(rect)
                current_y = (current_y + cy) / 2.0
            else:
                groups.append(sorted(current, key=lambda r: r[0]))
                current = [rect]
                current_y = cy
        if current:
            groups.append(sorted(current, key=lambda r: r[0]))
        return groups

    def _union_rects(self, rects):
        xs0 = [r[0] for r in rects]
        ys0 = [r[1] for r in rects]
        xs1 = [r[2] for r in rects]
        ys1 = [r[3] for r in rects]
        return (min(xs0), min(ys0), max(xs1), max(ys1))

    def _rect_gap(self, a, b):
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        dx = max(bx0 - ax1, ax0 - bx1, 0)
        dy = max(by0 - ay1, ay0 - by1, 0)
        return max(dx, dy)

    def _dedupe_rects(self, rects, merge_gap=5):
        out = []
        for rect in sorted(rects, key=lambda r: (r[1], r[0], r[2], r[3])):
            merged = False
            for idx, existing in enumerate(out):
                if self._rect_gap(existing, rect) <= merge_gap:
                    out[idx] = self._union_rects([existing, rect])
                    merged = True
                    break
            if not merged:
                out.append(rect)
        return out

    def _pdf_rect(self, img_rect, page_height):
        x0, y0, x1, y1 = img_rect
        return (
            float(x0) / RASTER_ZOOM,
            float(page_height) - (float(y1) / RASTER_ZOOM),
            float(x1 - x0) / RASTER_ZOOM,
            float(y1 - y0) / RASTER_ZOOM,
        )

    def _draw_issue_rects(self, canv, page_height, rects, rgb, label):
        if not rects:
            return
        canv.setStrokeColorRGB(*rgb)
        canv.setLineWidth(1.6)
        canv.setFont("Helvetica", 7)
        for rect in rects:
            x, y, w, h = self._pdf_rect(rect, page_height)
            canv.rect(x, y, w, h, stroke=1, fill=0)
            text_y = min(page_height - 10, y + h + 3)
            canv.drawString(x, max(6, text_y), label)

    def _build_overlay_pdf(self, page_width, page_height, page_result):
        if not (
            page_result["blank_rects"]
            or page_result["radio_group_rects"]
            or page_result["checkbox_group_rects"]
        ):
            return b""

        buf = io.BytesIO()
        canv = canvas.Canvas(buf, pagesize=(page_width, page_height))
        self._draw_issue_rects(canv, page_height, page_result["blank_rects"], (1.0, 0.75, 0.0), "Blank")
        self._draw_issue_rects(canv, page_height, page_result["radio_group_rects"], (1.0, 0.0, 0.0), "Radio")
        self._draw_issue_rects(canv, page_height, page_result["checkbox_group_rects"], (0.85, 0.0, 0.65), "Checkbox")
        canv.save()
        return buf.getvalue()

    def _build_summary_pdf(self, summary_entries):
        summary_buffer = io.BytesIO()
        doc_summary = SimpleDocTemplate(summary_buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()

        elements.append(Paragraph("<b>FULL AUDIT SUMMARY (ANDROID-SAFE MODE A)</b>", styles["Title"]))
        elements.append(Spacer(1, 0.25 * inch))
        elements.append(Paragraph(f"Total Issues Detected: {len(summary_entries)}", styles["Normal"]))
        elements.append(Spacer(1, 0.15 * inch))

        if not summary_entries:
            elements.append(Paragraph("No issues detected.", styles["Normal"]))
        else:
            for entry in summary_entries:
                elements.append(Paragraph(entry, styles["Normal"]))
                elements.append(Spacer(1, 0.08 * inch))

        doc_summary.build(elements)
        summary_buffer.seek(0)
        return summary_buffer.read()


class DesktopSelectPopup(Popup):
    def __init__(self, on_select, **kwargs):
        super().__init__(**kwargs)
        self.title = "Select PDF"
        self.size_hint = (0.92, 0.82)
        self.auto_dismiss = False
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
        self.path_input = TextInput(
            hint_text="Paste full PDF path here",
            multiline=False,
            size_hint_y=None,
            height=dp(46),
        )
        box.add_widget(self.path_input)
        hint = Label(
            text="On desktop, paste the full path to the PDF file.",
            size_hint_y=None,
            height=dp(26),
        )
        box.add_widget(hint)
        btns = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        cancel_btn = Button(text="Cancel")
        ok_btn = Button(text="Use This Path")
        cancel_btn.bind(on_release=lambda *_: self.dismiss())
        def _submit(*_):
            on_select(self.path_input.text.strip())
            self.dismiss()
        ok_btn.bind(on_release=_submit)
        btns.add_widget(cancel_btn)
        btns.add_widget(ok_btn)
        box.add_widget(btns)
        self.content = box


class AuditAppUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", spacing=dp(10), padding=dp(12), **kwargs)
        app = App.get_running_app()
        self.app = app

        self.header = Label(
            text=f"{APP_TITLE}\nLean APK build for PDF auditing",
            size_hint_y=None,
            height=dp(64),
            halign="center",
            valign="middle",
        )
        self.header.bind(size=lambda inst, value: setattr(inst, "text_size", value))
        self.add_widget(self.header)

        self.pick_btn = Button(text="1) Select PDF", size_hint_y=None, height=dp(48))
        self.pick_btn.bind(on_release=lambda *_: self.app.select_pdf())
        self.add_widget(self.pick_btn)

        self.file_label = Label(
            text="No PDF selected.",
            size_hint_y=None,
            height=dp(52),
            halign="left",
            valign="middle",
        )
        self.file_label.bind(size=lambda inst, value: setattr(inst, "text_size", value))
        self.add_widget(self.file_label)

        self.run_btn = Button(text="2) Run Full Audit", size_hint_y=None, height=dp(52), disabled=True)
        self.run_btn.bind(on_release=lambda *_: self.app.run_audit())
        self.add_widget(self.run_btn)

        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(12))
        self.add_widget(self.progress)

        self.status_box = TextInput(readonly=True, text="Ready.\n", multiline=True)
        scroller = ScrollView()
        scroller.add_widget(self.status_box)
        self.add_widget(scroller)

        self.footer = Label(
            text="Input: PDF only • Output: audited PDF with summary page",
            size_hint_y=None,
            height=dp(28),
            halign="center",
            valign="middle",
        )
        self.footer.bind(size=lambda inst, value: setattr(inst, "text_size", value))
        self.add_widget(self.footer)

    def set_selected_file(self, path):
        base = os.path.basename(path) if path else "No PDF selected."
        self.file_label.text = f"Selected PDF:\n{base}" if path else "No PDF selected."
        self.run_btn.disabled = not bool(path)

    def append_status(self, message):
        if not str(message or "").endswith("\n"):
            message = str(message) + "\n"
        self.status_box.text += str(message)
        self.status_box.cursor = (0, len(self.status_box.text))


class ImmuneAuditApp(App):
    def build(self):
        self.title = APP_TITLE
        self.selected_pdf_path = None
        self.pending_output_bytes = None
        self.pending_output_name = None

        base_dir = self.user_data_dir or os.path.join(os.getcwd(), ".immune_audit")
        self.import_store = LocalFileStore(os.path.join(base_dir, "imports"))
        self.output_store = LocalFileStore(os.path.join(base_dir, "outputs"))
        self.android_picker = AndroidDocumentPickerService(self.import_store, status_callback=self.post_status)
        self.engine = AuditEngine()
        self.ui = AuditAppUI()
        self.post_status("Ready.")
        if platform != "android" and fitz is None:
            self.post_status("Desktop note: page rendering outside Android needs PyMuPDF (fitz).")
        return self.ui

    def post_status(self, message):
        Clock.schedule_once(lambda dt, msg=str(message): self.ui.append_status(msg), 0)

    def set_progress(self, percent):
        percent = max(0.0, min(100.0, float(percent)))
        Clock.schedule_once(lambda dt, v=percent: setattr(self.ui.progress, "value", v), 0)

    def select_pdf(self):
        if platform == "android":
            self.post_status("Opening Android PDF picker...")
            self.android_picker.open_document_picker(
                request_code=43101,
                mime_type="application/pdf",
                title="PDF",
                on_picked=self._on_pdf_picked,
                cancel_message="PDF selection cancelled.",
                required_suffix=".pdf",
            )
            return

        DesktopSelectPopup(on_select=self._on_pdf_picked).open()

    def _on_pdf_picked(self, path):
        if not path:
            self.post_status("No PDF selected.")
            return
        if not os.path.exists(path):
            self.post_status(f"File not found:\n{path}")
            return
        self.selected_pdf_path = path
        self.ui.set_selected_file(path)
        self.post_status(f"PDF loaded:\n{path}")

    def run_audit(self):
        if not self.selected_pdf_path:
            self.post_status("Load a PDF first.")
            return
        self.ui.run_btn.disabled = True
        self.set_progress(0)
        self.post_status("Starting full audit...")
        worker = threading.Thread(target=self._run_audit_worker, daemon=True)
        worker.start()

    def _run_audit_worker(self):
        try:
            total_pages = len(PdfReader(self.selected_pdf_path).pages)
            progress_state = {"count": 0}

            def _progress(msg):
                progress_state["count"] += 1
                step = min(progress_state["count"], total_pages + 1)
                pct = (step / float(max(total_pages + 1, 1))) * 100.0
                self.post_status(msg)
                self.set_progress(pct)

            result = self.engine.run_audit(self.selected_pdf_path, progress_callback=_progress)
            suggested_name = os.path.splitext(os.path.basename(self.selected_pdf_path))[0] + "_AUDITED.pdf"
            self.pending_output_bytes = result["pdf_bytes"]
            self.pending_output_name = safe_name(suggested_name)
            self.post_status(f"Audit finished. Issues found: {result['issue_count']}")
            self.set_progress(100)
            Clock.schedule_once(lambda dt: self._finish_audit_save(), 0)
        except Exception as e:
            traceback.print_exc()
            self.post_status(f"Audit error:\n{e}")
            Clock.schedule_once(lambda dt: self._reset_after_job(), 0)

    def _finish_audit_save(self):
        if not self.pending_output_bytes:
            self.post_status("Nothing to save.")
            self._reset_after_job()
            return

        if platform == "android":
            self.post_status(f"Choose save location for:\n{self.pending_output_name}")
            try:
                self.android_picker.open_create_document(
                    request_code=43111,
                    suggested_name=self.pending_output_name,
                    mime_type="application/pdf",
                    on_picked=self._write_android_output,
                    cancel_message="Save cancelled.",
                )
                return
            except Exception as e:
                self.post_status(f"Android save picker error:\n{e}")

        out_path = self.output_store.save_bytes(
            self.pending_output_bytes,
            display_name=self.pending_output_name,
            default_name="audited_output",
            required_suffix=".pdf",
        )
        self.post_status(f"Saved audited PDF to:\n{out_path}")
        self._reset_after_job()

    def _write_android_output(self, uri):
        try:
            written = self.android_picker.write_bytes_to_uri(uri, self.pending_output_bytes)
            self.post_status(f"Saved audited PDF:\n{self.pending_output_name}\nBytes written: {written}")
        except Exception as e:
            traceback.print_exc()
            self.post_status(f"Save error:\n{e}")
        finally:
            self._reset_after_job()

    def _reset_after_job(self):
        self.pending_output_bytes = None
        self.pending_output_name = None
        self.ui.run_btn.disabled = not bool(self.selected_pdf_path)


if __name__ == "__main__":
    ImmuneAuditApp().run()
