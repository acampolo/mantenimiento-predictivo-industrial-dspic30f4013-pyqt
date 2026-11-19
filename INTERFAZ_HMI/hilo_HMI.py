"""
HMI de Mantenimiento Predictivo — Edge Computing
Supervisión de motores DC: vibración, corriente, RPM, FFT, RUL y diagnóstico ML.
Comunicación Modbus RTU con dsPIC30F.
"""
from __future__ import annotations

import sys
import time
import hashlib
import logging
import queue
import threading
from collections import deque

import numpy as np
import pyqtgraph as pg
from PyQt5.QtWidgets import (QApplication, QMainWindow, QLabel, QLineEdit,
                             QVBoxLayout, QHBoxLayout, QWidget, QPushButton,
                             QComboBox, QTabWidget, QGroupBox, QSizePolicy,
                             QGridLayout, QSlider, QFrame, QScrollArea, QTextEdit,
                             QTableWidget, QTableWidgetItem, QHeaderView)
from PyQt5.QtCore import pyqtSlot, Qt, QTimer, QRectF
from PyQt5.QtGui import QFont, QPainter, QColor, QPen, QBrush, QLinearGradient

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.gridspec as gridspec

from sklearn.ensemble import IsolationForest
from scipy.signal import hilbert, freqz

from hilo_modbus import TrabajoModbus
from db_manager import DBManager
from firma_arranque import AnalizadorFirma, DetectorTendencia

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN GLOBAL
# ══════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    filename='hmi_edge_errores.log',
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Paleta
C_BG = '#0A0E1A'
C_AX_BG = '#0D1525'
C_PANEL = '#0F1830'
C_BORDER = '#1A3060'
C_GRID = '#152040'
C_VIB = '#00FFCC'
C_CORR = '#FF8C00'
C_VIB_RAW = '#FF00FF'
C_CORR_RAW = '#FFE000'
C_REF = '#39FF14'
C_ALARM = '#FF3333'
C_EMA = '#00BFFF'
C_IIR = '#39FF14'
C_RAW_DIM = '#FF6B6B'
C_TEXT = '#C8D8F0'
C_TEXT_DIM = '#556688'

pg.setConfigOption('background', C_BG)
pg.setConfigOption('foreground', C_TEXT)
pg.setConfigOptions(antialias=True)

# Filtro IIR Butterworth pasa-bajas de 2.º orden (coeficientes del dsPIC)
IIR_B = (0.020083, 0.040167, 0.020083)
IIR_A = (1.0, -1.561018, 0.641351)

# Índices de pestañas principales
TAB_FILTROS = 2
TAB_RUL = 3

GRP_STYLE = ("QGroupBox{border:1px solid #2A5090;border-radius:6px;margin-top:10px;"
             "font-weight:bold;color:#7AAFFF;font-size:9pt;} "
             "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px;}")
SCROLL_STYLE = (f"QScrollArea{{border:none;background:{C_BG};}} "
                f"QScrollBar:vertical{{background:#0D1525;width:8px;border-radius:4px;}} "
                f"QScrollBar::handle:vertical{{background:#1A3060;border-radius:4px;}}")
INPUT_STYLE = (f"background:#0D1525;border:1px solid {C_BORDER};border-radius:4px;"
               f"padding:3px 6px;color:#FFD700;font-family:Consolas;")


# ══════════════════════════════════════════════════════════════════════════════
# UTILIDADES DE PROCESAMIENTO
# ══════════════════════════════════════════════════════════════════════════════
class Kalman1D:
    """Filtro de Kalman escalar para suavizado de variables de proceso."""

    def __init__(self, q=0.01, r=1.0):
        self.x = 0.0
        self.p = 1.0
        self.q = q
        self.r = r

    def predict(self):
        self.p += self.q
        return self.x

    def update(self, z):
        k = self.p / (self.p + self.r)
        self.x += k * (z - self.x)
        self.p *= (1 - k)
        return self.x

    def reset(self):
        self.x = 0.0
        self.p = 1.0


class FiltroBiquad:
    """Filtro IIR biquad en forma directa I."""

    def __init__(self, b, a):
        self.b = b
        self.a = a
        self.reset()

    def reset(self):
        self.x1 = self.x2 = 0.0
        self.y1 = self.y2 = 0.0

    def procesar(self, x):
        y = (self.b[0] * x + self.b[1] * self.x1 + self.b[2] * self.x2
             - self.a[1] * self.y1 - self.a[2] * self.y2)
        self.x2, self.x1 = self.x1, x
        self.y2, self.y1 = self.y1, y
        return y


def _estilizar_ax(ax, titulo, ylabel, color_y, xlabel="Muestras"):
    ax.set_facecolor(C_AX_BG)
    ax.set_title(titulo, color='#AACCFF', fontsize=9, fontweight='bold', pad=6)
    ax.tick_params(colors='#6688AA', labelsize=7)
    ax.set_ylabel(ylabel, color=color_y, fontsize=8)
    ax.tick_params(axis='y', labelcolor=color_y)
    for spine in ax.spines.values():
        spine.set_edgecolor(C_BORDER)
    ax.grid(True, color=C_GRID, linestyle='--', linewidth=0.5, alpha=0.8)
    ax.set_xlabel(xlabel, color=C_TEXT_DIM, fontsize=7)


def _bloquear_grafica_pg(plot_item):
    plot_item.setMouseEnabled(x=False, y=False)
    if hasattr(plot_item, 'hideButtons'):
        plot_item.hideButtons()
    plot_item.setMenuEnabled(False)


def _nueva_figura(**kw):
    fig = Figure(facecolor=C_BG)
    fig.subplots_adjust(**({'left': 0.08, 'right': 0.92, 'top': 0.92, 'bottom': 0.15} | kw))
    canvas = FigureCanvas(fig)
    canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return fig, canvas


def _leyenda(ax, **kw):
    ax.legend(fontsize=7, facecolor='#0A1020', edgecolor=C_BORDER, labelcolor='white', **kw)


# ══════════════════════════════════════════════════════════════════════════════
# WIDGETS
# ══════════════════════════════════════════════════════════════════════════════
class DisplayDigital(QWidget):
    def __init__(self, titulo, unidad, color_acento, min_val=0, max_val=100, parent=None):
        super().__init__(parent)
        self.titulo = titulo
        self.unidad = unidad
        self.color = QColor(color_acento)
        self.min_val = min_val
        self.max_val = max_val
        self.valor = 0.0
        self.setMinimumSize(160, 110)
        self.setMaximumSize(220, 130)

    def set_valor(self, v):
        self.valor = float(v)
        self.update()

    def set_color_acento(self, color_hex):
        self.color = QColor(color_hex)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        p.setPen(QPen(self.color.darker(180), 1))
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor('#0D1830'))
        grad.setColorAt(1, QColor('#070C18'))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 8, 8)

        pct = max(0.0, min(1.0, (self.valor - self.min_val) / max(1, self.max_val - self.min_val)))
        bar_w = int((w - 16) * pct)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(self.color.darker(200)))
        p.drawRoundedRect(8, h - 14, w - 16, 8, 3, 3)
        if bar_w > 0:
            p.setBrush(QBrush(self.color if pct < 0.85 else QColor(C_ALARM)))
            p.drawRoundedRect(8, h - 14, bar_w, 8, 3, 3)

        p.setPen(QPen(self.color.lighter(130)))
        p.setFont(QFont("Consolas", 8, QFont.Bold))
        p.drawText(8, 18, self.titulo.upper())

        texto_val = f"{self.valor:.1f}"
        p.setPen(QPen(QColor('white')))
        p.setFont(QFont("Consolas", 26, QFont.Bold))
        tw = p.fontMetrics().horizontalAdvance(texto_val)
        p.drawText(w - tw - 40, 62, texto_val)

        p.setPen(QPen(self.color))
        p.setFont(QFont("Consolas", 10))
        p.drawText(w - 36, 62, self.unidad)
        p.end()


class PanelDatosVivo(QWidget):
    def __init__(self, campos, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setStyleSheet(f"background:{C_PANEL};border:1px solid {C_BORDER};border-radius:5px;")

        layout = QHBoxLayout()
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(16)

        self._labels = {}
        for i, (clave, etiqueta, unidad, color) in enumerate(campos):
            col = QVBoxLayout()
            col.setSpacing(0)

            lbl_t = QLabel(etiqueta.upper())
            lbl_t.setFont(QFont("Consolas", 7, QFont.Bold))
            lbl_t.setStyleSheet(f"color:{C_TEXT_DIM};background:transparent;border:none;")
            lbl_t.setAlignment(Qt.AlignCenter)

            lbl_v = QLabel("—")
            lbl_v.setFont(QFont("Consolas", 14, QFont.Bold))
            lbl_v.setStyleSheet(f"color:{color};background:transparent;border:none;")
            lbl_v.setAlignment(Qt.AlignCenter)
            lbl_v.setMinimumWidth(90)

            col.addWidget(lbl_t)
            col.addWidget(lbl_v)
            self._labels[clave] = (lbl_v, unidad, color)
            layout.addLayout(col)

            if i < len(campos) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet(f"color:{C_BORDER};background:{C_BORDER};border:none;max-width:1px;")
                layout.addWidget(sep)
        self.setLayout(layout)

    def actualizar(self, datos: dict):
        for clave, (lbl, unidad, color) in self._labels.items():
            val = datos.get(clave, 0.0)
            if clave == 'ml_status':
                txt = str(val)
                c = C_ALARM if "ANOMALÍA" in txt else '#FFD700' if "CALIBRANDO" in txt else color
                lbl.setStyleSheet(f"color:{c};background:transparent;border:none;")
            texto = f"{val:.1f}" if isinstance(val, float) else f"{val}"
            lbl.setText(f"{texto} {unidad}".strip())


class DashboardSupervision(QWidget):
    """Ventana secundaria de indicadores clave del activo."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Supervisión de Activo — Indicadores Clave")
        self.resize(1000, 550)
        self.setStyleSheet(f"background-color: #050811; color: {C_TEXT};")

        layout = QVBoxLayout()
        self.lbl_titulo = QLabel("DIAGNÓSTICO AVANZADO DE ACTIVOS")
        self.lbl_titulo.setFont(QFont("Consolas", 12, QFont.Bold))
        self.lbl_titulo.setStyleSheet("color: #7AAFFF; padding: 10px;")
        self.lbl_titulo.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_titulo)

        self.box_carga, self.lbl_carga_val = self._caja("ÍNDICE DE CARGA", "0.0 %", 36, "#00FFCC")
        self.lbl_carga_txt = QLabel("ESTADO: CALCULANDO")
        self.lbl_carga_txt.setFont(QFont("Consolas", 12, QFont.Bold))
        self.lbl_carga_txt.setAlignment(Qt.AlignCenter)
        self.box_carga.layout().addWidget(self.lbl_carga_txt)

        self.box_salud, self.lbl_salud_val = self._caja("SALUD GLOBAL", "— %", 36, "#00FFCC")
        self.box_rul, self.lbl_rul_val = self._caja("PRONÓSTICO DE VIDA (RUL)", "ESPERANDO...", 24, "#FFD700")
        self.box_conf, self.lbl_conf_val = self._caja("CONFIANZA DEL SISTEMA", "— %", 28, "#AADDFF")
        self.box_eta, self.lbl_eta_val = self._caja("EFICIENCIA ESTIMADA (η)", "— %", 28, "#39FF14")
        self.box_disp, self.lbl_disp_val = self._caja("DISPONIBILIDAD TÉCNICA (Ai)", "100.0 %", 28, "#39FF14")
        self.box_deriva, self.lbl_deriva_val = self._caja("DERIVA OPERACIONAL", "— %", 28, "#FF8C00")
        self.box_diag, self.lbl_diag_val = self._caja("DIAGNÓSTICO ML", "EN ESPERA", 16, "#39FF14")

        grid = QGridLayout()
        grid.setSpacing(15)
        grid.addWidget(self.box_carga, 0, 0, 2, 1)
        grid.addWidget(self.box_salud, 0, 1, 2, 1)
        grid.addWidget(self.box_rul, 2, 0)
        grid.addWidget(self.box_conf, 2, 1)
        grid.addWidget(self.box_eta, 3, 0)
        grid.addWidget(self.box_disp, 3, 1)
        grid.addWidget(self.box_deriva, 4, 0)
        grid.addWidget(self.box_diag, 4, 1)
        layout.addLayout(grid)
        self.setLayout(layout)

    @staticmethod
    def _caja(titulo, texto, size, color):
        box = QGroupBox(titulo)
        box.setFont(QFont("Consolas", 10, QFont.Bold))
        lay = QVBoxLayout()
        lbl = QLabel(texto)
        lbl.setFont(QFont("Consolas", size, QFont.Bold))
        lbl.setStyleSheet(f"color: {color};")
        lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl)
        box.setLayout(lay)
        return box, lbl

    def actualizar_dashboard(self, carga, estado_lbl, color, rul_str, eta, disp, activo_id):
        self.lbl_titulo.setText(f"DIAGNÓSTICO AVANZADO DE ACTIVOS — {activo_id}")
        self.lbl_carga_val.setText(f"{carga}%")
        self.lbl_carga_val.setStyleSheet(f"color: {color};")
        self.lbl_carga_txt.setText(f"ESTADO: {estado_lbl}")
        self.lbl_carga_txt.setStyleSheet(f"color: {color};")
        self.lbl_rul_val.setText(rul_str)
        self.lbl_eta_val.setText(f"{eta:.1f} %")
        self.lbl_disp_val.setText(f"{disp:.1f} %")
        c = "#FF3333" if disp < 95.0 else "#FFD700" if disp < 98.0 else "#39FF14"
        self.lbl_disp_val.setStyleSheet(f"color: {c};")

    def actualizar_avanzado(self, salud, conf, deriva, diag):
        self.lbl_salud_val.setText(f"{salud:.1f} %")
        c = "#FF3333" if salud < 50 else "#FFD700" if salud < 80 else "#00FFCC"
        self.lbl_salud_val.setStyleSheet(f"color: {c};")
        self.lbl_conf_val.setText(f"{conf:.1f} %")
        self.lbl_deriva_val.setText(f"{deriva:.1f} %")
        self.lbl_diag_val.setText(diag)
        self.lbl_diag_val.setStyleSheet(f"color: {'#39FF14' if diag == 'OPERACION NOMINAL' else '#FF3333'};")


# ══════════════════════════════════════════════════════════════════════════════
# VENTANA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════
class PanelHMI(QMainWindow):
    MAX_DATOS = 60
    MAX_DATOS_LARGO = 512
    LIM_VIB = 50
    LIM_CORR = 800
    MTTR_TEORICO = 2.0

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HMI de Mantenimiento Predictivo — Edge Computing")
        self.resize(1280, 860)
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background-color: {C_BG}; color: {C_TEXT}; }}
            QGroupBox {{ border: 1px solid {C_BORDER}; border-radius: 6px; margin-top: 10px; font-weight: bold; color: #7AAFFF; font-size: 9pt; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
            QComboBox, QLineEdit {{ background-color: #0D1525; border: 1px solid {C_BORDER}; border-radius: 4px; padding: 4px 8px; color: {C_TEXT}; }}
            QTabWidget::pane {{ border: 1px solid {C_BORDER}; background: {C_BG}; }}
            QTabBar::tab {{ background: #0D1525; padding: 7px 14px; color: #6090B0; border-top-left-radius: 4px; border-top-right-radius: 4px; font-size: 8pt; }}
            QTabBar::tab:selected {{ background: {C_PANEL}; color: #00FFCC; font-weight: bold; }}
            QTabBar::tab:hover {{ background: #142035; color: #AADDFF; }}
            QSlider::groove:horizontal {{ background: #0D1525; border: 1px solid {C_BORDER}; height: 6px; border-radius: 3px; }}
            QSlider::handle:horizontal {{ background: #00BFFF; width: 14px; height: 14px; margin: -4px 0; border-radius: 7px; }}
            QSlider::sub-page:horizontal {{ background: #004466; border-radius: 3px; }}
        """)

        # Hilo trabajador de base de datos
        self.db_queue = queue.Queue()
        self.db_worker = threading.Thread(target=self._db_worker_loop, daemon=True)
        self.db_worker.start()

        self.dash_secundario = DashboardSupervision()
        self.dash_secundario.show()

        # Filtros
        self.kalman_corr = Kalman1D(0.02, 4)
        self.kalman_vib = Kalman1D(0.02, 1)
        self.kalman_rpm = Kalman1D(0.05, 4)
        self.filtro_iir = FiltroBiquad(IIR_B, IIR_A)

        # Indicadores
        self.health_score = 100.0
        self.hist_sim = deque(maxlen=30)
        self.drift_operacional = 0.0
        self.impacto_actual = 0.0
        self.confianza_sistema = 100.0
        self.disponibilidad_tecnica = 100.0

        # Buffers de señal
        n = self.MAX_DATOS
        self.hist_v_p1 = np.zeros(n)
        self.hist_iir_p1 = np.zeros(n)
        self.hist_c_p1 = np.zeros(n)
        self.hist_v_p2 = np.zeros(n)
        self.hist_c_p2 = np.zeros(n)
        self.matriz_fft = np.zeros((32, n))
        self.envolvente = np.zeros(n)
        self.buffer_vib_largo = deque(maxlen=self.MAX_DATOS_LARGO)

        self.hist_raw_didact = [0.0] * n
        self.hist_ema_didact = [0.0] * n
        self.hist_iir_didact = [0.0] * n
        self.hist_rms_didact = [0.0] * n
        self.hist_etime = [0.0] * n
        self.hist_efreq = [0.0] * n
        self.hist_rpm_med = [0.0] * n
        self.hist_rpm_obj = [0.0] * n
        self.ema_sim = 0.0
        self.alpha_didact = 0.125
        self.fft_bins = np.zeros(32)
        self._rpm_buffer = deque(maxlen=6)

        self._motor_id = "UNSET"
        self._activo_id = "UNSET"
        self._eta_pct = 0.0
        self._carga_pct = 0.0

        # Aprendizaje automático
        self._ml_modelo = IsolationForest(n_estimators=50, contamination='auto', random_state=42)
        self._ml_entrenado = False
        self._ml_entrenando = False
        self._ml_buffer = deque(maxlen=500)
        self._ML_SAMPLES_TRAIN = 150
        self._muestras_desde_entrenamiento = 0

        # RUL
        self._placa = {'rpm_nom': 2200.0, 'i_vacio': 350.0, 'i_nom': 2400.0,
                       'i_bloq': 5600.0, 'vib_nom': 20.0, 'vida_nom': 17520.0}
        self._placa_cargada = False
        self._rul_activo = False
        self._calibrando_rul = False
        self._buffer_calibracion = []
        self._MUESTRAS_CALIBRACION = 200
        self._MAX_HIST_RUL = 500
        self._hist_di = []
        self._hist_di_t = []
        self._di_acc = deque(maxlen=7)
        self._t0_vida_motor = None
        self._ref_sano = None
        self._cov_sano = None
        self._inv_cov_sano = None
        self._rul_horas = None
        self._rul_std_horas = None
        self._di_umbral = 6.0

        self._historial_eventos = []
        self._sobrecarga_count = 0
        self._desbalance_count = 0
        self._horas_acum = 0.0
        self._timer_horas = QTimer()
        self._timer_horas.timeout.connect(self._tick_horas)

        # Persistencia y firma de arranque
        self._db = DBManager()
        self._firma = None
        self._firma_ref_vec = None
        self._similitud_sesion = None
        self._tendencia = DetectorTendencia()
        self._muestra_sesion = 0
        self._di_sesion = []
        self._cargar_tendencia_bd()

        self.hilo_trabajo = None
        self._construir_interfaz()

    # ──────────────────────────────────────────────────────────────────────────
    # CICLO DE VIDA
    # ──────────────────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        if self.hilo_trabajo:
            self.hilo_trabajo.detener()
            self.hilo_trabajo.wait(2000)
        self._timer_horas.stop()
        self.dash_secundario.close()
        self.db_queue.put(None)
        event.accept()

    def _db_worker_loop(self):
        while True:
            tarea = self.db_queue.get()
            if tarea is None:
                break
            func, args, kwargs = tarea
            try:
                func(*args, **kwargs)
            except Exception as e:
                logging.warning(f"Excepción en BD worker [{func.__name__}]: {e}")
            finally:
                self.db_queue.task_done()

    def _tick_horas(self):
        self._horas_acum += 1.0

    def _tab_visible(self, idx):
        return self.tabs.currentIndex() == idx

    # ──────────────────────────────────────────────────────────────────────────
    # LÓGICA DE NEGOCIO
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def analizar_salud_motor(carga, vib, impacto):
        if vib > 20 and carga < 50:
            return "DESBALANCE MECANICO"
        if vib > 20 and carga > 90:
            return "SOBRECARGA MECANICA"
        if vib < 10 and carga > 90:
            return "FRICCION INTERNA"
        if impacto > 0.5:
            return "IMPACTOS EN RODAMIENTO"
        return "OPERACION NOMINAL"

    def _calcular_pct(self, c_proc, rpm_val):
        if not self._placa_cargada:
            return 0.0, ("SIN PLACA", "#556688")

        p = self._placa
        i_rango = max(p['i_nom'] - p['i_vacio'], 1.0)
        carga_elec = (c_proc - p['i_vacio']) / i_rango
        caida_rpm = max(0.0, 1.0 - (rpm_val / max(p['rpm_nom'], 1.0)))
        carga_rpm = caida_rpm / 0.15
        carga_total = float(np.clip(0.85 * carga_elec + 0.15 * carga_rpm, 0.0, 1.5))

        if carga_total < 0.25:
            estado = ("VACÍO", "#556688")
        elif carga_total < 0.60:
            estado = ("LIGERA", "#00FFCC")
        elif carga_total < 0.85:
            estado = ("NOMINAL", "#39FF14")
        elif carga_total < 1.10:
            estado = ("PLENA", "#FFD700")
        elif carga_total < 1.30:
            estado = ("SOBRECARGA", "#FF8C00")
        else:
            estado = ("BLOQUEO", "#FF3333")
        return round(carga_total * 100, 1), estado

    def _rul_tiempo_real_str(self):
        h = self._rul_horas
        if h is None:
            return "RUL: calculando..."
        if h > 8760:
            return f"RUL: {h / 8760:.1f} años"
        if h > 720:
            return f"RUL: {h / 720:.1f} meses"
        if h > 24:
            return f"RUL: {h / 24:.0f} días"
        return f"RUL: {h:.0f} h — CRÍTICO"

    def _motor_en_regimen_estable(self, c, v, r, imp):
        if not self._placa_cargada:
            return False
        p = self._placa
        return (r > p['rpm_nom'] * 0.85 and c < p['i_nom'] * 1.5
                and v < p['vib_nom'] * 2.0 and imp < p['vib_nom'] * 0.25)

    def _toggle_actuador(self, checked):
        self.btn_actuador.setText("CARGA EXTERNA: ON" if checked else "CARGA EXTERNA: OFF")
        if self.hilo_trabajo is not None:
            self.hilo_trabajo.cambiar_actuador(1 if checked else 0)

    def _toggle_motor_remoto(self, checked):
        if self.hilo_trabajo is None:
            return
        if checked:
            self.hilo_trabajo.cambiar_actuador(1)
            self.btn_parada.setText("ENCENDER MOTOR")
            self._set_estado("MOTOR APAGADO DESDE HMI", '#FF3333', bg='#330000', border='red')
        else:
            self.hilo_trabajo.cambiar_actuador(0)
            self.btn_parada.setText("APAGAR MOTOR")
            self._set_estado("MOTOR REANUDADO", '#00FFCC')

    def _limpiar_graficas(self):
        self.buffer_vib_largo.clear()
        self.matriz_fft.fill(0)
        for arr in (self.hist_v_p1, self.hist_c_p1, self.hist_iir_p1, self.hist_v_p2, self.hist_c_p2):
            arr.fill(0)
        self.hist_etime = [0.0] * self.MAX_DATOS
        self.hist_efreq = [0.0] * self.MAX_DATOS
        self.fft_bins = np.zeros(32)
        self.filtro_iir.reset()
        self._set_estado("BÚFERES LIMPIADOS", '#FFD700')

    def _set_estado(self, txt, color='#00FFCC', bg=C_BG, border=None):
        self.label_tel.setText(txt)
        self.label_tel.setStyleSheet(
            f"background:{bg};color:{color};border:1px solid {border or color};border-radius:4px;padding:4px;")

    # ──────────────────────────────────────────────────────────────────────────
    # CONSTRUCCIÓN DE LA INTERFAZ
    # ──────────────────────────────────────────────────────────────────────────
    def _construir_interfaz(self):
        central = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(5)
        layout.setContentsMargins(8, 8, 8, 8)

        # --- Panel de control ---
        grp = QGroupBox("Panel de Control del Nodo")
        row = QHBoxLayout()
        row.setSpacing(8)

        self.input_puerto = QLineEdit("/dev/ttyUSB0")
        self.input_puerto.setFixedWidth(120)
        self.input_puerto.setStyleSheet(INPUT_STYLE)

        self.btn_actuador = QPushButton("CARGA EXTERNA: OFF")
        self.btn_actuador.setCheckable(True)
        self.btn_actuador.setStyleSheet(
            "QPushButton{background:#1E1414;color:#AA8888;border:1px solid #443333;border-radius:4px;padding:6px;font-weight:bold;}"
            "QPushButton:checked{background:#003D14;color:#00FFCC;border:1px solid #00AA33;}")
        self.btn_actuador.toggled.connect(self._toggle_actuador)

        self.input_rpm = QLineEdit("2200")
        self.input_rpm.setPlaceholderText("RPM 10–10000")
        self.input_rpm.setFixedWidth(100)

        self.input_delgas = QLineEdit("3")
        self.input_delgas.setPlaceholderText("Delgas")
        self.input_delgas.setFixedWidth(50)
        self.input_delgas.setStyleSheet(INPUT_STYLE)

        self.input_reductora = QLineEdit("0")
        self.input_reductora.setPlaceholderText("Caja 1:X")
        self.input_reductora.setFixedWidth(60)
        self.input_reductora.setStyleSheet(INPUT_STYLE.replace("#FFD700", "#00FFCC"))

        self.btn_iniciar = QPushButton("Iniciar")
        self.btn_iniciar.setStyleSheet(
            "QPushButton{background:#003D14;color:#00FF66;border:1px solid #00AA33;border-radius:5px;font-weight:bold;padding:6px 14px;}"
            "QPushButton:hover{background:#005020;}"
            "QPushButton:disabled{background:#141E14;color:#334433;border-color:#223322;}")

        self.btn_detener = QPushButton("Detener")
        self.btn_detener.setStyleSheet(
            "QPushButton{background:#3D0000;color:#FF4444;border:1px solid #AA2222;border-radius:5px;font-weight:bold;padding:6px 14px;}"
            "QPushButton:hover{background:#550000;}"
            "QPushButton:disabled{background:#1E1414;color:#443333;border-color:#332222;}")
        self.btn_detener.setEnabled(False)

        self.btn_parada = QPushButton("APAGAR MOTOR")
        self.btn_parada.setCheckable(True)
        self.btn_parada.setStyleSheet(
            "QPushButton{background:#880000;color:#FFFFFF;border:1px solid #FF0000;border-radius:5px;font-weight:bold;padding:6px 14px;}"
            "QPushButton:checked{background:#003D14;color:#00FFCC;border:1px solid #00AA33;}"
            "QPushButton:checked:hover{background:#005020;}")
        self.btn_parada.toggled.connect(self._toggle_motor_remoto)

        self.btn_limpiar = QPushButton("Limpiar búfer")
        self.btn_limpiar.setStyleSheet(
            "QPushButton{background:#332200;color:#FFD700;border:1px solid #AA8800;border-radius:5px;font-weight:bold;padding:6px 14px;}"
            "QPushButton:hover{background:#553300;}")
        self.btn_limpiar.clicked.connect(self._limpiar_graficas)

        for lbl, w in [("Puerto:", self.input_puerto), ("Actuador:", self.btn_actuador),
                       ("Setpoint RPM:", self.input_rpm), ("Delgas:", self.input_delgas),
                       ("Reductora 1:X:", self.input_reductora)]:
            label = QLabel(lbl)
            label.setStyleSheet("color:#AACCFF; font-size:9pt;")
            row.addWidget(label)
            row.addWidget(w)
        row.addStretch()
        for b in (self.btn_iniciar, self.btn_detener, self.btn_parada, self.btn_limpiar):
            row.addWidget(b)
        grp.setLayout(row)
        layout.addWidget(grp)

        # --- Telemetría principal ---
        tel_row = QHBoxLayout()
        tel_row.setSpacing(8)
        self.disp_vib = DisplayDigital("Vibración", "u.a", C_VIB, 0, self.LIM_VIB * 1.5)
        self.disp_corr = DisplayDigital("Corriente", "mA", C_CORR, 0, self.LIM_CORR * 1.5)
        self.disp_rpm = DisplayDigital("RPM Motor", "rpm", "#AA88FF", 0, 10000)
        self.disp_carga = DisplayDigital("Carga", "%", "#556688", 0, 150)

        self.label_tel = QLabel("ESTADO: INACTIVO")
        self.label_tel.setAlignment(Qt.AlignCenter)
        self.label_tel.setFont(QFont("Consolas", 11, QFont.Bold))
        self.label_tel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.label_tel.setStyleSheet(
            f"background:{C_BG};color:#556688;border:1px solid #556688;border-radius:4px;padding:4px;")

        for w in (self.disp_vib, self.disp_corr, self.disp_rpm, self.disp_carga):
            tel_row.addWidget(w)
        tel_row.addWidget(self.label_tel, stretch=2)
        layout.addLayout(tel_row)

        # --- Pestañas ---
        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        t1 = QWidget()
        l1 = QVBoxLayout()
        l1.setContentsMargins(0, 0, 0, 0)
        self._crear_tab_procesado_pg(l1)
        t1.setLayout(l1)

        t2 = QWidget()
        l2 = QVBoxLayout()
        l2.setContentsMargins(0, 0, 0, 0)
        self._crear_tab_crudo_pg(l2)
        t2.setLayout(l2)

        self.tabs.addTab(t1, "Análisis Predictivo")                    # 0
        self.tabs.addTab(t2, "Telemetría Cruda")                       # 1
        self.tabs.addTab(self._crear_tab_senal(), "Procesamiento de Señal")  # 2 (TAB_FILTROS)
        self.tabs.addTab(self._crear_tab_rul(), "Vida Útil (RUL)")     # 3 (TAB_RUL)
        self.tabs.addTab(self._crear_tab_historico(), "Historial y Tendencia")
        self.tabs.addTab(self._crear_tab_avanzado(), "Diagnóstico Avanzado")
        self.tabs.addTab(self._crear_tab_respuesta_filtro(), "Respuesta del Filtro IIR")

        layout.addWidget(self.tabs, stretch=1)
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.btn_iniciar.clicked.connect(self._iniciar)
        self.btn_detener.clicked.connect(self._detener)

    def _crear_tab_procesado_pg(self, layout):
        self.panel_datos_t1 = PanelDatosVivo([
            ('ml_status', 'ESTADO ML', '', '#00FFCC'),
            ('v_proc', 'VIB EMA', 'u.a', C_VIB),
            ('v_iir', 'VIB IIR', 'u.a', C_IIR),
            ('c_proc', 'CORR EMA', 'mA', C_CORR),
            ('rpm', 'RPM EJE', 'rpm', '#AA88FF'),
            ('bin_dom', 'BIN DOM', '', '#FF88AA'),
        ])
        layout.addWidget(self.panel_datos_t1)

        self.glw_p1 = pg.GraphicsLayoutWidget()
        layout.addWidget(self.glw_p1)

        self.p1_v = self.glw_p1.addPlot(title="Vibración (EMA e IIR) y Corriente")
        self.p1_v.setLabel('left', "Vibración [u.a.]", color=C_VIB)
        self.p1_v.showGrid(x=True, y=True, alpha=0.3)
        self.p1_v.setXRange(0, self.MAX_DATOS)
        self.p1_v.setYRange(0, self.LIM_VIB * 1.6)
        _bloquear_grafica_pg(self.p1_v)

        self.p1_c = pg.ViewBox()
        self.p1_v.showAxis('right')
        self.p1_v.scene().addItem(self.p1_c)
        self.p1_v.getAxis('right').linkToView(self.p1_c)
        self.p1_c.setXLink(self.p1_v)
        self.p1_v.getAxis('right').setLabel('Corriente [mA]', color=C_CORR)
        self.p1_c.setYRange(0, self.LIM_CORR * 1.6)
        _bloquear_grafica_pg(self.p1_c)

        def sync_views():
            self.p1_c.setGeometry(self.p1_v.vb.sceneBoundingRect())
            self.p1_c.linkedViewChanged(self.p1_v.vb, self.p1_c.XAxis)

        sync_views()
        QTimer.singleShot(0, sync_views)
        self.p1_v.vb.sigResized.connect(sync_views)

        def con_alpha(color, a):
            c = QColor(color)
            c.setAlpha(a)
            return pg.mkBrush(c)

        self.line_v1 = self.p1_v.plot(pen=pg.mkPen(C_VIB, width=2), fillLevel=0,
                                      brush=con_alpha(C_VIB, 50), name="Vibración EMA")
        self.line_v1_iir = self.p1_v.plot(pen=pg.mkPen(C_IIR, width=2, style=Qt.DashLine), fillLevel=0,
                                          brush=con_alpha(C_IIR, 30), name="Vibración IIR")
        self.line_c1 = pg.PlotDataItem(pen=pg.mkPen(C_CORR, width=2), fillLevel=0,
                                       brush=con_alpha(C_CORR, 30), name="Corriente")
        self.p1_c.addItem(self.line_c1)

        self.p1_v.addItem(pg.InfiniteLine(pos=self.LIM_VIB, angle=0,
                                          pen=pg.mkPen(C_ALARM, width=1, style=Qt.DashLine)))
        self.p1_c.addItem(pg.InfiniteLine(pos=self.LIM_CORR, angle=0,
                                          pen=pg.mkPen(C_ALARM, width=1, style=Qt.DashLine)))
        self.line_ref = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(C_REF, width=1.5, style=Qt.DotLine))
        self.p1_c.addItem(self.line_ref)

        self.glw_p1.nextRow()

        self.p1_fft = self.glw_p1.addPlot(title="Espectrograma en Cascada (FFT)")
        self.p1_fft.setLabel('left', "Bin de frecuencia", color='#8899BB')
        self.p1_fft.setLabel('bottom', "Muestras", color=C_TEXT_DIM)
        _bloquear_grafica_pg(self.p1_fft)
        self.img_fft = pg.ImageItem()
        self.p1_fft.addItem(self.img_fft)
        self.img_fft.setColorMap(pg.colormap.get('plasma'))
        self.img_fft.setRect(QRectF(0, 0, self.MAX_DATOS, 32))

    def _crear_tab_crudo_pg(self, layout):
        self.panel_datos_t2 = PanelDatosVivo([
            ('v_crud', 'VIB CRUDA', 'u.a', C_VIB_RAW),
            ('c_crud', 'CORR CRUDA', 'mA', C_CORR_RAW),
            ('rpm', 'RPM EJE', 'rpm', '#AA88FF'),
            ('bin_dom', 'BIN DOM', '', '#FF88AA'),
            ('alertas', 'ALERTAS', '', C_ALARM),
        ])
        layout.addWidget(self.panel_datos_t2)

        self.glw_p2 = pg.GraphicsLayoutWidget()
        layout.addWidget(self.glw_p2)

        self.p2_v = self.glw_p2.addPlot(title="Valores Pico sin Filtrar")
        self.p2_v.setLabel('left', "Vibración pico [u.a.]", color=C_VIB_RAW)
        self.p2_v.showGrid(x=True, y=True, alpha=0.3)
        self.p2_v.setXRange(0, self.MAX_DATOS)
        self.p2_v.setYRange(0, self.LIM_VIB * 1.8)
        _bloquear_grafica_pg(self.p2_v)

        self.p2_c = pg.ViewBox()
        self.p2_v.showAxis('right')
        self.p2_v.scene().addItem(self.p2_c)
        self.p2_v.getAxis('right').linkToView(self.p2_c)
        self.p2_c.setXLink(self.p2_v)
        self.p2_v.getAxis('right').setLabel('Corriente pico [mA]', color=C_CORR_RAW)
        self.p2_c.setYRange(0, self.LIM_CORR * 1.8)
        _bloquear_grafica_pg(self.p2_c)

        def sync_views():
            self.p2_c.setGeometry(self.p2_v.vb.sceneBoundingRect())
            self.p2_c.linkedViewChanged(self.p2_v.vb, self.p2_c.XAxis)

        sync_views()
        QTimer.singleShot(0, sync_views)
        self.p2_v.vb.sigResized.connect(sync_views)

        self.line_v2 = self.p2_v.plot(pen=pg.mkPen(C_VIB_RAW, width=1.5), symbol='o',
                                      symbolSize=4, symbolBrush=C_VIB_RAW)
        self.line_c2 = pg.PlotDataItem(pen=pg.mkPen(C_CORR_RAW, width=1.5), symbol='o',
                                       symbolSize=4, symbolBrush=C_CORR_RAW)
        self.p2_c.addItem(self.line_c2)
        self.p2_v.addItem(pg.InfiniteLine(pos=self.LIM_VIB * 1.2, angle=0,
                                          pen=pg.mkPen(C_ALARM, width=1, style=Qt.DashLine)))

    # --- Procesamiento de señal (subpestañas) ---
    def _crear_tab_senal(self):
        contenedor = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        self.sub_tabs = QTabWidget()
        self.sub_tabs.setStyleSheet(
            "QTabBar::tab{background:#0A1020;padding:5px 10px;color:#557799;font-size:8pt;}"
            "QTabBar::tab:selected{background:#0F1830;color:#FFD700;font-weight:bold;}")
        self.sub_tabs.addTab(self._tab_ema(), "EMA vs IIR")
        self.sub_tabs.addTab(self._tab_rms(), "True RMS")
        self.sub_tabs.addTab(self._tab_fft(), "FFT")
        self.sub_tabs.addTab(self._tab_rpm(), "RPM")
        self.sub_tabs.addTab(self._tab_parseval(), "Validación Parseval")
        layout.addWidget(self.sub_tabs)
        contenedor.setLayout(layout)
        return contenedor

    def _tab_ema(self):
        w = QWidget()
        main = QVBoxLayout()
        main.setSpacing(6)
        self.panel_datos_ema = PanelDatosVivo([
            ('v_crud', 'VIB CRUDA', 'u.a', C_RAW_DIM), ('v_proc', 'VIB EMA', 'u.a', C_EMA),
            ('v_iir', 'VIB IIR', 'u.a', C_IIR), ('rpm', 'RPM', 'rpm', '#AA88FF')])
        main.addWidget(self.panel_datos_ema)

        alpha_row = QHBoxLayout()
        alpha_row.addWidget(QLabel("Coeficiente α del EMA (solo visualización):"))
        self.slider_alpha = QSlider(Qt.Horizontal)
        self.slider_alpha.setRange(1, 99)
        self.slider_alpha.setValue(int(self.alpha_didact * 100))
        self.lbl_alpha = QLabel(f"{self.alpha_didact:.2f}  (>> {self._alpha_to_shift(self.alpha_didact)})")
        self.lbl_alpha.setStyleSheet("color:#FFD700;font-family:Consolas;min-width:200px;")
        self.slider_alpha.valueChanged.connect(self._on_alpha_change)
        alpha_row.addWidget(self.slider_alpha, stretch=1)
        alpha_row.addWidget(self.lbl_alpha)
        main.addLayout(alpha_row)

        self.fig_ema, self.cv_ema = _nueva_figura()
        self.ax_ema = self.fig_ema.add_subplot(111)
        _estilizar_ax(self.ax_ema, "Señal cruda, EMA e IIR Biquad", "Amplitud", C_EMA)
        self.line_raw_ema, = self.ax_ema.plot([], [], color=C_RAW_DIM, lw=0.9, alpha=0.7, label='Señal cruda')
        self.line_ema_out, = self.ax_ema.plot([], [], color=C_EMA, lw=2.0, label='EMA')
        self.line_iir_out, = self.ax_ema.plot([], [], color=C_IIR, lw=2.0, ls='--', label='IIR Biquad')
        self.ax_ema.set_xlim(0, self.MAX_DATOS - 1)
        self.ax_ema.set_ylim(0, self.LIM_VIB * 2)
        _leyenda(self.ax_ema, loc='upper left')
        main.addWidget(self.cv_ema)
        w.setLayout(main)
        return w

    @staticmethod
    def _alpha_to_shift(alpha):
        if alpha <= 0:
            return "∞"
        return str(int(max(0, round(-np.log2(alpha)))))

    def _on_alpha_change(self, val):
        self.alpha_didact = val / 100.0
        self.ema_sim = 0.0
        self.lbl_alpha.setText(f"{self.alpha_didact:.2f}  (>> {self._alpha_to_shift(self.alpha_didact)})")

    def _tab_rms(self):
        w = QWidget()
        main = QVBoxLayout()
        main.setSpacing(6)
        self.panel_datos_rms = PanelDatosVivo([
            ('c_proc', 'TRUE RMS', 'mA', C_CORR), ('c_crud', 'PICO CRUDO', 'mA', C_CORR_RAW),
            ('rpm', 'RPM', 'rpm', '#AA88FF')])
        main.addWidget(self.panel_datos_rms)

        self.fig_rms, self.cv_rms = _nueva_figura(wspace=0.35)
        gs = gridspec.GridSpec(1, 2, figure=self.fig_rms, wspace=0.35)

        self.ax_rms_hist = self.fig_rms.add_subplot(gs[0])
        _estilizar_ax(self.ax_rms_hist, "Historial de corriente (True RMS)", "mA (RMS)", C_CORR)
        self.line_rms_hist, = self.ax_rms_hist.plot([], [], color=C_CORR, lw=1.5, label='True RMS')
        self.line_rms_peak, = self.ax_rms_hist.plot([], [], color=C_CORR_RAW, lw=1.0, ls='--', alpha=0.7,
                                                    label='Pico crudo')
        self.ax_rms_hist.set_xlim(0, self.MAX_DATOS - 1)
        self.ax_rms_hist.set_ylim(0, self.LIM_CORR * 1.5)
        _leyenda(self.ax_rms_hist)

        self.ax_rms_bar = self.fig_rms.add_subplot(gs[1])
        self.ax_rms_bar.set_facecolor(C_AX_BG)
        self.ax_rms_bar.set_title("RMS vs pico actual", color='#AACCFF', fontsize=9, fontweight='bold')
        self.ax_rms_bar.tick_params(colors='#6688AA', labelsize=8)
        for s in self.ax_rms_bar.spines.values():
            s.set_edgecolor(C_BORDER)
        self.bars_rms = self.ax_rms_bar.bar(['True RMS', 'Pico crudo'], [0, 0],
                                            color=[C_CORR, C_CORR_RAW], width=0.5, edgecolor=C_BORDER)
        self.ax_rms_bar.set_ylim(0, self.LIM_CORR * 1.5)
        self.ax_rms_bar.set_ylabel("mA", color='#8899BB', fontsize=8)
        self.txt_rms_factor = self.ax_rms_bar.text(0.5, 0.95, "Factor de cresta: —",
                                                   transform=self.ax_rms_bar.transAxes, ha='center',
                                                   color='#FFD700', fontsize=8)
        main.addWidget(self.cv_rms)
        w.setLayout(main)
        return w

    def _tab_fft(self):
        w = QWidget()
        main = QVBoxLayout()
        main.setSpacing(6)
        self.panel_datos_fft = PanelDatosVivo([
            ('v_proc', 'VIB EMA', 'u.a', C_VIB), ('bin_dom', 'BIN DOM', '', '#FF88AA'),
            ('rpm', 'RPM', 'rpm', '#AA88FF')])
        main.addWidget(self.panel_datos_fft)

        self.fig_fft_d, self.cv_fft_d = _nueva_figura(wspace=0.35)
        gs = gridspec.GridSpec(1, 2, figure=self.fig_fft_d, wspace=0.35)

        self.ax_fft_bars = self.fig_fft_d.add_subplot(gs[0])
        _estilizar_ax(self.ax_fft_bars, "Espectro de amplitud (dsPIC)", "Amplitud", '#AA88FF',
                      xlabel="Bin de frecuencia")
        self.bar_fft = self.ax_fft_bars.bar(range(32), self.fft_bins, color='#AA88FF',
                                            edgecolor='none', width=0.8)
        self.ax_fft_bars.set_xlim(-0.5, 31.5)
        self.ax_fft_bars.set_ylim(0, self.LIM_VIB)

        self.ax_fft_cascade = self.fig_fft_d.add_subplot(gs[1])
        self.ax_fft_cascade.set_facecolor('black')
        self.ax_fft_cascade.set_title("Cascada FFT histórica", color='#AACCFF', fontsize=9, fontweight='bold')
        self.ax_fft_cascade.tick_params(colors='#6688AA', labelsize=7)
        for s in self.ax_fft_cascade.spines.values():
            s.set_edgecolor(C_BORDER)
        self.img_fft_d = self.ax_fft_cascade.imshow(
            self.matriz_fft, aspect='auto', cmap='inferno', origin='lower', vmin=0,
            vmax=self.LIM_VIB / 2, extent=[0, self.MAX_DATOS, 0, 31], interpolation='bilinear')
        main.addWidget(self.cv_fft_d)
        w.setLayout(main)
        return w

    def _tab_rpm(self):
        w = QWidget()
        main = QVBoxLayout()
        main.setSpacing(6)
        self.panel_datos_rpm = PanelDatosVivo([
            ('rpm', 'RPM MEDIDA', 'rpm', '#AA88FF'), ('rpm_obj', 'RPM OBJETIVO', 'rpm', '#FFD700')])
        main.addWidget(self.panel_datos_rpm)

        self.fig_rpm, self.cv_rpm = _nueva_figura()
        self.ax_rpm_hist = self.fig_rpm.add_subplot(111)
        _estilizar_ax(self.ax_rpm_hist, "RPM medidas vs RPM objetivo", "RPM", '#AA88FF')
        self.line_rpm_med, = self.ax_rpm_hist.plot([], [], color='#AA88FF', lw=1.5, label='RPM medidas')
        self.line_rpm_obj, = self.ax_rpm_hist.plot([], [], color='#FFD700', lw=1.0, ls='--', alpha=0.8,
                                                   label='RPM objetivo')
        self.ax_rpm_hist.set_xlim(0, self.MAX_DATOS - 1)
        self.ax_rpm_hist.set_ylim(0, 12000)
        _leyenda(self.ax_rpm_hist)
        main.addWidget(self.cv_rpm)
        w.setLayout(main)
        return w

    def _tab_parseval(self):
        w = QWidget()
        main = QVBoxLayout()
        main.setSpacing(6)
        self.panel_datos_pars = PanelDatosVivo([
            ('e_time', 'ENERGÍA TIEMPO', 'u²', C_VIB), ('e_freq', 'ENERGÍA FFT', 'u²', '#AA88FF'),
            ('err_pars', 'DESVIACIÓN', '%', C_ALARM)])
        main.addWidget(self.panel_datos_pars)

        self.fig_pars, self.cv_pars = _nueva_figura()
        self.ax_pars = self.fig_pars.add_subplot(111)
        _estilizar_ax(self.ax_pars, "Conservación de energía: tiempo vs frecuencia", "Energía [u²]", '#39FF14')
        self.line_etime, = self.ax_pars.plot([], [], color=C_VIB, lw=2.5, alpha=0.9, label='Σ x[n]²')
        self.line_efreq, = self.ax_pars.plot([], [], color='#AA88FF', lw=1.5, ls='--', label='(1/N) Σ |X[k]|²')
        self.ax_pars.set_xlim(0, self.MAX_DATOS - 1)
        _leyenda(self.ax_pars)
        main.addWidget(self.cv_pars)

        self.tabla_parseval = QTableWidget(0, 4)
        self.tabla_parseval.setHorizontalHeaderLabels(
            ["Hora", "Energía tiempo (Σx²)", "Energía frec. (1/N ΣX²)", "Desviación (%)"])
        self.tabla_parseval.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabla_parseval.verticalHeader().setVisible(False)
        self.tabla_parseval.setFixedHeight(120)
        self.tabla_parseval.setStyleSheet(
            "QTableWidget{background-color:#0D1525;color:#C8D8F0;gridline-color:#1A3060;border:1px solid #1A3060;font-family:Consolas;font-size:9pt;}"
            "QHeaderView::section{background-color:#0F1830;color:#AACCFF;padding:4px;border:1px solid #1A3060;font-weight:bold;}")
        main.addWidget(self.tabla_parseval)
        w.setLayout(main)
        return w

    # --- Respuesta del filtro IIR ---
    def _crear_tab_respuesta_filtro(self):
        w = QWidget()
        main = QVBoxLayout()
        main.setContentsMargins(8, 8, 8, 8)

        b, a = list(IIR_B), list(IIR_A)
        w_norm, h_resp = freqz(b, a, worN=1024)
        f_norm = w_norm / np.pi
        mag_db = 20 * np.log10(np.maximum(np.abs(h_resp), 1e-12))
        fase_deg = np.unwrap(np.angle(h_resp)) * 180 / np.pi

        fig, cv = _nueva_figura(left=0.09, right=0.95, top=0.90, bottom=0.12)
        gs = gridspec.GridSpec(2, 2, figure=fig, wspace=0.30, hspace=0.45)

        ax_mag = fig.add_subplot(gs[0, 0])
        _estilizar_ax(ax_mag, "Respuesta en magnitud", "dB", C_VIB, xlabel="Frecuencia normalizada (× Fs/2)")
        ax_mag.plot(f_norm, mag_db, color=C_VIB, lw=2.0)
        ax_mag.axhline(-3, color='#FFD700', ls=':', lw=1.0, alpha=0.8, label='−3 dB')
        _leyenda(ax_mag)

        ax_fase = fig.add_subplot(gs[0, 1])
        _estilizar_ax(ax_fase, "Respuesta en fase", "Grados", '#AA88FF', xlabel="Frecuencia normalizada (× Fs/2)")
        ax_fase.plot(f_norm, fase_deg, color='#AA88FF', lw=2.0)

        ax_pz = fig.add_subplot(gs[1, 0])
        ax_pz.set_facecolor(C_AX_BG)
        ax_pz.set_title("Polos y ceros (plano Z)", color='#AACCFF', fontsize=9, fontweight='bold')
        ax_pz.tick_params(colors='#6688AA', labelsize=7)
        for s in ax_pz.spines.values():
            s.set_edgecolor(C_BORDER)
        ax_pz.grid(True, color=C_GRID, linestyle='--', linewidth=0.5, alpha=0.6)
        th = np.linspace(0, 2 * np.pi, 200)
        ax_pz.plot(np.cos(th), np.sin(th), color='#556688', lw=1.0, ls='--')
        ceros, polos = np.roots(b), np.roots(a)
        ax_pz.scatter(ceros.real, ceros.imag, marker='o', s=90, facecolors='none',
                      edgecolors=C_VIB, linewidths=2, label='Ceros', zorder=3)
        ax_pz.scatter(polos.real, polos.imag, marker='x', s=90, color=C_ALARM,
                      linewidths=2, label='Polos', zorder=3)
        ax_pz.set_xlim(-1.5, 1.5)
        ax_pz.set_ylim(-1.5, 1.5)
        ax_pz.set_aspect('equal')
        _leyenda(ax_pz, loc='upper right')

        ax_txt = fig.add_subplot(gs[1, 1])
        ax_txt.axis('off')
        radios = np.abs(polos)
        estable = bool(np.all(radios < 1.0))
        resumen = (
            "H(z) = (b0 + b1·z⁻¹ + b2·z⁻²) / (1 + a1·z⁻¹ + a2·z⁻²)\n\n"
            f"b  = {b[0]:.6f}, {b[1]:.6f}, {b[2]:.6f}\n"
            f"a1 = {a[1]:.6f}   a2 = {a[2]:.6f}\n\n"
            f"|Polo| máx: {np.max(radios):.4f}\n"
            f"Estabilidad: {'ESTABLE' if estable else 'INESTABLE'}"
        )
        ax_txt.text(0.02, 0.95, resumen, transform=ax_txt.transAxes, va='top', ha='left',
                    color=C_TEXT, fontsize=8.5, family='monospace',
                    bbox=dict(boxstyle='round', facecolor='#0D1525',
                              edgecolor='#39FF14' if estable else C_ALARM, linewidth=1.5))
        main.addWidget(cv)
        w.setLayout(main)
        return w

    # --- Diagnóstico avanzado ---
    def _crear_tab_avanzado(self):
        contenedor = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)

        self.glw_avanzado = pg.GraphicsLayoutWidget()
        layout.addWidget(self.glw_avanzado, stretch=3)

        self.p_env = self.glw_avanzado.addPlot(title="Análisis de envolvente — detección de impactos")
        self.p_env.setLabel('left', "Amplitud [u.a.]", color='#FFD700')
        self.p_env.showGrid(x=True, y=True, alpha=0.3)
        self.p_env.setXRange(0, self.MAX_DATOS)
        self.p_env.setYRange(0, self.LIM_VIB * 2)
        _bloquear_grafica_pg(self.p_env)
        brush_env = QColor('#FFD700')
        brush_env.setAlpha(30)
        self.line_envolvente = self.p_env.plot(pen=pg.mkPen('#FFD700', width=2), fillLevel=0,
                                               brush=pg.mkBrush(brush_env))

        self.glw_avanzado.nextRow()

        self.p_ceps = self.glw_avanzado.addPlot(title="Análisis cepstral — periodicidad de impactos")
        self.p_ceps.setLabel('left', "Amplitud", color='#00FFCC')
        self.p_ceps.setLabel('bottom', "Quefrency", color=C_TEXT_DIM)
        self.p_ceps.showGrid(x=True, y=True, alpha=0.3)
        self.p_ceps.setXRange(0, self.MAX_DATOS_LARGO // 2)
        _bloquear_grafica_pg(self.p_ceps)
        self.line_cepstrum = self.p_ceps.plot(pen=pg.mkPen('#00FFCC', width=2), fillLevel=0,
                                              brush=pg.mkBrush(QColor(0, 255, 204, 30)))

        grp = QGroupBox("Informe de diagnóstico en tiempo real")
        grp.setStyleSheet(GRP_STYLE)
        lay = QVBoxLayout()
        self.txt_analisis = QTextEdit()
        self.txt_analisis.setReadOnly(True)
        self.txt_analisis.setStyleSheet(
            f"background:#050811; color:{C_TEXT}; font-family:Consolas; font-size:10pt; border:none; padding:5px;")
        self.txt_analisis.setMaximumHeight(160)
        lay.addWidget(self.txt_analisis)
        grp.setLayout(lay)
        layout.addWidget(grp, stretch=1)
        contenedor.setLayout(layout)
        return contenedor

    def _actualizar_reporte_texto(self, carga, vib, impacto, health, conf, diag):
        if health >= 80:
            estado = "<span style='color:#00FFCC;'>ÓPTIMO</span>"
            sugerencia = "Continuar con el monitoreo nominal."
        elif health >= 50:
            estado = "<span style='color:#FFD700;'>CON ADVERTENCIAS</span>"
            sugerencia = "Revisar posibles desajustes mecánicos en el siguiente mantenimiento preventivo."
        else:
            estado = "<span style='color:#FF3333;'>CRÍTICO</span>"
            sugerencia = ("<b style='color:#FF3333;'>Detener el equipo e inspeccionar de inmediato: "
                          "riesgo de falla catastrófica.</b>")

        if impacto > 0.5:
            ceps = ("Se detectan picos periódicos en la envolvente y el cepstrum, compatibles con "
                    "<span style='color:#FF3333;'>impactos repetitivos (posible falla en pistas o elementos rodantes)</span>.")
        else:
            ceps = ("El cepstrum no muestra periodicidades anómalas: "
                    "<span style='color:#39FF14;'>sin impactos detectados</span>.")

        if conf < 70:
            deriva = ("Existe una <span style='color:#FF8C00;'>deriva operacional elevada</span> "
                      "respecto a la firma de referencia.")
        else:
            deriva = "Las métricas son consistentes con la firma de referencia histórica."

        self.txt_analisis.setHtml(
            f"<b style='color:#AADDFF; font-size:11pt;'>ESTADO DEL ACTIVO</b><br><br>"
            f"• <b>Estado global:</b> régimen {estado} — salud {health:.1f}%.<br>"
            f"• <b>Vibración:</b> a una carga de {carga:.1f}%, vibración RMS de {vib:.1f} u.a. {ceps}<br>"
            f"• <b>Modelo ML:</b> clasificación actual <b style='color:#FFD700;'>{diag}</b>. {deriva}<br><br>"
            f"• <b>Recomendación:</b> {sugerencia}")

    # --- RUL ---
    def _crear_tab_rul(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_STYLE)
        w = QWidget()
        main = QVBoxLayout()
        main.setSpacing(8)
        main.setContentsMargins(8, 8, 8, 8)

        grp_placa = QGroupBox("Datos de placa del motor")
        grp_placa.setStyleSheet(GRP_STYLE)
        grid = QGridLayout()
        grid.setSpacing(8)

        campos = [
            ("RPM nominales:", "rpm_nom", "2200", "rpm", 0, 0),
            ("Corriente en vacío:", "i_vacio", "350", "mA", 0, 3),
            ("Corriente nominal:", "i_nom", "2400", "mA", 1, 0),
            ("Corriente de bloqueo:", "i_bloq", "5600", "mA", 1, 3),
            ("Vibración nominal sana:", "vib_nom", "20", "u.a.", 2, 0),
            ("Vida útil del fabricante:", "vida_nom", "17520", "h", 2, 3),
        ]
        self._inputs_placa = {}
        for texto, key, default, unidad, r, c in campos:
            lbl = QLabel(texto)
            lbl.setStyleSheet(f"color:{C_TEXT};font-size:9pt;")
            inp = QLineEdit(default)
            inp.setFixedWidth(90)
            inp.setStyleSheet(INPUT_STYLE)
            lbl_u = QLabel(unidad)
            lbl_u.setStyleSheet(f"color:{C_TEXT_DIM};font-size:8pt;min-width:28px;")
            grid.addWidget(lbl, r, c)
            grid.addWidget(inp, r, c + 1)
            grid.addWidget(lbl_u, r, c + 2)
            self._inputs_placa[key] = inp

        lbl_tag = QLabel("Tag / Serial:")
        lbl_tag.setStyleSheet(f"color:{C_TEXT};font-size:9pt;")
        self.input_tag = QLineEdit()
        self.input_tag.setPlaceholderText("Ej: MOT-001")
        self.input_tag.setFixedWidth(90)
        self.input_tag.setStyleSheet(INPUT_STYLE)
        grid.addWidget(lbl_tag, 3, 0)
        grid.addWidget(self.input_tag, 3, 1, 1, 2)

        # [rpm_nom, i_vacio, i_nom, i_bloq, delgas, reductora, vib_nom, vida_nom]
        self.db_motores = {
            "Personalizado...": [0, 0, 0, 0, 0, 0, 0, 0],
            "Pololu 25D 131:1": [76, 150, 650, 2400, 6, 131, 12, 5000],
            "Pololu 37D 70:1": [150, 300, 2500, 5500, 6, 70, 15, 6000],
            "Johnson JS-540 12V": [16000, 800, 4500, 20000, 5, 1, 8, 3000],
            "Mabuchi RS-775": [18000, 1200, 8000, 40000, 5, 1, 10, 2000],
            "Maxon RE-MAX 29": [7200, 100, 1200, 8000, 9, 1, 3, 10000],
            "Bühler DC automotriz": [3000, 500, 3000, 12000, 5, 1, 6, 5000],
            "Motorreductor industrial 24V 50:1": [60, 400, 1800, 7500, 6, 50, 10, 8000],
        }
        lay_db = QHBoxLayout()
        lay_db.addWidget(QLabel("Perfil de motor:"))
        self.combo_motores = QComboBox()
        self.combo_motores.addItems(self.db_motores.keys())
        self.combo_motores.setStyleSheet("background:#0D1525; color:#00FFCC; border:1px solid #1A3060; padding:4px;")
        self.combo_motores.currentTextChanged.connect(self._autocompletar_motor)
        lay_db.addWidget(self.combo_motores, stretch=1)
        grid.addLayout(lay_db, 4, 0, 1, 6)

        self.btn_confirmar_placa = QPushButton("Confirmar placa y activar RUL")
        self.btn_confirmar_placa.setStyleSheet(
            "QPushButton{background:#002244;color:#00BFFF;border:1px solid #0055AA;border-radius:5px;font-weight:bold;padding:6px 18px;font-size:9pt;}"
            "QPushButton:hover{background:#003366;}")
        self.btn_confirmar_placa.clicked.connect(self._confirmar_placa)
        grid.addWidget(self.btn_confirmar_placa, 5, 0, 1, 6)
        grp_placa.setLayout(grid)
        main.addWidget(grp_placa)

        grp_rul = QGroupBox("Pronóstico de vida útil remanente (RUL)")
        grp_rul.setStyleSheet(GRP_STYLE)
        col = QVBoxLayout()
        self.lbl_rul_valor = QLabel("—")
        self.lbl_rul_valor.setFont(QFont("Consolas", 38, QFont.Bold))
        self.lbl_rul_valor.setStyleSheet("color:#00FFCC;background:transparent;")
        self.lbl_rul_unidad = QLabel("horas restantes")
        self.lbl_rul_unidad.setFont(QFont("Consolas", 11))
        self.lbl_rul_unidad.setStyleSheet(f"color:{C_TEXT_DIM};background:transparent;")
        self.lbl_rul_conf = QLabel("Intervalo de confianza: ±—")
        self.lbl_rul_conf.setFont(QFont("Consolas", 9))
        self.lbl_rul_conf.setStyleSheet("color:#FFD700;background:transparent;")
        self.lbl_rul_di = QLabel("DI actual: —  |  Umbral: 6.0σ")
        self.lbl_rul_di.setFont(QFont("Consolas", 9))
        self.lbl_rul_di.setStyleSheet(f"color:{C_TEXT_DIM};background:transparent;")
        self.lbl_rul_estado = QLabel("En espera de datos de placa")
        self.lbl_rul_estado.setFont(QFont("Consolas", 10, QFont.Bold))
        self.lbl_rul_estado.setStyleSheet(
            f"background:{C_PANEL};color:#556688;border:1px solid {C_BORDER};border-radius:4px;padding:4px;")
        for lbl in (self.lbl_rul_valor, self.lbl_rul_unidad, self.lbl_rul_conf,
                    self.lbl_rul_di, self.lbl_rul_estado):
            lbl.setAlignment(Qt.AlignCenter)
            col.addWidget(lbl)
        grp_rul.setLayout(col)
        main.addWidget(grp_rul)

        self.fig_rul, self.cv_rul = _nueva_figura(wspace=0.35)
        gs = gridspec.GridSpec(1, 2, figure=self.fig_rul, wspace=0.35)

        self.ax_di = self.fig_rul.add_subplot(gs[0])
        _estilizar_ax(self.ax_di, "Índice de degradación (DI)", "DI [σ]", '#FF8C00', xlabel="Tiempo [h]")
        self.line_di, = self.ax_di.plot([], [], color='#FF8C00', lw=1.5, label='DI actual')
        self.line_di_ma, = self.ax_di.plot([], [], color='#FFD700', lw=1.0, ls='--', alpha=0.8,
                                           label='Media móvil (20)')
        self.ax_di.axhline(self._di_umbral, color=C_ALARM, ls='--', lw=1.0, alpha=0.8,
                           label=f'Umbral {self._di_umbral}σ')
        self.ax_di.set_xlim(0, 100)
        self.ax_di.set_ylim(0, self._di_umbral * 1.5)
        _leyenda(self.ax_di)

        self.ax_rul_proj = self.fig_rul.add_subplot(gs[1])
        _estilizar_ax(self.ax_rul_proj, "Proyección RUL (degradación exponencial)", "DI [σ]", '#00FFCC',
                      xlabel="Tiempo [h]")
        self.line_rul_hist, = self.ax_rul_proj.plot([], [], color='#00FFCC', lw=1.5, label='DI histórico')
        self.line_rul_pred, = self.ax_rul_proj.plot([], [], color='#FF4488', lw=1.5, ls='--', label='Proyección')
        self.fill_rul_ci = None
        self.ax_rul_proj.axhline(self._di_umbral, color=C_ALARM, ls='--', lw=1.0, alpha=0.8)
        self.ax_rul_proj.set_xlim(0, 200)
        self.ax_rul_proj.set_ylim(0, self._di_umbral * 1.5)
        _leyenda(self.ax_rul_proj)

        main.addWidget(self.cv_rul)
        w.setLayout(main)
        scroll.setWidget(w)
        return scroll

    def _autocompletar_motor(self, nombre):
        datos = self.db_motores.get(nombre)
        if not datos or nombre == "Personalizado...":
            return
        llaves = ['rpm_nom', 'i_vacio', 'i_nom', 'i_bloq', 'delgas', 'reductora', 'vib_nom', 'vida_nom']
        for key, val in zip(llaves, datos):
            if key == 'delgas':
                self.input_delgas.setText(str(val))
            elif key == 'reductora':
                self.input_reductora.setText(str(val))
            else:
                self._inputs_placa[key].setText(str(val))

    def _confirmar_placa(self):
        try:
            p = {}
            for key, inp in self._inputs_placa.items():
                val = float(inp.text().replace(',', '.'))
                if val <= 0:
                    raise ValueError(f"{key} debe ser > 0")
                p[key] = val
            if p['i_vacio'] >= p['i_nom']:
                raise ValueError("La corriente en vacío debe ser menor que la nominal")
            if p['i_nom'] >= p['i_bloq']:
                raise ValueError("La corriente nominal debe ser menor que la de bloqueo")

            tag = self.input_tag.text().strip().upper()
            if not tag:
                raise ValueError("El Tag/Serial es obligatorio")
            self._activo_id = tag
            self._placa = p
            self._placa_cargada = True

            raw = f"{p['rpm_nom']}{p['i_nom']}{p['i_bloq']}{time.time()}"
            self._motor_id = hashlib.md5(raw.encode()).hexdigest()[:8].upper()

            self._calibrando_rul = True
            self._buffer_calibracion.clear()
            self._hist_di.clear()
            self._hist_di_t.clear()
            self._di_acc.clear()

            try:
                perfil = self._db.obtener_perfil_motor(self._activo_id)
                if perfil and 't0_vida' in perfil:
                    self._t0_vida_motor = perfil['t0_vida']
                else:
                    self._t0_vida_motor = time.time()
                    self.db_queue.put((self._db.guardar_perfil_motor,
                                       (self._activo_id, self._t0_vida_motor), {}))
            except AttributeError:
                self._t0_vida_motor = time.time()

            self._rul_activo = True
            self.lbl_rul_estado.setText(
                f"CALIBRANDO ACTIVO {self._activo_id} (requiere {self._MUESTRAS_CALIBRACION} muestras estables)")
            self.lbl_rul_estado.setStyleSheet(
                "background:#1A1000;color:#FFD700;border:1px solid #886600;border-radius:4px;padding:4px;")
            self.btn_confirmar_placa.setText("Reiniciar calibración de línea base")
        except ValueError as e:
            self.lbl_rul_estado.setText(f"Error: {e}")
            self.lbl_rul_estado.setStyleSheet(
                "background:#2A0000;color:#FF4444;border:1px solid red;border-radius:4px;padding:4px;")

    def _calcular_rul(self, c_proc, v_proc, rpm_val):
        if not self._placa_cargada:
            return

        t_actual = time.time()
        if self._t0_vida_motor is None:
            self._t0_vida_motor = t_actual
        t_horas = (t_actual - self._t0_vida_motor) / 3600.0
        x_actual = np.array([c_proc, v_proc, rpm_val, self.impacto_actual])

        # Fase de calibración de la línea base
        if self._calibrando_rul:
            if self._motor_en_regimen_estable(c_proc, v_proc, rpm_val, self.impacto_actual):
                self._buffer_calibracion.append(x_actual)
                if len(self._buffer_calibracion) >= self._MUESTRAS_CALIBRACION:
                    m = np.array(self._buffer_calibracion)
                    self._ref_sano = np.mean(m, axis=0)
                    self._cov_sano = np.cov(m, rowvar=False)
                    self._cov_sano += np.diag(np.diag(self._cov_sano) * 0.01 + 1e-8)
                    self._inv_cov_sano = np.linalg.inv(self._cov_sano)
                    self._calibrando_rul = False
                    self._di_umbral = 6.0
                    self.lbl_rul_estado.setText("LÍNEA BASE ESTABLECIDA — Monitoreo RUL activo")
                    self.lbl_rul_estado.setStyleSheet(
                        "background:#001A00;color:#00FF66;border:1px solid #00AA33;border-radius:4px;padding:4px;")
            return

        if self._ref_sano is None or self._inv_cov_sano is None:
            return

        # Índice de degradación (distancia de Mahalanobis, mediana móvil)
        delta = x_actual - self._ref_sano
        di_raw = float(np.sqrt(max(delta @ self._inv_cov_sano @ delta, 0.0)))
        self._di_acc.append(di_raw)
        di = min(float(np.median(self._di_acc)), self._di_umbral * 1.5)

        self._hist_di.append(di)
        self._hist_di_t.append(t_horas)
        if len(self._hist_di) > self._MAX_HIST_RUL:
            self._hist_di.pop(0)
            self._hist_di_t.pop(0)

        n = len(self._hist_di)
        ventana = min(20, n)
        di_ma = np.convolve(self._hist_di, np.ones(ventana) / ventana, mode='valid')
        visible = self._tab_visible(TAB_RUL)
        b = 0.0

        if n >= 30:
            t_arr = np.array(self._hist_di_t[-len(di_ma):], dtype=float)
            log_di = np.log(np.asarray(di_ma, dtype=float) + 1e-6)
            try:
                b, ln_a = np.polyfit(t_arr, log_di, 1)
            except (np.linalg.LinAlgError, ValueError):
                b, ln_a = 0.0, float(np.mean(log_di))
            a = np.exp(ln_a)
            residuos = log_di - (b * t_arr + ln_a)
            std_res = float(np.std(residuos)) if len(residuos) > 2 else 0.1
            t_hist = np.array(self._hist_di_t, dtype=float)
            t0 = t_hist[0]

            if b > 1e-6:
                t_falla = np.log(self._di_umbral / a) / b
                self._rul_horas = max(0.0, t_falla - t_horas)
                self._rul_std_horas = std_res / b

                horas_proj = min(self._rul_horas * 1.2 + 1.0, 5000.0)
                t_fut = np.linspace(t_horas, t_horas + horas_proj, 50)
                di_fut = a * np.exp(b * t_fut)
                di_u = np.exp(ln_a + std_res) * np.exp(b * t_fut)
                di_l = np.exp(ln_a - std_res) * np.exp(b * t_fut)

                self.line_rul_hist.set_data(t_hist - t0, self._hist_di)
                self.line_rul_pred.set_data(t_fut - t0, di_fut)
                if visible:
                    if self.fill_rul_ci is not None:
                        self.fill_rul_ci.remove()
                    self.fill_rul_ci = self.ax_rul_proj.fill_between(
                        t_fut - t0, di_l, di_u, color='#FF4488', alpha=0.15)
                    self.ax_rul_proj.set_xlim(0, max((t_fut[-1] - t0) * 1.05, 1.0))
                    self.cv_rul.draw_idle()
            else:
                self._rul_horas = max(0.0, self._placa['vida_nom'] - t_horas)
                self._rul_std_horas = self._placa['vida_nom'] * 0.1
                self.line_rul_hist.set_data(t_hist - t0, self._hist_di)
                self.line_rul_pred.set_data([], [])
                if visible:
                    if self.fill_rul_ci is not None:
                        self.fill_rul_ci.remove()
                        self.fill_rul_ci = None
                    self.ax_rul_proj.set_xlim(0, max((t_hist[-1] - t0) * 1.05, 1.0))
                    self.cv_rul.draw_idle()
        else:
            self._rul_horas = self._placa['vida_nom']
            self._rul_std_horas = self._placa['vida_nom'] * 0.1

        # Historial de DI
        t_full = np.array(self._hist_di_t, dtype=float) - self._hist_di_t[0]
        di_full = np.array(self._hist_di, dtype=float)
        self.line_di.set_data(t_full, di_full)
        if len(di_ma) > 0:
            self.line_di_ma.set_data(t_full[len(self._hist_di) - len(di_ma):], di_ma)
        if visible:
            self.ax_di.set_xlim(0, max(float(t_full[-1]) * 1.1, 1.0))
            self.ax_di.set_ylim(0, max(self._di_umbral * 1.5, float(np.max(di_full)) * 1.2))
            self.cv_rul.draw_idle()

        self.lbl_rul_di.setText(
            f"DI actual: {di:.3f}σ  |  Media móvil: {float(di_ma[-1]):.3f}σ  |  Umbral: {self._di_umbral:.1f}σ")

        if self._rul_horas is not None:
            h = self._rul_horas
            ci = self._rul_std_horas or 0.0
            vida_nom = self._placa['vida_nom']

            if n >= 30 and b <= 1e-6:
                color, estado, bg, brd = '#00FFCC', "TENDENCIA ESTABLE — sin degradación detectable", '#001A00', '#00AA33'
            elif h > vida_nom * 0.5:
                color, estado, bg, brd = '#00FFCC', "OPERACIÓN ESTABLE (estimación heurística)", '#001A00', '#00AA33'
            elif h > vida_nom * 0.2:
                color, estado, bg, brd = '#FFD700', "DEGRADACIÓN DETECTADA — inspección recomendada", '#1A1000', '#886600'
            elif h > 0:
                color, estado, bg, brd = '#FF4444', "FALLA INMINENTE — mantenimiento urgente", '#2A0000', 'red'
            else:
                color, estado, bg, brd = '#FF0000', "LÍMITE ESTADÍSTICO SUPERADO", '#330000', 'darkred'

            self.lbl_rul_valor.setText(f"{h:,.1f}")
            self.lbl_rul_valor.setStyleSheet(f"color:{color};background:transparent;")
            self.lbl_rul_conf.setText(
                f"Intervalo de confianza: ±{ci:,.1f} h  |  Aprox.: {int(h // 24)} d {int(h % 24)} h restantes")
            self.lbl_rul_estado.setText(estado)
            self.lbl_rul_estado.setStyleSheet(
                f"background:{bg};color:{color};border:1px solid {brd};border-radius:4px;padding:4px;")

    # --- Historial y tendencia ---
    def _crear_tab_historico(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_STYLE)
        w = QWidget()
        main = QVBoxLayout()
        main.setSpacing(6)
        main.setContentsMargins(6, 6, 6, 6)

        fila1 = QHBoxLayout()
        fila1.setSpacing(8)

        grp_firma = QGroupBox("Firma de arranque")
        grp_firma.setStyleSheet(GRP_STYLE)
        firma_lay = QVBoxLayout()
        self.lbl_firma_sim = QLabel("Similitud: —")
        self.lbl_firma_sim.setFont(QFont("Consolas", 20, QFont.Bold))
        self.lbl_firma_sim.setStyleSheet("color:#00FFCC;background:transparent;")
        self.lbl_firma_sim.setAlignment(Qt.AlignCenter)
        self.lbl_firma_detalle = QLabel("RPM pico: —  |  I pico: — mA  |  T estab.: — s")
        self.lbl_firma_detalle.setFont(QFont("Consolas", 9))
        self.lbl_firma_detalle.setStyleSheet(f"color:{C_TEXT_DIM};background:transparent;")
        self.lbl_firma_detalle.setAlignment(Qt.AlignCenter)
        firma_lay.addWidget(self.lbl_firma_sim)
        firma_lay.addWidget(self.lbl_firma_detalle)

        self.fig_firma = Figure(facecolor=C_BG)
        self.fig_firma.subplots_adjust(left=0.15, right=0.95, top=0.9, bottom=0.15)
        self.cv_firma = FigureCanvas(self.fig_firma)
        self.cv_firma.setFixedHeight(130)
        gs_f = gridspec.GridSpec(2, 1, figure=self.fig_firma, hspace=0.15)
        self.ax_firma_c = self.fig_firma.add_subplot(gs_f[0])
        self.ax_firma_v = self.fig_firma.add_subplot(gs_f[1])
        for ax, col, lbl in [(self.ax_firma_c, C_CORR, 'I [mA]'), (self.ax_firma_v, C_VIB, 'Vib')]:
            ax.set_facecolor(C_AX_BG)
            ax.tick_params(colors='#6688AA', labelsize=6)
            ax.set_ylabel(lbl, color=col, fontsize=7)
            for s in ax.spines.values():
                s.set_edgecolor(C_BORDER)
            ax.grid(True, color=C_GRID, ls='--', lw=0.4, alpha=0.7)
        self.line_firma_c, = self.ax_firma_c.plot([], [], color=C_CORR, lw=1.5)
        self.line_firma_v, = self.ax_firma_v.plot([], [], color=C_VIB, lw=1.5)
        self.line_firma_c_ref, = self.ax_firma_c.plot([], [], color='#FFD700', lw=1.0, ls='--', alpha=0.7)
        self.line_firma_v_ref, = self.ax_firma_v.plot([], [], color='#FFD700', lw=1.0, ls='--', alpha=0.7)
        firma_lay.addWidget(self.cv_firma)
        grp_firma.setLayout(firma_lay)
        fila1.addWidget(grp_firma, stretch=2)

        grp_tend = QGroupBox("Alerta temprana — tendencia entre sesiones")
        grp_tend.setStyleSheet(GRP_STYLE)
        tend_lay = QVBoxLayout()
        self.lbl_tendencia = QLabel("Historial insuficiente (mínimo 5 sesiones)")
        self.lbl_tendencia.setFont(QFont("Consolas", 9, QFont.Bold))
        self.lbl_tendencia.setWordWrap(True)
        self.lbl_tendencia.setStyleSheet(
            f"background:{C_PANEL};color:#556688;border:1px solid {C_BORDER};border-radius:4px;padding:6px;")
        self.lbl_tend_detalle = QLabel("Pendiente DI: —  |  R²: —  |  Similitud actual: —")
        self.lbl_tend_detalle.setFont(QFont("Consolas", 8))
        self.lbl_tend_detalle.setStyleSheet(f"color:{C_TEXT_DIM};background:transparent;")
        tend_lay.addWidget(self.lbl_tendencia)
        tend_lay.addWidget(self.lbl_tend_detalle)

        self.fig_tend = Figure(facecolor=C_BG)
        self.fig_tend.subplots_adjust(left=0.15, right=0.95, top=0.9, bottom=0.15)
        self.cv_tend = FigureCanvas(self.fig_tend)
        self.cv_tend.setFixedHeight(130)
        self.ax_tend = self.fig_tend.add_subplot(111)
        _estilizar_ax(self.ax_tend, "DI medio por sesión", "DI [σ]", '#FF8C00', xlabel="Sesión")
        self.line_tend_di, = self.ax_tend.plot([], [], color='#FF8C00', lw=1.5, marker='o', markersize=3,
                                               label='DI sesión')
        self.line_tend_reg, = self.ax_tend.plot([], [], color='#FF4488', lw=1.0, ls='--', alpha=0.8,
                                                label='Regresión')
        self.ax_tend.axhline(3.0, color=C_ALARM, ls='--', lw=0.8, alpha=0.7)
        self.ax_tend.set_xlim(0, 10)
        self.ax_tend.set_ylim(0, 4)
        self.ax_tend.legend(fontsize=6, facecolor='#0A1020', edgecolor=C_BORDER, labelcolor='white')
        tend_lay.addWidget(self.cv_tend)
        grp_tend.setLayout(tend_lay)
        fila1.addWidget(grp_tend, stretch=3)
        main.addLayout(fila1)

        grp_hist = QGroupBox("Sesiones anteriores")
        grp_hist.setStyleSheet(GRP_STYLE)
        hist_lay = QVBoxLayout()
        enc = QHBoxLayout()
        for txt, ancho in [("ID", 40), ("Inicio", 150), ("Horas", 55), ("DI med", 60), ("DI máx", 60),
                           ("Sobrec.", 55), ("Desb.", 55), ("Similitud", 75), ("Sensor", 120)]:
            lh = QLabel(txt)
            lh.setFixedWidth(ancho)
            lh.setFont(QFont("Consolas", 7, QFont.Bold))
            lh.setStyleSheet("color:#AACCFF;background:#0D1525;padding:2px;")
            lh.setAlignment(Qt.AlignCenter)
            enc.addWidget(lh)
        enc.addStretch()
        hist_lay.addLayout(enc)

        scroll2 = QScrollArea()
        scroll2.setWidgetResizable(True)
        scroll2.setFixedHeight(160)
        scroll2.setStyleSheet(f"background:{C_AX_BG};border:1px solid {C_BORDER};border-radius:4px;")
        self._hist_container = QWidget()
        self._hist_container.setStyleSheet(f"background:{C_AX_BG};")
        self._hist_vlay = QVBoxLayout()
        self._hist_vlay.setSpacing(1)
        self._hist_vlay.setContentsMargins(0, 0, 0, 0)
        self._hist_container.setLayout(self._hist_vlay)
        scroll2.setWidget(self._hist_container)
        hist_lay.addWidget(scroll2)

        btn_row = QHBoxLayout()
        self.btn_exportar = QPushButton("Exportar a Excel")
        self.btn_exportar.setStyleSheet(
            "QPushButton{background:#002244;color:#00BFFF;border:1px solid #0055AA;border-radius:5px;font-weight:bold;padding:5px 14px;}"
            "QPushButton:hover{background:#003366;}")
        self.btn_exportar.clicked.connect(self._exportar_excel)
        self.btn_refrescar = QPushButton("Refrescar tabla")
        self.btn_refrescar.setStyleSheet(
            "QPushButton{background:#0F1830;color:#7AAFFF;border:1px solid #2A5090;border-radius:5px;font-weight:bold;padding:5px 14px;}"
            "QPushButton:hover{background:#142035;}")
        self.btn_refrescar.clicked.connect(self._refrescar_panel_historico)
        self.lbl_export_status = QLabel("")
        self.lbl_export_status.setFont(QFont("Consolas", 8))
        self.lbl_export_status.setStyleSheet(f"color:{C_TEXT_DIM};background:transparent;")
        btn_row.addWidget(self.btn_exportar)
        btn_row.addWidget(self.btn_refrescar)
        btn_row.addWidget(self.lbl_export_status)
        btn_row.addStretch()
        hist_lay.addLayout(btn_row)

        grp_hist.setLayout(hist_lay)
        main.addWidget(grp_hist)
        w.setLayout(main)
        scroll.setWidget(w)
        self._refrescar_panel_historico()
        return scroll

    def _actualizar_panel_firma(self, res: dict, sim: float | None):
        try:
            sim_txt = f"{sim:.1f} %" if sim is not None else "Sin ref."
            sim_col = '#00FFCC' if sim and sim > 85 else '#FFD700' if sim and sim > 65 else '#FF4444'
            self.lbl_firma_sim.setText(f"Similitud: {sim_txt}")
            self.lbl_firma_sim.setStyleSheet(
                f"color:{sim_col};background:transparent;font-size:20pt;font-weight:bold;")

            self.lbl_firma_detalle.setText(
                f"RPM pico: {(res.get('rpm_pico') or 0):.0f}  |  I pico: {(res.get('c_pico') or 0):.1f} mA  |  "
                f"T estab.: {(res.get('t_estab_s') or 0):.1f} s")

            muestras = res.get('muestras', [])
            if muestras:
                ts = [m['t'] for m in muestras]
                cs = [m['c_proc'] for m in muestras]
                vs = [m['v_proc'] for m in muestras]
                self.line_firma_c.set_data(ts, cs)
                self.line_firma_v.set_data(ts, vs)
                for ax, ys in ((self.ax_firma_c, cs), (self.ax_firma_v, vs)):
                    ax.set_xlim(min(ts), max(ts) + 1)
                    ax.set_ylim(0, max(ys) * 1.3 + 1)

            ref = self._firma_ref_vec
            if ref is not None and len(ref) >= 3 and muestras:
                chunk = len(ref) // 3
                c_ref = np.array(ref[:chunk]) * 2400.0
                v_ref = np.array(ref[chunk:2 * chunk]) * 50.0
                t_ref = list(range(len(c_ref)))[:len(muestras)]
                self.line_firma_c_ref.set_data(t_ref, c_ref[:len(muestras)])
                self.line_firma_v_ref.set_data(t_ref, v_ref[:len(muestras)])
            self.cv_firma.draw_idle()
        except Exception as e:
            logging.warning(f"Error en _actualizar_panel_firma: {e}")

    def _refrescar_panel_historico(self):
        try:
            sesiones = self._db.obtener_sesiones(30)
            while self._hist_vlay.count():
                item = self._hist_vlay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            for s in sesiones:
                row_w = QWidget()
                row_w.setStyleSheet(f"background:#0D1525;border-bottom:1px solid {C_BORDER};")
                row_lay = QHBoxLayout()
                row_lay.setContentsMargins(0, 1, 0, 1)
                row_lay.setSpacing(0)

                sim = s['similitud_pct']
                dim = s['di_medio'] or 0
                dimx = s['di_max'] or 0
                sobr = s['sobrecargas'] or 0
                desb = s['desbalances'] or 0
                sim_col = '#00FFCC' if sim and sim > 80 else '#FFD700' if sim and sim > 60 else '#FF4444' if sim else '#556688'
                di_col = '#00FFCC' if dim < 1.0 else '#FFD700' if dim < 2.0 else '#FF4444'

                celdas = [
                    (str(s['id']), 40, '#AACCFF'),
                    (s['inicio'] or '—', 150, C_TEXT),
                    (f"{s['horas_op'] or 0:.1f}h", 55, C_TEXT),
                    (f"{dim:.2f}σ" if dim else '—', 60, di_col),
                    (f"{dimx:.2f}σ" if dimx else '—', 60, di_col),
                    (str(sobr), 55, '#FF4444' if sobr else C_TEXT),
                    (str(desb), 55, '#FFA500' if desb else C_TEXT),
                    (f"{sim:.0f}%" if sim else '—', 75, sim_col),
                    (s['sensor_tipo'] or '—', 120, C_TEXT_DIM),
                ]
                for txt, ancho, fg in celdas:
                    lbl = QLabel(txt)
                    lbl.setFixedWidth(ancho)
                    lbl.setFont(QFont("Consolas", 8))
                    lbl.setStyleSheet(f"color:{fg};background:transparent;padding:1px 3px;")
                    lbl.setAlignment(Qt.AlignCenter)
                    row_lay.addWidget(lbl)
                row_lay.addStretch()
                row_w.setLayout(row_lay)
                self._hist_vlay.addWidget(row_w)

            tend = self._db.tendencia_di(30)
            if len(tend) >= 2:
                xs = np.arange(len(tend), dtype=float)
                dis = np.array([t['di_medio'] or 0 for t in tend], dtype=float)
                self.line_tend_di.set_data(xs, dis)
                pend_ls, interc = np.polyfit(xs, dis, 1)
                self.line_tend_reg.set_data(xs, pend_ls * xs + interc)
                self.ax_tend.set_xlim(0, max(len(tend) - 1, 5))
                self.ax_tend.set_ylim(0, max(float(np.max(dis)) * 1.3, 1.0))
                self.cv_tend.draw_idle()

                res = self._tendencia.analizar()
                nivel = res['nivel']
                color = {'ok': '#00FFCC', 'alerta': '#FFD700', 'critico': '#FF4444',
                         'sin_datos': '#556688'}.get(nivel, '#556688')
                bg = {'ok': '#001A00', 'alerta': '#1A1000', 'critico': '#2A0000',
                      'sin_datos': C_PANEL}.get(nivel, C_PANEL)
                self.lbl_tendencia.setText(res['mensaje'])
                self.lbl_tendencia.setStyleSheet(
                    f"background:{bg};color:{color};border:1px solid {color};border-radius:4px;padding:6px;")

                pend, r2, sim_a = res.get('pendiente_di'), res.get('r2'), res.get('sim_actual')
                if pend is not None and r2 is not None and sim_a is not None:
                    self.lbl_tend_detalle.setText(
                        f"Pendiente DI: {pend:+.4f}σ/sesión  |  R²: {r2:.3f}  |  "
                        f"Similitud actual: {sim_a:.1f}%  |  Sesiones: {len(tend)}")
                else:
                    self.lbl_tend_detalle.setText(f"Sesiones analizadas: {len(tend)}")
        except Exception as e:
            logging.warning(f"Error en _refrescar_panel_historico: {e}")

    def _cargar_tendencia_bd(self):
        try:
            tend = self._db.tendencia_di(30)
            ultimas = self._db.obtener_sesiones(1)
            sim_ult = ultimas[0].get('similitud_pct') if ultimas else None
            for t in tend:
                self._tendencia.agregar_sesion(t['id'], t['di_medio'], sim_ult)
        except Exception as e:
            logging.warning(f"Error en _cargar_tendencia_bd: {e}")

    def _exportar_excel(self):
        try:
            ruta = self._db.exportar_excel()
            self.lbl_export_status.setText(f"Guardado: {ruta}")
            self.lbl_export_status.setStyleSheet("color:#00FFCC;background:transparent;")
        except ImportError:
            self.lbl_export_status.setText("Falta la dependencia openpyxl (pip install openpyxl)")
            self.lbl_export_status.setStyleSheet("color:#FFD700;background:transparent;")
        except Exception as e:
            self.lbl_export_status.setText(f"Error: {e}")
            self.lbl_export_status.setStyleSheet("color:#FF4444;background:transparent;")

    # ──────────────────────────────────────────────────────────────────────────
    # EVENTOS Y DIAGNÓSTICO
    # ──────────────────────────────────────────────────────────────────────────
    def _registrar_evento(self, tipo, d):
        evento = {
            'tipo': tipo, 'hora_op': self._horas_acum,
            'c_proc': d.get('c_proc', 0.0), 'c_crud': d.get('c_crud', 0.0),
            'v_proc': d.get('v_proc', 0.0), 'v_crud': d.get('v_crud', 0.0),
            'rpm': d.get('rpm', 0), 'bin_dom': d.get('bin_dom', 0),
            'diagnostico': self._diagnosticar_evento(tipo, d),
        }
        self._historial_eventos.append(evento)
        if len(self._historial_eventos) > 50:
            self._historial_eventos.pop(0)
        self.db_queue.put((self._db.guardar_evento,
                           (tipo, self._horas_acum, dict(d), evento['diagnostico']), {}))

    def _diagnosticar_evento(self, tipo, d):
        c = d.get('c_proc', 0.0)
        cv = d.get('c_crud', 0.0)
        v = d.get('v_proc', 0.0)
        r = d.get('rpm', 0)
        b = d.get('bin_dom', 0)
        p = self._placa if self._placa_cargada else {}

        i_nom = p.get('i_nom', 300.0)
        r_nom = p.get('rpm_nom', 1750.0)
        v_nom = p.get('vib_nom', 5.0)

        ratio_i = c / max(i_nom, 1.0)
        ratio_ip = cv / max(i_nom, 1.0)
        ratio_v = v / max(v_nom, 1.0)
        ratio_rpm = r / max(r_nom, 1.0)

        if tipo == 'SOBRECARGA':
            if ratio_ip > 2.5 and ratio_v < 1.5:
                return {'causa': "Sobrecarga mecánica súbita (atascamiento)",
                        'norma': "NEMA MG1 §12.48 / IEEE 1099 §6.3.2",
                        'accion': "Verificar carga acoplada, correas o rodamientos bloqueados"}
            if ratio_i > 1.5 and ratio_v > 2.5:
                return {'causa': "Desbalance de carga con vibración excesiva",
                        'norma': "ISO 10816-3 Zona D / IEEE 1099 §6.4",
                        'accion': "Balanceo dinámico urgente e inspección de rodamientos"}
            if ratio_i > 1.3 and ratio_rpm < 0.88:
                return {'causa': "Caída de tensión de alimentación o falla de fase",
                        'norma': "NEMA MG1 §12.45 / IEEE Std 1100",
                        'accion': "Verificar tensión de red, fusibles y borneras"}
            if ratio_i > 1.2 and ratio_v < 1.2 and ratio_rpm > 0.95:
                return {'causa': "Sobrecarga térmica por ciclo de trabajo excesivo",
                        'norma': "IEC 60034-1 (servicio S1→S3)",
                        'accion': "Reducir ciclo de trabajo o mejorar la ventilación"}
            if ratio_ip > 1.8 and ratio_rpm < 0.7:
                return {'causa': "Arranque con carga plena (corriente de bloqueo)",
                        'norma': "NEMA MG1 §12.38",
                        'accion': "Implementar arrancador suave o variador de frecuencia"}
            return {'causa': "Sobrecorriente transitoria (posible perturbación de red)",
                    'norma': "IEEE 1100-2005 §8.3",
                    'accion': "Instalar filtro EMI o supresor de transitorios"}

        if ratio_v > 3.0 and b > 0:
            return {'causa': f"Desbalance mecánico severo (bin {b})",
                    'norma': "ISO 10816-3 Zona D",
                    'accion': "Balanceo dinámico inmediato e inspección de rotor y acoplamiento"}
        if ratio_v > 2.0 and ratio_i > 1.1:
            return {'causa': "Excentricidad de rotor o desgaste de rodamiento",
                    'norma': "ISO 10816-3 Zona C / IEEE 1099 §7.2.1",
                    'accion': "Análisis de firma de corriente (MCSA) y cambio de rodamientos"}
        if ratio_v > 1.5 and ratio_rpm > 1.05:
            return {'causa': "Resonancia mecánica (frecuencia natural excitada)",
                    'norma': "ISO 10816-1 §5.4",
                    'accion': "Verificar rigidez de montaje y frecuencias de resonancia"}
        if ratio_v > 1.0 and ratio_i < 1.05 and ratio_rpm > 0.98:
            return {'causa': "Desalineación de acoplamiento (angular o paralela)",
                    'norma': "ISO 10816-3 Zona B→C / NEMA MG1 §7.06",
                    'accion': "Realineación láser del acoplamiento motor-carga"}
        return {'causa': "Vibración elevada sin patrón eléctrico claro",
                'norma': "ISO 10816-3 Zona B",
                'accion': "Incrementar frecuencia de monitoreo e inspección visual"}

    def _entrenar_ml_async(self, datos):
        """Entrena el modelo en segundo plano y lo intercambia de forma atómica."""
        try:
            modelo = IsolationForest(n_estimators=50, contamination='auto', random_state=42)
            modelo.fit(datos)
            self._ml_modelo = modelo
            self._ml_entrenado = True
        except Exception as e:
            logging.warning(f"Error asíncrono en ajuste ML: {e}")
        finally:
            self._ml_entrenando = False

    def _lanzar_entrenamiento(self):
        self._ml_entrenando = True
        threading.Thread(target=self._entrenar_ml_async,
                         args=(np.array(self._ml_buffer),), daemon=True).start()

    # ──────────────────────────────────────────────────────────────────────────
    # INICIO / PARADA DE ADQUISICIÓN
    # ──────────────────────────────────────────────────────────────────────────
    def _iniciar(self):
        try:
            rpm = int(self.input_rpm.text())
            puerto = self.input_puerto.text().strip()
            if not (10 <= rpm <= 10000):
                raise ValueError("RPM fuera de rango (10–10000)")
            if not puerto:
                raise ValueError("Puerto inválido")

            try:
                delgas = int(self.input_delgas.text().strip())
                reductora = int(self.input_reductora.text().strip())
            except ValueError:
                delgas, reductora = 3, 0

            self.hilo_trabajo = TrabajoModbus(puerto, rpm)
            self.hilo_trabajo.datos_listos.connect(self._datos)
            self.hilo_trabajo.start()
            self.hilo_trabajo.cambiar_actuador(1 if self.btn_actuador.isChecked() else 0)

            def escribir(reg, val):
                if self.hilo_trabajo is not None:
                    self.hilo_trabajo.escribir_registro(reg, val)

            QTimer.singleShot(800, lambda: escribir(148, delgas))
            QTimer.singleShot(1200, lambda: escribir(149, reductora))

            self.btn_iniciar.setEnabled(False)
            self.btn_detener.setEnabled(True)
            self.input_rpm.setEnabled(False)
            self.input_puerto.setEnabled(False)
            self._set_estado("Conectando al dsPIC...", '#00FFCC')

            self._horas_acum = 0.0
            self._historial_eventos.clear()
            self._sobrecarga_count = 0
            self._desbalance_count = 0
            self._di_sesion.clear()
            self._muestra_sesion = 0
            self._similitud_sesion = None
            self.filtro_iir.reset()

            self._ml_entrenado = False
            self._ml_entrenando = False
            self._ml_buffer.clear()
            self._muestras_desde_entrenamiento = 0

            self.disponibilidad_tecnica = 100.0
            self._timer_horas.start(3600000)

            placa_snap = self._placa if self._placa_cargada else None
            self.db_queue.put((self._db.abrir_sesion, (rpm, "ACS758_FIXED", placa_snap), {}))

            self._firma = AnalizadorFirma(rpm, placa_snap)
            ref = self._db.obtener_firma_referencia()
            self._firma_ref_vec = ref['vector'] if ref else None
        except ValueError as e:
            self._set_estado(f"{e}", '#FF4444', bg='#2A0000', border='#FF2222')

    def _detener(self):
        if self.hilo_trabajo:
            self.hilo_trabajo.detener()
            self.hilo_trabajo.wait()
            self.hilo_trabajo = None

        self.btn_iniciar.setEnabled(True)
        self.btn_detener.setEnabled(False)
        self.input_rpm.setEnabled(True)
        self.input_puerto.setEnabled(True)
        self._timer_horas.stop()

        for k in (self.kalman_corr, self.kalman_vib, self.kalman_rpm):
            k.reset()

        di_arr = self._di_sesion
        di_medio = float(np.mean(di_arr)) if di_arr else None
        di_max = float(np.max(di_arr)) if di_arr else None

        self.db_queue.put((self._db.cerrar_sesion, (), {
            'horas_op': self._horas_acum, 'sobrecargas': self._sobrecarga_count,
            'desbalances': self._desbalance_count, 'di_medio': di_medio,
            'di_max': di_max, 'similitud_pct': self._similitud_sesion}))

        if di_medio is not None:
            ultimas = self._db.obtener_sesiones(1)
            sid = ultimas[0]['id'] if ultimas else 0
            self._tendencia.agregar_sesion(sid, di_medio, self._similitud_sesion)

        self._set_estado("MONITOREO DETENIDO", '#AAAAAA', border='#444444')
        self._refrescar_panel_historico()

    # ──────────────────────────────────────────────────────────────────────────
    # PROCESAMIENTO DE CADA PAQUETE DE DATOS
    # ──────────────────────────────────────────────────────────────────────────
    def _estimar_rpm_eje(self, raw_rpm, c_proc):
        try:
            reductora = float(self.input_reductora.text().replace(',', '.'))
        except ValueError:
            reductora = 0.0
        rpm_eje_raw = raw_rpm / max(reductora, 1.0)

        if rpm_eje_raw > 1.0:
            self._rpm_buffer.append(rpm_eje_raw)

        if c_proc > 50.0:
            if self._rpm_buffer:
                return float(np.median(self._rpm_buffer))
            try:
                return float(self.input_rpm.text().replace(',', '.'))
            except ValueError:
                return 2200.0
        self._rpm_buffer.clear()
        return 0.0

    @staticmethod
    def _desplazar(arr, valor):
        arr[:-1] = arr[1:]
        arr[-1] = valor

    def _analisis_largo(self, n_actual):
        """Cepstrum de la ventana larga y validación de Parseval. Devuelve (e_t, e_f, err)."""
        e_tiempo = e_freq = error_pars = 0.0
        if n_actual < 64:
            return e_tiempo, e_freq, error_pars

        arr = np.array(self.buffer_vib_largo)
        arr = (arr - np.mean(arr)) * np.hanning(n_actual)
        espectro = np.abs(np.fft.fft(arr))
        cepstrum = np.abs(np.fft.ifft(np.log(espectro + 1e-6)))
        mitad = n_actual // 2
        i0 = max(1, n_actual // 50)
        i1 = min(mitad, n_actual // 5)
        if i1 > i0:
            self.impacto_actual = float(np.max(cepstrum[i0:i1]))
        self.line_cepstrum.setData(cepstrum[:mitad])
        self.p_ceps.setXRange(0, mitad)

        # Parseval sobre los últimos 64 puntos
        a64 = np.array(list(self.buffer_vib_largo)[-64:])
        a64 = (a64 - np.mean(a64)) * np.hanning(64)
        e_tiempo = float(np.sum(np.square(a64)))
        e_freq = float(np.sum(np.square(np.abs(np.fft.fft(a64)))) / 64.0)
        error_pars = abs(e_tiempo - e_freq) / max(e_tiempo, 1e-6) * 100.0

        self.hist_etime.append(e_tiempo)
        self.hist_etime.pop(0)
        self.hist_efreq.append(e_freq)
        self.hist_efreq.pop(0)

        if self._tab_visible(TAB_FILTROS):
            x = list(range(self.MAX_DATOS))
            self.line_etime.set_data(x, self.hist_etime)
            self.line_efreq.set_data(x, self.hist_efreq)
            self.ax_pars.set_ylim(0, max(max(self.hist_etime), max(self.hist_efreq), 1.0) * 1.2)
            self.cv_pars.draw_idle()

            self.tabla_parseval.insertRow(0)
            self.tabla_parseval.setItem(0, 0, QTableWidgetItem(time.strftime("%H:%M:%S")))
            self.tabla_parseval.setItem(0, 1, QTableWidgetItem(f"{e_tiempo:.1f}"))
            self.tabla_parseval.setItem(0, 2, QTableWidgetItem(f"{e_freq:.1f}"))
            item = QTableWidgetItem(f"{error_pars:.4f} %")
            item.setForeground(QColor('#FF3333') if error_pars > 1.0 else QColor('#39FF14'))
            self.tabla_parseval.setItem(0, 3, item)
            if self.tabla_parseval.rowCount() > 8:
                self.tabla_parseval.removeRow(8)
        return e_tiempo, e_freq, error_pars

    def _actualizar_ml(self, d, c_proc, v_proc, rpm_eje):
        """Devuelve (texto_estado, predicción) del detector de anomalías."""
        if self._calibrando_rul or rpm_eje <= 50:
            return "MOTOR DETENIDO", 1

        p = self._placa if self._placa_cargada else {'rpm_nom': 1750.0, 'vib_nom': 5.0, 'i_nom': 300.0}
        vector = [
            min(v_proc / max(p['vib_nom'], 1.0), 10.0),
            min(c_proc / max(p['i_nom'], 1.0), 10.0),
            min(rpm_eje / max(p['rpm_nom'], 1.0), 3.0),
            min(self.impacto_actual / max(p['vib_nom'] * 0.25, 1.0), 10.0),
        ]
        if d.get('_predicho', False):
            return "MOTOR DETENIDO", 1

        sana = self._motor_en_regimen_estable(c_proc, v_proc, rpm_eje, self.impacto_actual)

        if not self._ml_entrenado:
            if sana:
                self._ml_buffer.append(vector)
            faltan = self._ML_SAMPLES_TRAIN - len(self._ml_buffer)
            if faltan > 0:
                return f"CALIBRANDO {faltan}", 1
            if not self._ml_entrenando:
                self._lanzar_entrenamiento()
            return "ENTRENANDO...", 1

        pred = self._ml_modelo.predict(np.array([vector]))[0]
        if sana:
            self._ml_buffer.append(vector)
            self._muestras_desde_entrenamiento += 1
            if self._muestras_desde_entrenamiento >= 200:
                self._muestras_desde_entrenamiento = 0
                if not self._ml_entrenando:
                    self._lanzar_entrenamiento()
        return ("ANOMALÍA DETECTADA" if pred == -1 else "ML: NORMAL"), pred

    def _procesar_firma(self, d):
        """Acumula la firma de arranque y actualiza similitud y confianza."""
        sim = self._similitud_sesion
        if self._firma and not self._firma.completada:
            completa = self._firma.agregar_muestra(self._muestra_sesion, d)
            res = self._firma.resumen()
            if completa:
                sim = self._firma.calcular_similitud(self._firma_ref_vec)
                self._similitud_sesion = sim
                if sim is not None:
                    if len(self.hist_sim) > 5:
                        self.drift_operacional = abs(sim - float(np.mean(self.hist_sim)))
                    self.hist_sim.append(sim)
                self.db_queue.put((self._db.guardar_firma, (), {
                    'vector': res['vector_norm'], 'rpm_pico': res['rpm_pico'],
                    'c_pico': res['c_pico'], 't_estab_s': res['t_estab_s'],
                    'similitud_pct': sim}))
            if self._tab_visible(4):
                self._actualizar_panel_firma(res, sim)

        sim_conf = sim if sim else 100.0
        self.confianza_sistema = float(np.clip(
            sim_conf * 0.4 + (100 - self.drift_operacional) * 0.3 + self.health_score * 0.3, 0, 100))

    @pyqtSlot(dict)
    def _datos(self, d):
        try:
            if d.get('error', False):
                for k in (self.kalman_corr, self.kalman_vib, self.kalman_rpm):
                    k.reset()
                self._set_estado("RUIDO EMI — ESPERANDO PAQUETE VÁLIDO", '#FFD700', bg='#1A1000', border='#886600')
                return

            c_proc = float(d.get('c_proc', 0.0))
            v_proc = float(d.get('v_proc', 0.0))
            c_crud = float(d.get('c_crud', 0.0))
            v_crud = float(d.get('v_crud', 0.0))
            ref_sana = d.get('ref_sana', 0)
            rpm_eje = self._estimar_rpm_eje(float(d.get('rpm', 0.0)), c_proc)

            # --- FFT reportada por el dsPIC ---
            fft_dspic = np.array(d.get('fft_vib', [0] * 32), dtype=float)
            if c_proc < 10.0 or rpm_eje < 10.0:
                fft_plot = np.zeros(32)
                bin_dom = 0
            else:
                fft_plot = fft_dspic
                bin_dom = int(d.get('bin_dom', 0))
                if bin_dom == 0:
                    bin_dom = int(np.argmax(fft_plot[1:]) + 1)
            self.fft_bins = fft_plot

            # --- Buffers de historial ---
            self._desplazar(self.hist_c_p1, c_proc)
            self._desplazar(self.hist_v_p1, v_proc)
            self._desplazar(self.hist_c_p2, c_crud)
            self._desplazar(self.hist_v_p2, v_crud)
            self.buffer_vib_largo.append(v_crud)
            n_actual = len(self.buffer_vib_largo)

            # --- Filtro IIR de vibración ---
            v_iir = max(0.0, self.filtro_iir.procesar(v_crud))
            self._desplazar(self.hist_iir_p1, v_iir)

            if not d.get('_predicho', False):
                self.kalman_corr.update(c_proc)
                self.kalman_vib.update(v_proc)
                self.kalman_rpm.update(rpm_eje)

            self._carga_pct, estado_carga = self._calcular_pct(c_proc, rpm_eje)

            # --- Alertas ---
            alerta = d.get('alertas', 0)
            if alerta & 0b01:
                self._sobrecarga_count += 1
                self._registrar_evento('SOBRECARGA', d)
                self._set_estado(f"SOBRECARGA  {c_crud} mA | CARGA: {self._carga_pct}%",
                                 '#FF2222', bg='#330000', border='red')
            elif alerta & 0b10:
                self._desbalance_count += 1
                self._registrar_evento('DESBALANCE', d)
                self._set_estado(f"DESBALANCE  Bin: {bin_dom} | {self._rul_tiempo_real_str()}",
                                 '#FFA500', bg='#2A1800', border='orange')
            else:
                self._set_estado(f"NORMAL  |  RPM: {rpm_eje:.0f}  |  CARGA: {self._carga_pct}%  |  "
                                 f"{self._rul_tiempo_real_str()}", '#00FFCC')

            # --- Disponibilidad y eficiencia ---
            fallas = self._sobrecarga_count + self._desbalance_count
            if fallas == 0:
                self.disponibilidad_tecnica = 100.0
            else:
                mtbf = max(self._horas_acum, 0.1) / fallas
                self.disponibilidad_tecnica = mtbf / (mtbf + self.MTTR_TEORICO) * 100.0

            if self._placa_cargada and rpm_eje > 10:
                carga_n = self._carga_pct / 100.0
                if carga_n < 0.05:
                    eta = 0.0
                else:
                    eta = max(0.1, min(0.85, 0.85 - 0.4 * (carga_n - 0.75) ** 2))
                self._eta_pct = eta * 100
            else:
                self._eta_pct = 0.0

            try:
                rpm_obj = float(self.input_rpm.text())
            except ValueError:
                rpm_obj = 0.0

            # --- Envolvente, cepstrum y Parseval ---
            self.envolvente = np.abs(hilbert(self.hist_v_p1))
            self.line_envolvente.setData(self.envolvente)
            e_tiempo, e_freq, error_pars = self._analisis_largo(n_actual)

            if self._rul_activo:
                self._calcular_rul(c_proc, v_proc, rpm_eje)

            ml_txt, pred = self._actualizar_ml(d, c_proc, v_proc, rpm_eje)

            # --- Salud global ---
            penal = (min(30, v_proc * 0.6) + min(20, v_iir * 0.4)
                     + max(0, self._carga_pct - 100) * 0.5 + (15 if pred == -1 else 0))
            self.health_score = max(0.0, 100.0 - penal)
            diagnostico = self.analizar_salud_motor(self._carga_pct, v_proc, self.impacto_actual)

            # --- Indicadores principales ---
            self.disp_vib.set_valor(v_proc)
            self.disp_corr.set_valor(c_proc)
            self.disp_rpm.set_valor(rpm_eje)
            self.disp_carga.set_valor(self._carga_pct)
            self.disp_carga.set_color_acento(estado_carga[1])

            self.dash_secundario.actualizar_dashboard(
                self._carga_pct, estado_carga[0], estado_carga[1], self._rul_tiempo_real_str(),
                self._eta_pct, self.disponibilidad_tecnica,
                self._activo_id if self._activo_id != "UNSET" else self._motor_id)
            self.dash_secundario.actualizar_avanzado(
                self.health_score, self.confianza_sistema, self.drift_operacional, diagnostico)
            self._actualizar_reporte_texto(self._carga_pct, v_proc, self.impacto_actual,
                                           self.health_score, self.confianza_sistema, diagnostico)

            # --- Gráficas pyqtgraph ---
            self.line_v1.setData(self.hist_v_p1)
            self.line_v1_iir.setData(self.hist_iir_p1)
            self.line_c1.setData(self.hist_c_p1)
            self.line_ref.setValue(ref_sana)
            self.line_v2.setData(self.hist_v_p2)
            self.line_c2.setData(self.hist_c_p2)

            max_fft = max(float(np.max(fft_plot)) * 1.2, 10.0)
            self.matriz_fft = np.roll(self.matriz_fft, -1, axis=1)
            self.matriz_fft[:, -1] = fft_plot
            self.img_fft.setImage(self.matriz_fft.T[:, ::-1], autoLevels=False, levels=(0, max_fft))

            # --- Historiales de procesamiento de señal ---
            self.hist_raw_didact.append(v_crud)
            self.hist_raw_didact.pop(0)
            self.ema_sim += self.alpha_didact * (v_crud - self.ema_sim)
            self.hist_ema_didact.append(self.ema_sim)
            self.hist_ema_didact.pop(0)
            self.hist_iir_didact.append(v_iir)
            self.hist_iir_didact.pop(0)
            self.hist_rms_didact.append(c_proc)
            self.hist_rms_didact.pop(0)
            self.hist_rpm_med.append(float(rpm_eje))
            self.hist_rpm_med.pop(0)
            self.hist_rpm_obj.append(rpm_obj)
            self.hist_rpm_obj.pop(0)

            # --- Gráficas matplotlib (solo si la pestaña es visible) ---
            if self._tab_visible(TAB_FILTROS):
                self._dibujar_senal(c_proc, c_crud, fft_plot, bin_dom, max_fft)

            # --- Paneles de datos vivos ---
            datos_vivo = {
                'ml_status': ml_txt, 'v_proc': v_proc, 'v_iir': v_iir, 'c_proc': c_proc,
                'v_crud': v_crud, 'c_crud': c_crud, 'rpm': rpm_eje, 'ref_sana': ref_sana,
                'bin_dom': bin_dom, 'alertas': d.get('alertas', 0), 'rpm_obj': rpm_obj,
                'e_time': e_tiempo, 'e_freq': e_freq, 'err_pars': error_pars,
            }
            for panel in (self.panel_datos_t1, self.panel_datos_t2, self.panel_datos_ema,
                          self.panel_datos_rms, self.panel_datos_fft, self.panel_datos_rpm,
                          self.panel_datos_pars):
                panel.actualizar(datos_vivo)

            # --- Persistencia ---
            try:
                di_actual = self._hist_di[-1] if self._hist_di else None
                if di_actual is not None:
                    self._di_sesion.append(di_actual)
                self.db_queue.put((self._db.guardar_muestra, (self._muestra_sesion, dict(d)),
                                   {'di': di_actual, 'predicho': bool(d.get('_predicho', False))}))
                self._muestra_sesion += 1
            except Exception as e:
                logging.warning(f"Error al encolar guardar_muestra: {e}")

            try:
                self._procesar_firma(d)
            except Exception as e:
                logging.warning(f"Error en procesamiento de firma: {e}")

        except Exception as e:
            logging.error(f"[_datos] Fallo no capturado: {e}", exc_info=True)

    def _dibujar_senal(self, c_proc, c_crud, fft_plot, bin_dom, max_fft):
        x = list(range(self.MAX_DATOS))

        # EMA vs IIR
        self.line_raw_ema.set_data(x, self.hist_raw_didact)
        self.line_ema_out.set_data(x, self.hist_ema_didact)
        self.line_iir_out.set_data(x, self.hist_iir_didact)
        for col in self.ax_ema.collections[:]:
            col.remove()
        self.ax_ema.fill_between(x, self.hist_ema_didact, color=C_EMA, alpha=0.12)
        self.cv_ema.draw_idle()

        # True RMS
        self.line_rms_hist.set_data(x, self.hist_rms_didact)
        self.line_rms_peak.set_data(x, self.hist_c_p2)
        for bar, h in zip(self.bars_rms, (c_proc, c_crud)):
            bar.set_height(h)
        factor = (c_crud / c_proc) if c_proc > 0 else 0
        self.txt_rms_factor.set_text(f"Factor de cresta: {factor:.2f}")
        self.cv_rms.draw_idle()

        # FFT
        for i, (bar, h) in enumerate(zip(self.bar_fft, fft_plot)):
            bar.set_height(h)
            bar.set_color('#FF4488' if i == bin_dom and h > 2.0 else '#AA88FF')
        self.ax_fft_bars.set_ylim(0, max_fft)
        self.img_fft_d.set_array(self.matriz_fft)
        self.img_fft_d.set_clim(vmin=0, vmax=max_fft)
        self.cv_fft_d.draw_idle()

        # RPM
        self.line_rpm_med.set_data(x, self.hist_rpm_med)
        self.line_rpm_obj.set_data(x, self.hist_rpm_obj)
        self.cv_rpm.draw_idle()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ventana = PanelHMI()
    ventana.show()
    sys.exit(app.exec_())