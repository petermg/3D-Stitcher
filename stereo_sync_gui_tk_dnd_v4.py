import os
import sys
import shlex
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except Exception:
    TkinterDnD = None
    DND_FILES = None
    DND_AVAILABLE = False


SCRIPT_NAME_DEFAULT = "stereo_sync_stack_v1.py"
VIDEO_FILETYPES = [
    ("Video files", "*.mp4 *.mkv *.mov *.avi *.m4v *.ts *.mts *.webm"),
    ("All files", "*.*"),
]
OUTPUT_FILETYPES = [
    ("Matroska", "*.mkv"),
    ("MP4", "*.mp4"),
    ("MOV", "*.mov"),
    ("All files", "*.*"),
]
PY_FILETYPES = [("Python files", "*.py"), ("All files", "*.*")]


class FileRow(ttk.Frame):
    def __init__(
        self,
        master,
        label,
        variable,
        browse_text="Browse",
        save=False,
        filetypes=None,
        drop_mode=None,
        on_drop_file=None,
    ):
        super().__init__(master)
        self.variable = variable
        self.save = save
        self.filetypes = filetypes or [("All files", "*.*")]
        self.drop_mode = drop_mode
        self.on_drop_file = on_drop_file

        self.columnconfigure(1, weight=1)
        ttk.Label(self, text=label, width=28).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)

        self.entry = ttk.Entry(self, textvariable=variable)
        self.entry.grid(row=0, column=1, sticky="ew", pady=2)

        ttk.Button(self, text=browse_text, command=self._browse, width=10).grid(row=0, column=2, padx=(8, 0), pady=2)

        if drop_mode and DND_AVAILABLE:
            self._enable_dnd()

    def _browse(self):
        current = self.variable.get().strip()
        initialdir = None
        initialfile = None
        if current:
            initialdir = os.path.dirname(current) or None
            initialfile = os.path.basename(current) or None
        if self.save:
            path = filedialog.asksaveasfilename(initialdir=initialdir, initialfile=initialfile or "output.mkv", filetypes=self.filetypes)
        else:
            path = filedialog.askopenfilename(initialdir=initialdir, filetypes=self.filetypes)
        if path:
            self.variable.set(path)

    def _enable_dnd(self):
        try:
            self.entry.drop_target_register(DND_FILES)
            self.entry.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _parse_drop_paths(self, event_data):
        try:
            paths = list(self.tk.splitlist(event_data))
        except Exception:
            paths = [event_data]

        cleaned = []
        for path in paths:
            if not path:
                continue
            path = path.strip()
            if path.startswith("{") and path.endswith("}"):
                path = path[1:-1]
            path = path.strip('"')
            if path:
                cleaned.append(path)
        return cleaned

    def _looks_like_video(self, path):
        ext = os.path.splitext(path)[1].lower()
        return ext in {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".ts", ".mts", ".webm"}

    def _on_drop(self, event):
        paths = self._parse_drop_paths(event.data)
        if not paths:
            return

        if self.drop_mode == "single":
            chosen = paths[0]
            if self.on_drop_file:
                self.on_drop_file(chosen)
            else:
                self.variable.set(chosen)
            return

        if self.drop_mode == "video":
            videos = [p for p in paths if self._looks_like_video(p)]
            chosen = videos[0] if videos else paths[0]
            if self.on_drop_file:
                self.on_drop_file(chosen)
            else:
                self.variable.set(chosen)


class LabeledField(ttk.Frame):
    def __init__(self, master, label, variable, width=16):
        super().__init__(master)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text=label, width=28).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(self, textvariable=variable, width=width).grid(row=0, column=1, sticky="w", pady=2)


class LabeledCombo(ttk.Frame):
    def __init__(self, master, label, variable, values, width=18, state="readonly"):
        super().__init__(master)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text=label, width=28).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        self.combo = ttk.Combobox(self, textvariable=variable, values=values, width=width, state=state)
        self.combo.grid(row=0, column=1, sticky="w", pady=2)


class StereoSyncGUI(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.master = master
        self.process = None
        self.output_queue = queue.Queue()
        self.worker = None
        self._output_auto_mode = True
        self._internal_output_change = False

        self.vars = {}
        self._init_vars()
        self._build_ui()
        self._bind_updates()
        self._update_dynamic_state()
        self._enable_window_dnd_if_available()
        self.update_command_preview()
        self.after(100, self._poll_output_queue)

    def _init_vars(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.vars["script_path"] = tk.StringVar(value=os.path.join(script_dir, SCRIPT_NAME_DEFAULT))
        self.vars["left_video"] = tk.StringVar()
        self.vars["right_video"] = tk.StringVar()
        self.vars["mode"] = tk.StringVar(value="render")
        self.vars["output"] = tk.StringVar(value=os.path.join(script_dir, "output.mkv"))

        # numeric/text args from v10
        defaults = {
            "start_analyze_seconds": "300.0",
            "drift_probe_window": "30.0",
            "end_margin": "15.0",
            "sample_rate": "2000",
            "max_lag": "2.0",
            "height": "1080",
            "crf": "35",
            "preset": "slow",
            "encoder": "nvidia",
            "fps": "",
            "force_left_trim": "",
            "force_right_trim": "",
            "force_setpts_factor": "",
            "stereo_output": "sbs",
            "anaglyph_mode": "arcd",
            "manual_right_shift_x": "0.0",
            "manual_right_shift_y": "0.0",
            "manual_right_rotate_deg": "0.0",
            "align_section_start": "2.0",
            "align_section_duration": "6.0",
            "align_samples": "5",
            "align_analysis_width": "640",
            "align_crop_fraction": "0.70",
        }
        for key, value in defaults.items():
            self.vars[key] = tk.StringVar(value=value)

        # booleans from v10
        self.vars["use_right_audio"] = tk.BooleanVar(value=False)
        self.vars["disable_drift_correction"] = tk.BooleanVar(value=False)
        self.vars["auto_align_vertical"] = tk.BooleanVar(value=False)
        self.vars["auto_align_horizontal"] = tk.BooleanVar(value=False)

    def _build_ui(self):
        self.master.title("Stereo Sync GUI (Complete v11 flags + optional drag-and-drop + auto output folder)")
        self.master.geometry("1320x980")
        self.master.minsize(1120, 820)
        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)

        files = ttk.LabelFrame(top, text="Files / Core Arguments")
        files.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        files.columnconfigure(0, weight=1)
        FileRow(files, "Processing script", self.vars["script_path"], filetypes=PY_FILETYPES, drop_mode="single").grid(row=0, column=0, sticky="ew", padx=8, pady=4)
        FileRow(files, "left_video", self.vars["left_video"], filetypes=VIDEO_FILETYPES, drop_mode="video", on_drop_file=self._set_left_video).grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        FileRow(files, "right_video", self.vars["right_video"], filetypes=VIDEO_FILETYPES, drop_mode="video", on_drop_file=self._set_right_video).grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        FileRow(files, "--output", self.vars["output"], browse_text="Save As", save=True, filetypes=OUTPUT_FILETYPES, drop_mode="single").grid(row=3, column=0, sticky="ew", padx=8, pady=4)

        core = ttk.LabelFrame(top, text="Run / Explicit Flag Mapping")
        core.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))
        core.columnconfigure(0, weight=1)
        LabeledCombo(core, "--mode", self.vars["mode"], ["analyze", "render"]).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        ttk.Label(
            core,
            text=(
                "This GUI includes a visible control for every current v11 argparse argument.\n"
                "It launches the processing script with the same Python interpreter that launched this GUI,\n"
                "so if you start the GUI from your venv, it uses that venv automatically.\n"
                "Optional drag-and-drop for input files is enabled automatically when tkinterdnd2 is installed."
            ),
            justify="left",
            wraplength=480,
        ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

        buttons = ttk.Frame(core)
        buttons.grid(row=2, column=0, sticky="w", padx=8, pady=(0, 8))
        self.run_selected_btn = ttk.Button(buttons, text="Run Selected Mode", command=self.run_selected_mode)
        self.run_selected_btn.grid(row=0, column=0, padx=(0, 8))
        self.analyze_btn = ttk.Button(buttons, text="Analyze Now", command=lambda: self._launch("analyze"))
        self.analyze_btn.grid(row=0, column=1, padx=(0, 8))
        self.render_btn = ttk.Button(buttons, text="Render Now", command=lambda: self._launch("render"))
        self.render_btn.grid(row=0, column=2, padx=(0, 8))
        self.stop_btn = ttk.Button(buttons, text="Stop", command=self.stop_process, state="disabled")
        self.stop_btn.grid(row=0, column=3, padx=(0, 8))
        ttk.Button(buttons, text="Reset Defaults", command=self.reset_defaults).grid(row=0, column=4)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(core, textvariable=self.status_var).grid(row=3, column=0, sticky="w", padx=8, pady=(0, 4))
        self.dnd_status_var = tk.StringVar(
            value="Drag-and-drop: enabled (tkinterdnd2 detected)" if DND_AVAILABLE else "Drag-and-drop: not available (install tkinterdnd2 to enable)"
        )
        ttk.Label(core, textvariable=self.dnd_status_var).grid(row=4, column=0, sticky="w", padx=8, pady=(0, 8))

        preview_box = ttk.LabelFrame(self, text="Command Preview")
        preview_box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        preview_box.columnconfigure(0, weight=1)
        self.command_preview = ScrolledText(preview_box, height=5, wrap="word")
        self.command_preview.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        self.command_preview.configure(state="disabled")

        notebook = ttk.Notebook(self)
        notebook.grid(row=2, column=0, sticky="nsew")

        self._build_sync_tab(notebook)
        self._build_output_tab(notebook)
        self._build_overrides_tab(notebook)
        self._build_alignment_tab(notebook)
        self._build_console_tab(notebook)

    def _set_left_video(self, path):
        self.vars["left_video"].set(path)
        self._maybe_auto_output_from_inputs()

    def _set_right_video(self, path):
        self.vars["right_video"].set(path)
        self._maybe_auto_output_from_inputs()

    def _on_output_changed(self):
        if not self._internal_output_change:
            self._output_auto_mode = False

    def _set_output_value(self, value, auto=False):
        self._internal_output_change = True
        try:
            self.vars["output"].set(value)
        finally:
            self._internal_output_change = False
        self._output_auto_mode = bool(auto)

    def _choose_input_folder(self):
        left = self.vars["left_video"].get().strip()
        right = self.vars["right_video"].get().strip()
        if left:
            return os.path.dirname(left) or None
        if right:
            return os.path.dirname(right) or None
        return None

    def _maybe_auto_output_from_inputs(self, force=False):
        input_folder = self._choose_input_folder()
        if not input_folder:
            return

        current_output = self.vars["output"].get().strip()
        if not force and current_output and not self._output_auto_mode:
            return

        basename = os.path.basename(current_output) if current_output else "output.mkv"
        if not basename:
            basename = "output.mkv"
        suggested = os.path.join(input_folder, basename)
        self._set_output_value(suggested, auto=True)

    def _enable_window_dnd_if_available(self):
        if not DND_AVAILABLE:
            return
        try:
            self.master.drop_target_register(DND_FILES)
            self.master.dnd_bind("<<Drop>>", self._on_window_drop)
        except Exception:
            pass

    def _parse_drop_paths(self, event_data):
        try:
            paths = list(self.tk.splitlist(event_data))
        except Exception:
            paths = [event_data]

        cleaned = []
        for path in paths:
            if not path:
                continue
            path = path.strip()
            if path.startswith("{") and path.endswith("}"):
                path = path[1:-1]
            path = path.strip('"')
            if path:
                cleaned.append(path)
        return cleaned

    def _on_window_drop(self, event):
        paths = self._parse_drop_paths(event.data)
        if not paths:
            return

        videos = [p for p in paths if os.path.splitext(p)[1].lower() in {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".ts", ".mts", ".webm"}]
        if not videos:
            videos = paths

        if len(videos) >= 2:
            self._set_left_video(videos[0])
            self._set_right_video(videos[1])
            self.status_var.set("Loaded left/right videos from drag-and-drop.")
        elif len(videos) == 1:
            if not self.vars["left_video"].get().strip():
                self._set_left_video(videos[0])
                self.status_var.set("Loaded LEFT video from drag-and-drop.")
            else:
                self._set_right_video(videos[0])
                self.status_var.set("Loaded RIGHT video from drag-and-drop.")

    def _build_sync_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Sync / Drift")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        left = ttk.LabelFrame(tab, text="Audio Start Lock / Probe")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        left.columnconfigure(0, weight=1)
        self._add_field(left, 0, "--start-analyze-seconds", "start_analyze_seconds")
        self._add_field(left, 1, "--sample-rate", "sample_rate")
        self._add_field(left, 2, "--max-lag", "max_lag")

        right = ttk.LabelFrame(tab, text="Late Drift Analysis")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        right.columnconfigure(0, weight=1)
        self._add_field(right, 0, "--drift-probe-window", "drift_probe_window")
        self._add_field(right, 1, "--end-margin", "end_margin")

        note = ttk.LabelFrame(tab, text="Notes")
        note.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Label(
            note,
            text=(
                "These fields map directly to the sync and drift argparse options in v10.\n"
                "Leaving the defaults alone is usually fine unless you need a longer start lock or different late drift probe behavior."
            ),
            justify="left",
            wraplength=1100,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=8)

    def _build_output_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Output / Audio")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        vid = ttk.LabelFrame(tab, text="Video Flags")
        vid.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        vid.columnconfigure(0, weight=1)
        self._add_field(vid, 0, "--height", "height")
        self._add_field(vid, 1, "--crf", "crf")
        self._add_field(vid, 2, "--preset", "preset")
        self._add_combo(vid, 3, "--encoder", "encoder", ["nvidia", "amd", "cpu"])
        self._add_field(vid, 4, "--fps", "fps")
        self.stereo_combo = self._add_combo(vid, 5, "--stereo-output", "stereo_output", ["sbs", "ou", "anaglyph"])
        self.anaglyph_combo = self._add_combo(vid, 6, "--anaglyph-mode", "anaglyph_mode", ["arcd", "arcc", "arch", "arcg"])

        aud = ttk.LabelFrame(tab, text="Audio Flags")
        aud.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        aud.columnconfigure(0, weight=1)
        ttk.Checkbutton(aud, text="--use-right-audio", variable=self.vars["use_right_audio"]).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        ttk.Label(
            aud,
            text="When checked, the script mixes in right audio too. Unchecked means left audio only.",
            justify="left",
            wraplength=450,
        ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

    def _build_overrides_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Overrides / Manual Adjustments")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        trims = ttk.LabelFrame(tab, text="Trim / Drift Override Flags")
        trims.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        trims.columnconfigure(0, weight=1)
        self._add_field(trims, 0, "--force-left-trim", "force_left_trim")
        self._add_field(trims, 1, "--force-right-trim", "force_right_trim")
        self._add_field(trims, 2, "--force-setpts-factor", "force_setpts_factor")
        ttk.Checkbutton(trims, text="--disable-drift-correction", variable=self.vars["disable_drift_correction"]).grid(row=3, column=0, sticky="w", padx=8, pady=(8, 8))

        manual = ttk.LabelFrame(tab, text="Manual RIGHT-Eye Alignment Flags")
        manual.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        manual.columnconfigure(0, weight=1)
        self._add_field(manual, 0, "--manual-right-shift-x", "manual_right_shift_x")
        self._add_field(manual, 1, "--manual-right-shift-y", "manual_right_shift_y")
        self._add_field(manual, 2, "--manual-right-rotate-deg", "manual_right_rotate_deg")

    def _build_alignment_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Auto Alignment")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        left = ttk.LabelFrame(tab, text="Auto Alignment Flags")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        left.columnconfigure(0, weight=1)
        ttk.Checkbutton(left, text="--auto-align-vertical", variable=self.vars["auto_align_vertical"]).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        ttk.Checkbutton(left, text="--auto-align-horizontal", variable=self.vars["auto_align_horizontal"]).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

        right = ttk.LabelFrame(tab, text="Alignment Analysis Arguments")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        right.columnconfigure(0, weight=1)
        self._add_field(right, 0, "--align-section-start", "align_section_start")
        self._add_field(right, 1, "--align-section-duration", "align_section_duration")
        self._add_field(right, 2, "--align-samples", "align_samples")
        self._add_field(right, 3, "--align-analysis-width", "align_analysis_width")
        self._add_field(right, 4, "--align-crop-fraction", "align_crop_fraction")

        note = ttk.LabelFrame(tab, text="Notes")
        note.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Label(
            note,
            text=(
                "Vertical auto-align is usually the most reliable. Horizontal auto-align is more scene-dependent.\n"
                "These controls correspond directly to the current v11 alignment argparse options."
            ),
            justify="left",
            wraplength=1100,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=8)

    def _build_console_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Console")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        self.console = ScrolledText(tab, wrap="word")
        self.console.grid(row=0, column=0, sticky="nsew")
        self.console.configure(font=("Consolas", 10) if sys.platform.startswith("win") else ("Courier", 10))

    def _add_field(self, parent, row, label, key):
        field = LabeledField(parent, label, self.vars[key])
        field.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
        return field

    def _add_combo(self, parent, row, label, key, values):
        combo = LabeledCombo(parent, label, self.vars[key], values)
        combo.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
        return combo.combo

    def _bind_updates(self):
        for var in self.vars.values():
            try:
                var.trace_add("write", lambda *_: self.update_command_preview())
            except Exception:
                pass
        self.vars["stereo_output"].trace_add("write", lambda *_: self._update_dynamic_state())
        self.vars["mode"].trace_add("write", lambda *_: self._update_dynamic_state())
        self.vars["output"].trace_add("write", lambda *_: self._on_output_changed())

    def _update_dynamic_state(self):
        if self.vars["stereo_output"].get() == "anaglyph":
            self.anaglyph_combo.configure(state="readonly")
        else:
            self.anaglyph_combo.configure(state="disabled")
        self.update_command_preview()

    def reset_defaults(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.vars["script_path"].set(os.path.join(script_dir, SCRIPT_NAME_DEFAULT))
        self.vars["mode"].set("render")
        self._set_output_value(os.path.join(script_dir, "output.mkv"), auto=True)
        self.vars["start_analyze_seconds"].set("300.0")
        self.vars["drift_probe_window"].set("30.0")
        self.vars["end_margin"].set("15.0")
        self.vars["sample_rate"].set("2000")
        self.vars["max_lag"].set("2.0")
        self.vars["height"].set("1080")
        self.vars["crf"].set("35")
        self.vars["preset"].set("slow")
        self.vars["encoder"].set("nvidia")
        self.vars["fps"].set("")
        self.vars["use_right_audio"].set(False)
        self.vars["force_left_trim"].set("")
        self.vars["force_right_trim"].set("")
        self.vars["disable_drift_correction"].set(False)
        self.vars["force_setpts_factor"].set("")
        self.vars["stereo_output"].set("sbs")
        self.vars["anaglyph_mode"].set("arcd")
        self.vars["manual_right_shift_x"].set("0.0")
        self.vars["manual_right_shift_y"].set("0.0")
        self.vars["manual_right_rotate_deg"].set("0.0")
        self.vars["auto_align_vertical"].set(False)
        self.vars["auto_align_horizontal"].set(False)
        self.vars["align_section_start"].set("2.0")
        self.vars["align_section_duration"].set("6.0")
        self.vars["align_samples"].set("5")
        self.vars["align_analysis_width"].set("640")
        self.vars["align_crop_fraction"].set("0.70")

    def build_command(self, mode=None):
        mode = mode or self.vars["mode"].get().strip() or "render"
        script_path = self.vars["script_path"].get().strip()
        left_video = self.vars["left_video"].get().strip()
        right_video = self.vars["right_video"].get().strip()
        output = self.vars["output"].get().strip()

        if not script_path:
            raise ValueError("Processing script path is required.")
        if not left_video:
            raise ValueError("left_video is required.")
        if not right_video:
            raise ValueError("right_video is required.")
        if mode == "render" and not output:
            raise ValueError("--output is required in render mode.")
        if self.vars["force_left_trim"].get().strip() and self.vars["force_right_trim"].get().strip():
            raise ValueError("Only one of --force-left-trim or --force-right-trim may be set.")

        cmd = [sys.executable, script_path, left_video, right_video, "--mode", mode]
        if mode == "render":
            cmd += ["--output", output]

        # direct mapping for every v10 argparse option
        self._append_if_present(cmd, "--start-analyze-seconds", "start_analyze_seconds")
        self._append_if_present(cmd, "--drift-probe-window", "drift_probe_window")
        self._append_if_present(cmd, "--end-margin", "end_margin")
        self._append_if_present(cmd, "--sample-rate", "sample_rate")
        self._append_if_present(cmd, "--max-lag", "max_lag")
        self._append_if_present(cmd, "--height", "height")
        self._append_if_present(cmd, "--crf", "crf")
        self._append_if_present(cmd, "--preset", "preset")
        cmd += ["--encoder", self.vars["encoder"].get().strip() or "nvidia"]
        self._append_if_present(cmd, "--fps", "fps")

        if self.vars["use_right_audio"].get():
            cmd.append("--use-right-audio")

        self._append_if_present(cmd, "--force-left-trim", "force_left_trim")
        self._append_if_present(cmd, "--force-right-trim", "force_right_trim")
        if self.vars["disable_drift_correction"].get():
            cmd.append("--disable-drift-correction")
        self._append_if_present(cmd, "--force-setpts-factor", "force_setpts_factor")

        stereo_output = self.vars["stereo_output"].get().strip() or "sbs"
        cmd += ["--stereo-output", stereo_output]
        cmd += ["--anaglyph-mode", self.vars["anaglyph_mode"].get().strip() or "arcd"]

        self._append_if_present(cmd, "--manual-right-shift-x", "manual_right_shift_x")
        self._append_if_present(cmd, "--manual-right-shift-y", "manual_right_shift_y")
        self._append_if_present(cmd, "--manual-right-rotate-deg", "manual_right_rotate_deg")

        if self.vars["auto_align_vertical"].get():
            cmd.append("--auto-align-vertical")
        if self.vars["auto_align_horizontal"].get():
            cmd.append("--auto-align-horizontal")

        self._append_if_present(cmd, "--align-section-start", "align_section_start")
        self._append_if_present(cmd, "--align-section-duration", "align_section_duration")
        self._append_if_present(cmd, "--align-samples", "align_samples")
        self._append_if_present(cmd, "--align-analysis-width", "align_analysis_width")
        self._append_if_present(cmd, "--align-crop-fraction", "align_crop_fraction")
        return cmd

    def _append_if_present(self, cmd, flag, key):
        value = self.vars[key].get().strip()
        if value != "":
            cmd += [flag, value]

    def format_command_for_display(self, cmd):
        if os.name == "nt":
            return subprocess.list2cmdline(cmd)
        return " ".join(shlex.quote(part) for part in cmd)

    def update_command_preview(self):
        try:
            cmd = self.build_command()
            text = self.format_command_for_display(cmd)
        except Exception as exc:
            text = f"Command preview unavailable: {exc}"

        self.command_preview.configure(state="normal")
        self.command_preview.delete("1.0", "end")
        self.command_preview.insert("1.0", text)
        self.command_preview.configure(state="disabled")

    def run_selected_mode(self):
        self._launch(self.vars["mode"].get().strip() or "render")

    def _launch(self, mode):
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Already running", "A process is already running.")
            return
        try:
            cmd = self.build_command(mode)
            script_path = self.vars["script_path"].get().strip()
            if not os.path.isfile(script_path):
                raise ValueError(f"Processing script not found:\n{script_path}")
            if mode == "render":
                out_dir = os.path.dirname(self.vars["output"].get().strip())
                if out_dir and not os.path.isdir(out_dir):
                    raise ValueError(f"Output folder does not exist:\n{out_dir}")
        except Exception as exc:
            messagebox.showerror("Cannot run", str(exc))
            return

        self.clear_console()
        self._append_console(f"Starting {mode}...\n\n")
        self._append_console(self.format_command_for_display(cmd) + "\n\n")
        self.status_var.set(f"Running {mode}...")
        self._set_running_state(True)

        script_path = self.vars["script_path"].get().strip()

        def worker():
            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    cwd=os.path.dirname(os.path.abspath(script_path)) or None,
                )
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    self.output_queue.put(line)
                rc = self.process.wait()
                self.output_queue.put(("__DONE__", rc))
            except Exception as exc:
                self.output_queue.put(("__ERROR__", str(exc)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def stop_process(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self._append_console("\nTermination requested...\n")
            except Exception as exc:
                self._append_console(f"\nCould not terminate process: {exc}\n")

    def _set_running_state(self, running):
        state_run = "disabled" if running else "normal"
        state_stop = "normal" if running else "disabled"
        self.run_selected_btn.configure(state=state_run)
        self.analyze_btn.configure(state=state_run)
        self.render_btn.configure(state=state_run)
        self.stop_btn.configure(state=state_stop)

    def _poll_output_queue(self):
        try:
            while True:
                item = self.output_queue.get_nowait()
                if isinstance(item, tuple):
                    tag, payload = item
                    if tag == "__DONE__":
                        rc = payload
                        if rc == 0:
                            self._append_console("\nProcess finished successfully.\n")
                            self.status_var.set("Done")
                        else:
                            self._append_console(f"\nProcess exited with code {rc}.\n")
                            self.status_var.set(f"Failed (exit code {rc})")
                        self.process = None
                        self._set_running_state(False)
                    elif tag == "__ERROR__":
                        self._append_console(f"\nFailed to start process: {payload}\n")
                        self.status_var.set("Failed to start")
                        self.process = None
                        self._set_running_state(False)
                else:
                    self._append_console(item)
        except queue.Empty:
            pass
        self.after(100, self._poll_output_queue)

    def _append_console(self, text):
        self.console.insert("end", text)
        self.console.see("end")

    def clear_console(self):
        self.console.delete("1.0", "end")


def main():
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    app = StereoSyncGUI(root)
    app.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    root.mainloop()


if __name__ == "__main__":
    main()
