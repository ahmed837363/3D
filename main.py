"""
Abaya Fabric Draping Studio — Desktop Application
Generates abaya fabrics and drapes them on a mannequin using Blender cloth physics.
"""

import os
import sys
import uuid
import json
import subprocess

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QPixmap, QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QColorDialog,
    QSpinBox,
    QDoubleSpinBox,
    QGroupBox,
    QFormLayout,
    QSplitter,
    QFrame,
    QMessageBox,
    QFileDialog,
    QProgressBar,
    QSizePolicy,
    QScrollArea,
    QLineEdit,
    QTextEdit,
    QCheckBox,
)

from cloth_sdk import get_fabric, get_fabric_names

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
BLENDER_SCRIPT = os.path.join(BASE_DIR, "blender_script.py")
os.makedirs(OUTPUT_DIR, exist_ok=True)

DEFAULT_BLENDER_PATH = os.environ.get(
    "BLENDER_PATH",
    r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
)


TEXTURES_DIR = os.path.join(BASE_DIR, "textures")
os.makedirs(TEXTURES_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Worker thread — generates AI fabric texture
# ---------------------------------------------------------------------------
class TextureWorker(QThread):
    finished = Signal(bool, str)  # success, texture_path or error
    progress = Signal(str)
    progress_pct = Signal(int)  # 0-100

    def __init__(self, fabric_type, user_prompt, output_path, parent=None):
        super().__init__(parent)
        self.fabric_type = fabric_type
        self.user_prompt = user_prompt
        self.output_path = output_path

    def _emit_progress(self, msg):
        if msg.startswith("PROGRESS:"):
            try:
                rest = msg[len("PROGRESS:"):]
                pct_str, label = rest.split("|", 1)
                pct = int(pct_str.replace("%", ""))
                self.progress_pct.emit(pct)
                self.progress.emit(label)
            except Exception:
                self.progress.emit(msg)
        else:
            self.progress.emit(msg)

    def run(self):
        try:
            from generate_texture import generate_texture
            generate_texture(
                fabric_type=self.fabric_type,
                user_prompt=self.user_prompt,
                output_path=self.output_path,
                progress_callback=self._emit_progress,
            )
            self.finished.emit(True, self.output_path)
        except ImportError as e:
            self.finished.emit(
                False,
                f"AI dependencies not installed.\n\n"
                f"Run:\n  .\\venv\\Scripts\\pip install torch diffusers transformers accelerate\n\n"
                f"For AMD GPU acceleration also install:\n  .\\venv\\Scripts\\pip install torch-directml\n\n"
                f"Error: {e}",
            )
        except Exception as e:
            self.finished.emit(False, str(e))


# ---------------------------------------------------------------------------
# Worker thread — runs Blender in background
# ---------------------------------------------------------------------------
class BlenderWorker(QThread):
    finished = Signal(bool, str)  # success, message_or_path
    progress = Signal(str)
    progress_pct = Signal(int)  # 0-100

    def __init__(self, blender_path, params, parent=None):
        super().__init__(parent)
        self.blender_path = blender_path
        self.params = params

    def run(self):
        cmd = [
            self.blender_path,
            "--background",
            "--python", BLENDER_SCRIPT,
            "--", json.dumps(self.params),
        ]

        try:
            self.progress.emit("Launching Blender...")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("PROGRESS:"):
                    try:
                        rest = line[len("PROGRESS:"):]
                        pct_str, label = rest.split("|", 1)
                        pct = int(pct_str.replace("%", ""))
                        self.progress_pct.emit(pct)
                        self.progress.emit(label)
                    except Exception:
                        self.progress.emit(line)
                else:
                    self.progress.emit(line)

            proc.wait(timeout=300)

            if proc.returncode != 0:
                self.finished.emit(False, f"Blender exited with code {proc.returncode}")
                return

            output_path = self.params["output_path"]
            if not os.path.exists(output_path):
                self.finished.emit(False, "Render completed but no output file was created.")
                return

            self.finished.emit(True, output_path)

        except FileNotFoundError:
            self.finished.emit(
                False,
                f"Blender not found at:\n{self.blender_path}\n\n"
                "Click Settings to set the correct path.",
            )
        except subprocess.TimeoutExpired:
            self.finished.emit(False, "Blender process timed out (5 min limit).")
        except Exception as e:
            self.finished.emit(False, str(e))


# ---------------------------------------------------------------------------
# Color picker button
# ---------------------------------------------------------------------------
class ColorButton(QPushButton):
    color_changed = Signal(str)

    def __init__(self, initial_color="#1a1a2e", parent=None):
        super().__init__(parent)
        self._color = initial_color
        self.setFixedSize(48, 32)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()
        self.clicked.connect(self._pick_color)

    def _update_style(self):
        self.setStyleSheet(
            f"background-color: {self._color}; border: 2px solid #555; border-radius: 4px;"
        )

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self._color), self.parent(), "Pick Color")
        if color.isValid():
            self._color = color.name()
            self._update_style()
            self.color_changed.emit(self._color)

    def get_color(self):
        return self._color


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Abaya Fabric Studio")
        self.setMinimumSize(1100, 700)
        self.blender_path = DEFAULT_BLENDER_PATH
        self.worker = None
        self.texture_worker = None
        self.last_render_path = None
        self.current_texture_path = ""

        self._apply_dark_theme()
        self._build_ui()

    # --- Dark theme ---
    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #0e0e14;
                color: #d8d0c4;
                font-family: "Segoe UI", sans-serif;
                font-size: 13px;
            }
            QGroupBox {
                border: 1px solid #1f1f2e;
                border-radius: 8px;
                margin-top: 14px;
                padding: 14px 10px 10px 10px;
                font-weight: bold;
                color: #c4a35a;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #16161e;
                border: 1px solid #2a2a3a;
                border-radius: 5px;
                padding: 5px 8px;
                color: #d8d0c4;
                min-height: 26px;
            }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border-color: #c4a35a;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #16161e;
                color: #d8d0c4;
                selection-background-color: #c4a35a;
                selection-color: #0e0e14;
            }
            QPushButton#generateBtn {
                background-color: #c4a35a;
                color: #0e0e14;
                font-weight: bold;
                font-size: 14px;
                border-radius: 6px;
                padding: 10px;
                letter-spacing: 1px;
            }
            QPushButton#generateBtn:hover { background-color: #d4b36a; }
            QPushButton#generateBtn:disabled { background-color: #555; color: #888; }
            QPushButton#saveBtn, QPushButton#settingsBtn {
                background-color: #1f1f2e;
                border: 1px solid #2a2a3a;
                border-radius: 5px;
                padding: 7px 14px;
                color: #d8d0c4;
            }
            QPushButton#saveBtn:hover, QPushButton#settingsBtn:hover {
                border-color: #c4a35a;
            }
            QProgressBar {
                background-color: #16161e;
                border: 1px solid #2a2a3a;
                border-radius: 5px;
                text-align: center;
                color: #c4a35a;
                min-height: 20px;
            }
            QProgressBar::chunk {
                background-color: #c4a35a;
                border-radius: 4px;
            }
            QLabel#statusLabel {
                color: #888;
                font-size: 11px;
            }
            QLabel#previewPlaceholder {
                color: #444;
                font-size: 15px;
            }
            QLabel#titleLabel {
                color: #c4a35a;
                font-size: 20px;
                font-weight: 300;
                letter-spacing: 3px;
            }
            QFrame#previewFrame {
                background-color: #111118;
                border: 1px solid #1f1f2e;
                border-radius: 10px;
            }
            QLineEdit, QTextEdit {
                background-color: #16161e;
                border: 1px solid #2a2a3a;
                border-radius: 5px;
                padding: 5px 8px;
                color: #d8d0c4;
                font-size: 13px;
            }
            QLineEdit:focus, QTextEdit:focus {
                border-color: #c4a35a;
            }
        """)

    # --- Build UI ---
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(16, 12, 16, 12)

        # Title bar
        title = QLabel("ABAYA  FABRIC  STUDIO")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignCenter)
        root_layout.addWidget(title)

        # Splitter: controls | preview
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)
        root_layout.addWidget(splitter, 1)

        # ---- Left: controls ----
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QFrame.NoFrame)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_scroll.setMaximumWidth(380)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 8, 0)

        # Fabric group
        fabric_group = QGroupBox("Fabric")
        fabric_form = QFormLayout(fabric_group)
        fabric_form.setLabelAlignment(Qt.AlignRight)

        self.fabric_color_btn = ColorButton("#1a1a2e")
        self.fabric_color_hex = QLabel("#1a1a2e")
        self.fabric_color_hex.setStyleSheet("color: #aaa; font-family: Consolas;")
        self.fabric_color_btn.color_changed.connect(self.fabric_color_hex.setText)
        color_row = QHBoxLayout()
        color_row.addWidget(self.fabric_color_btn)
        color_row.addWidget(self.fabric_color_hex)
        color_row.addStretch()
        fabric_form.addRow("Color:", color_row)

        self.fabric_type_combo = QComboBox()
        self.fabric_type_combo.addItems(get_fabric_names())
        self.fabric_type_combo.currentIndexChanged.connect(self._on_fabric_type_changed)
        fabric_form.addRow("Type:", self.fabric_type_combo)

        self.fabric_desc_label = QLabel()
        self.fabric_desc_label.setStyleSheet("color: #888; font-size: 11px; font-style: italic;")
        self.fabric_desc_label.setWordWrap(True)
        fabric_form.addRow("", self.fabric_desc_label)
        self._on_fabric_type_changed(0)

        controls_layout.addWidget(fabric_group)

        # Pattern Source group (Procedural vs Seamly2D)
        pattern_source_group = QGroupBox("Pattern Source")
        pattern_source_form = QFormLayout(pattern_source_group)
        pattern_source_form.setLabelAlignment(Qt.AlignRight)

        self.pattern_source_combo = QComboBox()
        self.pattern_source_combo.addItems([
            "Procedural (Simple)",
            "FreeSewing (Accurate)"
        ])
        self.pattern_source_combo.setToolTip(
            "Procedural: Quick tube/panel geometry\n"
            "FreeSewing: Measures MPFB mannequin for perfect fit"
        )
        self.pattern_source_combo.currentIndexChanged.connect(self._on_pattern_source_changed)
        pattern_source_form.addRow("Method:", self.pattern_source_combo)

        self.freesewing_info_label = QLabel(
            "FreeSewing measures the MPFB\n"
            "mannequin directly for perfect fit:\n"
            "• Body-conforming tube geometry\n"
            "• Auto-fitted to body shape\n"
            "• Clean cloth simulation"
        )
        self.freesewing_info_label.setStyleSheet("color: #888; font-size: 10px;")
        pattern_source_form.addRow("", self.freesewing_info_label)

        # Size selector (for FreeSewing)
        self.size_combo = QComboBox()
        self.size_combo.addItems(["XS", "S", "M", "L", "XL", "XXL"])
        self.size_combo.setCurrentIndex(2)  # Default: M
        self.size_combo.setToolTip("Standard garment size — affects chest, waist, hip proportions")
        self.size_label = QLabel("Size:")
        pattern_source_form.addRow(self.size_label, self.size_combo)

        # Height input (for FreeSewing)
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(140.0, 200.0)
        self.height_spin.setValue(165.0)
        self.height_spin.setSingleStep(1.0)
        self.height_spin.setSuffix(" cm")
        self.height_spin.setToolTip("Person's height — affects garment length")
        self.height_label = QLabel("Height:")
        pattern_source_form.addRow(self.height_label, self.height_spin)

        # Default to FreeSewing for better drape quality
        self.pattern_source_combo.setCurrentIndex(1)
        self._on_pattern_source_changed(1)

        controls_layout.addWidget(pattern_source_group)

        # Texture source group
        texture_group = QGroupBox("Texture")
        texture_form = QFormLayout(texture_group)
        texture_form.setLabelAlignment(Qt.AlignRight)

        self.texture_source_combo = QComboBox()
        self.texture_source_combo.addItems([
            "Solid Color",
            "Procedural Pattern",
            "AI Generated",
        ])
        self.texture_source_combo.currentIndexChanged.connect(self._on_texture_source_changed)
        texture_form.addRow("Source:", self.texture_source_combo)

        # --- Procedural pattern widgets ---
        self.pattern_combo = QComboBox()
        self.pattern_combo.addItems(["Stripes", "Diamonds", "Floral", "Geometric"])
        self.pattern_combo_label = QLabel("Pattern:")
        texture_form.addRow(self.pattern_combo_label, self.pattern_combo)

        self.pattern_color_btn = ColorButton("#c4a35a")
        self.pattern_color_hex = QLabel("#c4a35a")
        self.pattern_color_hex.setStyleSheet("color: #aaa; font-family: Consolas;")
        self.pattern_color_btn.color_changed.connect(self.pattern_color_hex.setText)
        pcolor_row = QHBoxLayout()
        pcolor_row.addWidget(self.pattern_color_btn)
        pcolor_row.addWidget(self.pattern_color_hex)
        pcolor_row.addStretch()
        self.pattern_color_label = QLabel("Color:")
        texture_form.addRow(self.pattern_color_label, pcolor_row)

        self.pattern_scale_spin = QDoubleSpinBox()
        self.pattern_scale_spin.setRange(1.0, 50.0)
        self.pattern_scale_spin.setValue(5.0)
        self.pattern_scale_spin.setSingleStep(0.5)
        self.pattern_scale_label = QLabel("Scale:")
        texture_form.addRow(self.pattern_scale_label, self.pattern_scale_spin)

        # --- AI prompt widgets ---
        self.ai_prompt_edit = QTextEdit()
        self.ai_prompt_edit.setPlaceholderText(
            "Describe your fabric...\n"
            "e.g. dark navy embroidered silk with gold floral motifs"
        )
        self.ai_prompt_edit.setFixedHeight(80)
        self.ai_prompt_label = QLabel("Prompt:")
        texture_form.addRow(self.ai_prompt_label, self.ai_prompt_edit)

        self._on_texture_source_changed(0)  # hide pattern/AI initially
        controls_layout.addWidget(texture_group)

        # Quality group
        quality_group = QGroupBox("Quality")
        quality_form = QFormLayout(quality_group)
        quality_form.setLabelAlignment(Qt.AlignRight)

        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["EEVEE (GPU — Fast)", "Cycles (CPU — Slower, higher quality)"])
        self.engine_combo.setCurrentIndex(0)
        self.engine_combo.setToolTip(
            "EEVEE uses your RX 5700 XT GPU via Vulkan (fast!)\n"
            "Cycles falls back to CPU because RX 5700 XT is RDNA 1 (HIP needs RDNA 2+)"
        )
        quality_form.addRow("Engine:", self.engine_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["Low (Fast)", "Medium", "High", "Ultra"])
        self.quality_combo.setCurrentIndex(1)
        quality_form.addRow("Physics:", self.quality_combo)

        self.samples_combo = QComboBox()
        self.samples_combo.addItems(["32  (Preview)", "64  (Good)", "128 (High)", "256 (Production)"])
        self.samples_combo.setCurrentIndex(1)
        quality_form.addRow("Render:", self.samples_combo)

        self.open_blender_cb = QCheckBox("Open in Blender after render")
        self.open_blender_cb.setChecked(True)
        self.open_blender_cb.setStyleSheet("color: #d8d0c4; padding: 4px 0;")
        quality_form.addRow("", self.open_blender_cb)

        controls_layout.addWidget(quality_group)

        # Buttons
        controls_layout.addSpacing(8)

        self.generate_btn = QPushButton("GENERATE  &&  DRAPE")
        self.generate_btn.setObjectName("generateBtn")
        self.generate_btn.setCursor(Qt.PointingHandCursor)
        self.generate_btn.clicked.connect(self._on_generate)
        controls_layout.addWidget(self.generate_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setVisible(False)
        controls_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        controls_layout.addWidget(self.status_label)

        controls_layout.addStretch()

        # Bottom buttons row
        bottom_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Image")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save)
        bottom_row.addWidget(self.save_btn)

        settings_btn = QPushButton("Settings")
        settings_btn.setObjectName("settingsBtn")
        settings_btn.clicked.connect(self._on_settings)
        bottom_row.addWidget(settings_btn)

        controls_layout.addLayout(bottom_row)
        controls_scroll.setWidget(controls)
        splitter.addWidget(controls_scroll)

        # ---- Right: preview ----
        preview_frame = QFrame()
        preview_frame.setObjectName("previewFrame")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(12, 12, 12, 12)

        self.preview_label = QLabel("Configure your fabric and click\nGENERATE & DRAPE")
        self.preview_label.setObjectName("previewPlaceholder")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_layout.addWidget(self.preview_label)

        splitter.addWidget(preview_frame)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 700])

    # --- Fabric type description ---
    def _on_fabric_type_changed(self, index):
        fabric_key = self.fabric_type_combo.currentText().lower()
        fabric = get_fabric(fabric_key)
        self.fabric_desc_label.setText(f"ClothSDK: {fabric.description}")

    # --- Texture source visibility ---
    def _on_texture_source_changed(self, index):
        # 0 = Solid Color, 1 = Procedural Pattern, 2 = AI Generated
        show_pattern = index == 1
        show_ai = index == 2

        self.pattern_combo.setVisible(show_pattern)
        self.pattern_combo_label.setVisible(show_pattern)
        self.pattern_color_btn.setVisible(show_pattern)
        self.pattern_color_hex.setVisible(show_pattern)
        self.pattern_color_label.setVisible(show_pattern)
        self.pattern_scale_spin.setVisible(show_pattern)
        self.pattern_scale_label.setVisible(show_pattern)

        self.ai_prompt_edit.setVisible(show_ai)
        self.ai_prompt_label.setVisible(show_ai)

    # --- Pattern source visibility ---
    def _on_pattern_source_changed(self, index):
        # 0 = Procedural, 1 = FreeSewing
        show_freesewing = index == 1
        self.freesewing_info_label.setVisible(show_freesewing)
        self.size_combo.setVisible(show_freesewing)
        self.size_label.setVisible(show_freesewing)
        self.height_spin.setVisible(show_freesewing)
        self.height_label.setVisible(show_freesewing)

    # --- Generate ---
    def _on_generate(self):
        if self.worker and self.worker.isRunning():
            return
        if self.texture_worker and self.texture_worker.isRunning():
            return

        texture_source = self.texture_source_combo.currentIndex()

        # If AI texture, generate texture first, then run Blender
        if texture_source == 2:
            self._start_ai_texture_generation()
            return

        # Otherwise go straight to Blender
        self._start_blender_render()

    def _start_ai_texture_generation(self):
        job_id = str(uuid.uuid4())[:8]
        texture_path = os.path.join(TEXTURES_DIR, f"{job_id}_texture.png")
        fabric_type = self.fabric_type_combo.currentText().lower()
        user_prompt = self.ai_prompt_edit.toPlainText().strip()

        self.generate_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Generating AI fabric texture...")
        self.status_label.setStyleSheet("color: #888;")

        self.texture_worker = TextureWorker(fabric_type, user_prompt, texture_path)
        self.texture_worker.progress.connect(self._on_progress)
        self.texture_worker.progress_pct.connect(self._on_pct)
        self.texture_worker.finished.connect(self._on_texture_finished)
        self.texture_worker.start()

    def _on_texture_finished(self, success, result):
        if success:
            self.current_texture_path = result
            self.status_label.setText("Texture generated! Starting Blender...")
            self._start_blender_render()
        else:
            self.generate_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.status_label.setText(result)
            self.status_label.setStyleSheet("color: #e88;")
            QMessageBox.warning(self, "AI Texture Failed", result)

    def _start_blender_render(self):
        texture_source = self.texture_source_combo.currentIndex()
        pattern_map = {0: "stripes", 1: "diamonds", 2: "floral", 3: "geometric"}
        quality_map = {0: 5, 1: 10, 2: 15, 3: 20}
        samples_map = {0: 32, 1: 64, 2: 128, 3: 256}

        job_id = str(uuid.uuid4())[:8]
        output_path = os.path.join(OUTPUT_DIR, f"{job_id}.png")
        blend_path = os.path.join(OUTPUT_DIR, f"{job_id}.blend")

        if texture_source == 1:
            pattern = pattern_map[self.pattern_combo.currentIndex()]
        else:
            pattern = "none"

        fabric_key = self.fabric_type_combo.currentText().lower()
        fabric = get_fabric(fabric_key)

        # Determine pattern source (procedural vs FreeSewing)
        pattern_source_idx = self.pattern_source_combo.currentIndex()
        pattern_source = "freesewing" if pattern_source_idx == 1 else "procedural"

        # FreeSewing uses MPFB mannequin measurements directly - no size needed
        # We just tell Blender to use freesewing mode

        params = {
            "fabric_color": self.fabric_color_btn.get_color(),
            "fabric_type": fabric_key,
            "pattern": pattern,
            "pattern_color": self.pattern_color_btn.get_color(),
            "pattern_scale": self.pattern_scale_spin.value(),
            "drape_quality": quality_map[self.quality_combo.currentIndex()],
            "render_samples": samples_map[self.samples_combo.currentIndex()],
            "render_engine": "EEVEE" if self.engine_combo.currentIndex() == 0 else "CYCLES",
            "output_path": output_path,
            "texture_path": self.current_texture_path if texture_source == 2 else "",
            "blend_path": blend_path,
            "open_in_blender": self.open_blender_cb.isChecked(),
            "cloth_params": fabric.to_blender_params(),
            # Pattern source (freesewing measures MPFB mannequin directly)
            "pattern_source": pattern_source,
            # Size and height for FreeSewing
            "garment_size": self.size_combo.currentText(),
            "garment_height": self.height_spin.value(),
            # Warp XPBD draper (external physics engine — more accurate, no crashes)
            "use_warp_draper": (pattern_source == "freesewing"),
            "warp_params": fabric.to_warp_params(),
        }
        self.last_blend_path = blend_path

        self.generate_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Launching Blender...")
        self.status_label.setStyleSheet("color: #888;")

        self.worker = BlenderWorker(self.blender_path, params)
        self.worker.progress.connect(self._on_progress)
        self.worker.progress_pct.connect(self._on_pct)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, msg):
        self.status_label.setText(msg)
        print(f"[LOG] {msg}", flush=True)

    def _on_pct(self, pct):
        self.progress_bar.setValue(pct)
        print(f"[{pct}%]", end=" ", flush=True)

    def _on_finished(self, success, message):
        self.generate_btn.setEnabled(True)
        self.progress_bar.setValue(100 if success else 0)
        self.progress_bar.setVisible(False)

        if success:
            print(f"\n[DONE] Render saved to: {message}", flush=True)
            self.last_render_path = message
            pixmap = QPixmap(message)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
            self.status_label.setText("Render complete!")
            self.status_label.setStyleSheet("color: #8e8;")
            self.save_btn.setEnabled(True)

            # Open in Blender if checkbox is checked
            if (
                self.open_blender_cb.isChecked()
                and hasattr(self, "last_blend_path")
                and os.path.exists(self.last_blend_path)
            ):
                print(f"[OPEN] Launching Blender with: {self.last_blend_path}", flush=True)
                subprocess.Popen([self.blender_path, self.last_blend_path])
                self.status_label.setText("Render complete! Blender opened.")
        else:
            self.status_label.setText(message)
            self.status_label.setStyleSheet("color: #e88;")
            QMessageBox.warning(self, "Generation Failed", message)

    # --- Save ---
    def _on_save(self):
        if not self.last_render_path or not os.path.exists(self.last_render_path):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Rendered Image", "abaya_render.png", "PNG Images (*.png)"
        )
        if path:
            import shutil
            shutil.copy2(self.last_render_path, path)
            self.status_label.setText(f"Saved to {path}")
            self.status_label.setStyleSheet("color: #8e8;")

    # --- Settings (Blender path) ---
    def _on_settings(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Blender Executable",
            os.path.dirname(self.blender_path),
            "Executable (blender.exe blender);;All Files (*)",
        )
        if path:
            self.blender_path = path
            QMessageBox.information(self, "Settings", f"Blender path set to:\n{path}")

    # --- Resize preview ---
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.last_render_path and os.path.exists(self.last_render_path):
            pixmap = QPixmap(self.last_render_path)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  ABAYA FABRIC STUDIO")
    print("=" * 60)
    print("  GPU: RX 5700 XT (RDNA 1)")
    print("  AI Textures: DirectML (torch-directml)")
    print("  EEVEE: Uses GPU via Vulkan (fast)")
    print("  Cycles: CPU only (RDNA 1 not supported by HIP)")
    print("  ClothSDK: %d fabrics loaded" % len(get_fabric_names()))
    print("  Tip: Use EEVEE for GPU-accelerated rendering")
    print("=" * 60)
    print("  Logs will appear here during generation.")
    print("=" * 60)
    print(flush=True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
