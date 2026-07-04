import sys
import math
import ezdxf
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSlider, QLabel, QPushButton, QComboBox, QFileDialog, QListWidget
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath
from shapely.geometry import LineString, Polygon, box

class LazerCADEngine(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(700, 700)
        self.setMouseTracking(True)
        
        # CAD Chizish parametrlari
        self.nodes = []             # [(x, y), ...] millimetrda
        self.segment_types = []     # ["line", "line", ...]
        self.curvatures = []        # [0.0, 0.0, ...] -> Yoy bükülishi darajasi
        self.is_closed = False
        
        # Ish maydoni o'lchami (mm)
        self.workspace_size = 800.0
        
        # Naqsh parametrlari
        self.naqsh_turi = "Kvadrat Grid"
        self.scale_val = 1.0
        self.shift_x = 0.0
        self.shift_y = 0.0
        self.border_mm = 15.0
        
        # Naqsh kutubxonasi xom liniyalari
        self.naqsh_raw_lines = []
        self.naqsh_w = 100.0
        self.naqsh_h = 100.0
        
        # Yakuniy hisoblangan qismlar
        self.tashqi_shakl = None
        self.ichki_border = None
        self.cut_lines = []
        
        self.generatsiya_naqsh_kutubxonasi()

    def generatsiya_naqsh_kutubxonasi(self):
        self.naqsh_raw_lines = []
        if self.naqsh_turi == "Kvadrat Grid":
            self.naqsh_w, self.naqsh_h = 50.0, 50.0
            for i in range(6):
                self.naqsh_raw_lines.append([(0, i*10), (50, i*10)])
                self.naqsh_raw_lines.append([(i*10, 0), (i*10, 50)])
        elif self.naqsh_turi == "Romb (Diamond)":
            self.naqsh_w, self.naqsh_h = 60.0, 60.0
            self.naqsh_raw_lines.append([(30, 0), (60, 30), (30, 60), (0, 30), (30, 0)])
            self.naqsh_raw_lines.append([(0, 0), (60, 60)])
            self.naqsh_raw_lines.append([(0, 60), (60, 0)])
        elif self.naqsh_turi == "Islimiy Liniyalar":
            self.naqsh_w, self.naqsh_h = 80.0, 80.0
            steps = 24
            circle1 = [(40 + 20*math.cos(2*math.pi*i/steps), 40 + 20*math.sin(2*math.pi*i/steps)) for i in range(steps+1)]
            self.naqsh_raw_lines.append(circle1)
            self.naqsh_raw_lines.append([(0,0), (80,80)])
            self.naqsh_raw_lines.append([(0,80), (80,0)])
            self.naqsh_raw_lines.append([(40,0), (40,80)])
            self.naqsh_raw_lines.append([(0,40), (80,40)])

    def to_screen(self, x, y):
        """ MM koordinatadan ekran piksellariga o'tkazish """
        pad = 50
        avail_w = self.width() - 2 * pad
        avail_h = self.height() - 2 * pad
        scale = min(avail_w / self.workspace_size, avail_h / self.workspace_size)
        
        sx = pad + x * scale
        sy = self.height() - pad - y * scale
        return QPointF(sx, sy)

    def to_cad(self, sx, sy):
        """ Ekrandagi pikselni MM koordinataga o'tkazish """
        pad = 50
        avail_w = self.width() - 2 * pad
        avail_h = self.height() - 2 * pad
        scale = min(avail_w / self.workspace_size, avail_h / self.workspace_size)
        
        x = (sx - pad) / scale
        y = (self.height() - pad - sy) / scale
        return x, y

    def mousePressEvent(self, event):
        if self.is_closed:
            return # Yopilgan shaklni qayta chizib bo'lmaydi, reset qilish kerak
            
        x, y = self.to_cad(event.position().x(), event.position().y())
        
        if event.button() == Qt.MouseButton.LeftButton:
            # Yangi nuqta qo'shish
            self.nodes.append((x, y))
            if len(self.nodes) > 1:
                self.segment_types.append("line")
                self.curvatures.append(0.0)
            self.parent().parent().yangila_segment_royxati()
            
        elif event.button() == Qt.MouseButton.RightButton and len(self.nodes) >= 3:
            # Shaklni yopish (Close Loop)
            self.segment_types.append("line")
            self.curvatures.append(0.0)
            self.is_closed = True
            self.parent().parent().yangila_segment_royxati()
            
        self.yangila_geometriya()
        self.update()

    def hisobla_segment_nuqtalari(self, p1, p2, seg_type, curv, steps=20):
        """ Chiziqni silliq yoyga yoki to'g'ri chiziqqa aylantiruvchi motor """
        if seg_type == "line" or curv == 0.0:
            return [p1, p2]
        
        # AutoCAD uslubidagi Arc generatsiyasi (Bézier bükülishi)
        pts = []
        mx = (p1[0] + p2[0]) / 2.0
        my = (p1[1] + p2[1]) / 2.0
        
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = math.sqrt(dx*dx + dy*dy)
        
        if length == 0:
            return [p1, p2]
            
        # Perpendikulyar vektor yo'nalishi
        nx = -dy / length
        ny = dx / length
        
        # Boshqaruv nuqtasi (Control Point)
        cx = mx + nx * curv * 2.0
        cy = my + ny * curv * 2.0
        
        for i in range(steps + 1):
            t = i / steps
            # Quadratic Bezier formulasi
            rx = (1-t)**2 * p1[0] + 2*(1-t)*t * cx + t**2 * p2[0]
            ry = (1-t)**2 * p1[1] + 2*(1-t)*t * cy + t**2 * p2[1]
            pts.append((rx, ry))
        return pts

    def yangila_geometriya(self):
        if len(self.nodes) < 3 or not self.is_closed:
            self.tashqi_shakl = None
            self.ichki_border = None
            self.cut_lines = []
            return

        # 1. Butun poliliniya nuqtalarini yig'ish (Yoylar bilan birga)
        barcha_nuqtalar = []
        n = len(self.nodes)
        
        for i in range(n):
            p1 = self.nodes[i]
            p2 = self.nodes[(i + 1) % n]
            seg_type = self.segment_types[i]
            curv = self.curvatures[i]
            
            seg_pts = self.hisobla_segment_nuqtalari(p1, p2, seg_type, curv)
            if i == 0:
                barcha_nuqtalar.extend(seg_pts)
            else:
                barcha_nuqtalar.extend(seg_pts[1:]) # Takrorlanishni oldini olish

        # Shapely ko'pburchagini qurish
        try:
            self.tashqi_shakl = Polygon(barcha_nuqtalar).buffer(0)
            if self.tashqi_shakl.is_empty or self.tashqi_shakl.geom_type != 'Polygon':
                return
        except:
            return

        # 2. Ichki border hisoblash
        self.ichki_border = self.tashqi_shakl.buffer(-self.border_mm, join_style=2).buffer(0)

        # 3. Naqshlarni o'yinlar kabi cheksiz tekstura qilib qirqib joylashtirish
        self.cut_lines = []
        if not self.ichki_border or self.ichki_border.is_empty or not self.naqsh_raw_lines:
            return

        nw = max(5.0, self.naqsh_w * self.scale_val)
        nh = max(5.0, self.naqsh_h * self.scale_val)

        b_minx, b_miny, b_maxx, b_maxy = self.ichki_border.bounds
        b_cx = (b_minx + b_maxx) / 2
        b_cy = (b_miny + b_maxy) / 2

        wrapped_x = self.shift_x % nw
        wrapped_y = self.shift_y % nh

        start_kx = int((b_minx - b_cx - wrapped_x) // nw) - 1
        end_kx = int((b_maxx - b_cx - wrapped_x) // nw) + 1
        start_ky = int((b_miny - b_cy - wrapped_y) // nh) - 1
        end_ky = int((b_maxy - b_cy - wrapped_y) // nh) + 1

        for kx in range(start_kx, end_kx + 1):
            for ky in range(start_ky, end_ky + 1):
                cx = b_cx + wrapped_x + kx * nw
                cy = b_cy + wrapped_y + ky * nh
                
                for line in self.naqsh_raw_lines:
                    if len(line) < 2: continue
                    try:
                        pts = [(pt[0] * self.scale_val + cx, pt[1] * self.scale_val + cy) for pt in line]
                        ls = LineString(pts)
                        
                        clipped = ls.intersection(self.ichki_border)
                        if not clipped.is_empty:
                            if clipped.geom_type == 'LineString':
                                self.cut_lines.append(list(clipped.coords))
                            elif clipped.geom_type == 'MultiLineString':
                                for sub_geom in clipped.geoms:
                                    self.cut_lines.append(list(sub_geom.coords))
                    except: continue

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(18, 18, 18))

        # Yordamchi setka (Grid) chizish
        painter.setPen(QPen(QColor(40, 40, 40), 0.5))
        for step in range(0, int(self.workspace_size)+1, 50):
            p_start_h = self.to_screen(0, step)
            p_end_h = self.to_screen(self.workspace_size, step)
            painter.drawLine(p_start_h, p_end_h)
            p_start_v = self.to_screen(step, 0)
            p_end_v = self.to_screen(step, self.workspace_size)
            painter.drawLine(p_start_v, p_end_v)

        # 1. ERKIN CHIZILAYOTGAN NUQTALAR VA CHIZIQLAR
        if len(self.nodes) > 0:
            painter.setPen(QPen(QColor(0, 255, 255), 2))
            for i in range(len(self.nodes) - 1):
                p1 = self.nodes[i]
                p2 = self.nodes[i+1]
                # Agar oraliq turi yoy bo'lsa, uni silliq chizish
                pts = self.hisobla_segment_nuqtalari(p1, p2, self.segment_types[i], self.curvatures[i])
                for k in range(len(pts)-1):
                    painter.drawLine(self.to_screen(pts[k][0], pts[k][1]), self.to_screen(pts[k+1][0], pts[k+1][1]))
            
            # Agar yopilmagan bo'lsa, chizishni davom ettirish chizig'i
            if not self.is_closed:
                painter.setPen(QPen(QColor(0, 255, 255), 1, Qt.PenStyle.DashLine))
                # Oxirgi nuqtaga kichik doira qo'yish
                last_pt = self.to_screen(self.nodes[-1][0], self.nodes[-1][1])
                painter.drawEllipse(last_pt, 4, 4)

        # 2. TAYYOR TASHQI SHAKL (Yopilgandan keyin - Yashil)
        if self.is_closed and self.tashqi_shakl:
            painter.setPen(QPen(QColor(0, 255, 120), 2.5))
            path_tashqi = QPainterPath()
            t_coords = list(self.tashqi_shakl.exterior.coords)
            if t_coords:
                path_tashqi.moveTo(self.to_screen(t_coords[0][0], t_coords[0][1]))
                for x, y in t_coords[1:]:
                    path_tashqi.lineTo(self.to_screen(x, y))
                path_tashqi.closeSubpath()
                painter.drawPath(path_tashqi)

        # 3. ICHKI BORDER (Sariq)
        if self.ichki_border and not self.ichki_border.is_empty:
            painter.setPen(QPen(QColor(255, 190, 0), 1.5, Qt.PenStyle.DashLine))
            borders = [self.ichki_border] if self.ichki_border.geom_type == 'Polygon' else self.ichki_border.geoms
            for poly in borders:
                path_border = QPainterPath()
                b_coords = list(poly.exterior.coords)
                if b_coords:
                    path_border.moveTo(self.to_screen(b_coords[0][0], b_coords[0][1]))
                    for x, y in b_coords[1:]:
                        path_border.lineTo(self.to_screen(x, y))
                    path_border.closeSubpath()
                    painter.drawPath(path_border)

        # 4. CHEKSIZ QIRQILGAN NAQSHLAR (Oq)
        painter.setPen(QPen(QColor(255, 255, 255), 1.2))
        for line in self.cut_lines:
            if len(line) < 2: continue
            for idx in range(len(line)-1):
                painter.drawLine(self.to_screen(line[idx][0], line[idx][1]), self.to_screen(line[idx+1][0], line[idx+1][1]))


class AutoCADLauncher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lazer Ustasi v11.0 - Polyline Vektor Injiniring (AutoCAD Arc Engine)")
        self.setGeometry(50, 50, 1450, 850)
        
        self.engine = LazerCADEngine()
        self.init_ui()

    def init_ui(self):
        asosiy_widget = QWidget()
        self.setCentralWidget(asosiy_widget)
        gory_layout = QHBoxLayout(asosiy_widget)

        # Chap panel: CAD ish stoli
        gory_layout.addWidget(self.engine, stretch=3)

        # O'ng panel: Boshqaruv va Redaktor
        panel = QWidget()
        panel.setFixedWidth(380)
        panel_layout = QVBoxLayout(panel)
        gory_layout.addWidget(panel)

        # Foydalanish bo'yicha qisqa qo'llanma
        lbl_info = QLabel("📝 CAD QO'LLANMA:\n• Chap Klik: Nuqta/Chiziq chizish\n• O'ng Klik: Shaklni yopish (Min. 3 ta nuqta)\n• Segmentni tanlab Arc ga aylantiring!")
        lbl_info.setStyleSheet("color: #007bff; font-weight: bold; background-color: #111; padding: 10px; border-radius: 4px;")
        panel_layout.addWidget(lbl_info)

        btn_reset = QPushButton("🔄 CHIZMANI BOSHDAN BOSHLASH")
        btn_reset.setStyleSheet("background-color: #dc3545; color: white; font-weight: bold; padding: 8px;")
        btn_reset.clicked.connect(self.reset_canvas)
        panel_layout.addWidget(btn_reset)

        panel_layout.addWidget(QLabel("--------------------------------------------------"))

        # --- AUTOCAD SEGMENT EDITING PANEL ---
        panel_layout.addWidget(QLabel("📐 SEGMENTLAR VA ARC (YOY) REDAKTORI:"))
        self.lst_segments = QListWidget()
        self.lst_segments.setFixedHeight(150)
        self.lst_segments.itemSelectionChanged.connect(self.segment_tanlandi)
        panel_layout.addWidget(self.lst_segments)

        hbox_type = QHBoxLayout()
        hbox_type.addWidget(QLabel("Segment turi:"))
        self.cmb_seg_type = QComboBox()
        self.cmb_seg_type.addItems(["Line (To'g'ri chiziq)", "Arc (Yoy)"])
        self.cmb_seg_type.currentIndexChanged.connect(self.segment_turi_ozgardi)
        hbox_type.addWidget(self.cmb_seg_type)
        panel_layout.addLayout(hbox_type)

        self.lbl_curv = QLabel("Yoy bükülishi (Curvature): 0 mm")
        panel_layout.addWidget(self.lbl_curv)
        self.sld_curv = QSlider(Qt.Orientation.Horizontal)
        self.sld_curv.setRange(-200, 200)
        self.sld_curv.setValue(0)
        self.sld_curv.setEnabled(False)
        self.sld_curv.valueChanged.connect(self.curvature_ozgardi)
        panel_layout.addWidget(self.sld_curv)

        panel_layout.addWidget(QLabel("--------------------------------------------------"))

        # --- INTERFAYS PARAMETRLARI (NAQSH) ---
        panel_layout.addWidget(QLabel("🎨 TAYYOR NAQSHLAR KUTUBXONASI:"))
        self.cmb_naqsh = QComboBox()
        self.cmb_naqsh.addItems(["Kvadrat Grid", "Romb (Diamond)", "Islimiy Liniyalar"])
        self.cmb_naqsh.currentIndexChanged.connect(self.naqsh_turi_ozgardi)
        panel_layout.addWidget(self.cmb_naqsh)

        btn_tashqi = QPushButton("➕ TASHQI DXF NAQSH YUKLASH")
        btn_tashqi.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold;")
        btn_tashqi.clicked.connect(self.yuklash_tashqi)
        panel_layout.addWidget(btn_tashqi)

        self.lbl_scale = QLabel("Naqsh masshtabi: 1.0x")
        panel_layout.addWidget(self.lbl_scale)
        self.sld_scale = QSlider(Qt.Orientation.Horizontal)
        self.sld_scale.setRange(2, 50)
        self.sld_scale.setValue(10)
        self.sld_scale.valueChanged.connect(self.global_parametr_ozgardi)
        panel_layout.addWidget(self.sld_scale)

        self.lbl_x = QLabel("Cheksiz Surish X: 0 mm")
        panel_layout.addWidget(self.lbl_x)
        self.sld_x = QSlider(Qt.Orientation.Horizontal)
        self.sld_x.setRange(-2000, 2000)
        self.sld_x.setValue(0)
        self.sld_x.valueChanged.connect(self.global_parametr_ozgardi)
        panel_layout.addWidget(self.sld_x)

        self.lbl_y = QLabel("Cheksiz Surish Y: 0 mm")
        panel_layout.addWidget(self.lbl_y)
        self.sld_y = QSlider(Qt.Orientation.Horizontal)
        self.sld_y.setRange(-2000, 2000)
        self.sld_y.setValue(0)
        self.sld_y.valueChanged.connect(self.global_parametr_ozgardi)
        panel_layout.addWidget(self.sld_y)

        self.lbl_border = QLabel("Border qalinligi: 15 mm")
        panel_layout.addWidget(self.lbl_border)
        self.sld_border = QSlider(Qt.Orientation.Horizontal)
        self.sld_border.setRange(0, 80)
        self.sld_border.setValue(15)
        self.sld_border.valueChanged.connect(self.global_parametr_ozgardi)
        panel_layout.addWidget(self.sld_border)

        panel_layout.addStretch()

        btn_eksport = QPushButton("💾 DXF SIFATIDA EKSPORT (TAYYOR)")
        btn_eksport.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 14px; font-size: 14px;")
        btn_eksport.clicked.connect(self.eksport_dxf)
        panel_layout.addWidget(btn_eksport)

    def reset_canvas(self):
        self.engine.nodes = []
        self.engine.segment_types = []
        self.engine.curvatures = []
        self.engine.is_closed = False
        self.engine.tashqi_shakl = None
        self.engine.ichki_border = None
        self.engine.cut_lines = []
        self.lst_segments.clear()
        self.engine.update()

    def yangila_segment_royxati(self):
        """ Chizilgan liniyalarni AutoCAD kabi ro'yxatga olib chiqish """
        self.lst_segments.clear()
        n = len(self.engine.nodes)
        if n < 2: return
        
        loop_count = n if self.engine.is_closed else n - 1
        for i in range(loop_count):
            p1 = i + 1
            p2 = 1 if (i + 1) == n else i + 2
            turi = "Line" if self.engine.segment_types[i] == "line" else "Arc"
            self.lst_segments.addItem(f"Segment {p1} ➔ {p2} [{turi}]")

    def segment_tanlandi(self):
        row = self.lst_segments.currentRow()
        if row < 0 or row >= len(self.engine.segment_types):
            return
        
        turi = self.engine.segment_types[row]
        if turi == "line":
            self.cmb_seg_type.setCurrentIndex(0)
            self.sld_curv.setEnabled(False)
        else:
            self.cmb_seg_type.setCurrentIndex(1)
            self.sld_curv.setEnabled(True)
            
        self.sld_curv.setValue(int(self.engine.curvatures[row]))
        self.lbl_curv.setText(f"Yoy bükülishi: {self.engine.curvatures[row]} mm")

    def segment_turi_ozgardi(self):
        row = self.lst_segments.currentRow()
        if row < 0 or row >= len(self.engine.segment_types): return
        
        idx = self.cmb_seg_type.currentIndex()
        if idx == 0:
            self.engine.segment_types[row] = "line"
            self.engine.curvatures[row] = 0.0
            self.sld_curv.setEnabled(False)
        else:
            self.engine.segment_types[row] = "arc"
            self.sld_curv.setEnabled(True)
            
        self.engine.yangila_geometriya()
        self.engine.update()
        self.yangila_segment_royxati()
        self.lst_segments.setCurrentRow(row)

    def curvature_ozgardi(self):
        row = self.lst_segments.currentRow()
        if row < 0 or row >= len(self.engine.curvatures): return
        
        val = self.sld_curv.value()
        self.engine.curvatures[row] = float(val)
        self.lbl_curv.setText(f"Yoy bükülishi: {val} mm")
        
        self.engine.yangila_geometriya()
        self.engine.update()

    def naqsh_turi_ozgardi(self):
        self.engine.naqsh_turi = self.cmb_naqsh.currentText()
        self.engine.generatsiya_naqsh_kutubxonasi()
        self.engine.yangila_geometriya()
        self.engine.update()

    def yuklash_tashqi(self):
        fayl, _ = QFileDialog.getOpenFileName(self, "DXF Naqsh yuklash", "", "DXF (*.dxf)")
        if fayl:
            try:
                dxf_n = ezdxf.readfile(fayl)
                raw = []
                all_x, all_y = [], []
                for ent in dxf_n.modelspace().query('LINE LWPOLYLINE POLYLINE ARC CIRCLE'):
                    try:
                        p = ezdxf.path.make_path(ent)
                        pts = list(p.flattening(distance=0.1))
                        if len(pts) >= 2:
                            seg = [(v.x, v.y) for v in pts]
                            raw.append(seg)
                            for vx, vy in seg:
                                all_x.append(vx); all_y.append(vy)
                    except: continue
                if all_x:
                    cx, cy = (min(all_x)+max(all_x))/2, (min(all_y)+max(all_y))/2
                    self.engine.naqsh_w = max(1.0, max(all_x)-min(all_x))
                    self.engine.naqsh_h = max(1.0, max(all_y)-min(all_y))
                    self.engine.naqsh_raw_lines = [[(vx-cx, vy-cy) for vx, vy in s] for s in raw]
                    if self.cmb_naqsh.findText("Yuklangan DXF") == -1:
                        self.cmb_naqsh.addItem("Yuklangan DXF")
                    self.cmb_naqsh.setCurrentText("Yuklangan DXF")
            except Exception as e: print(e)

    def global_parametr_ozgardi(self):
        sc = self.sld_scale.value() / 10.0
        sx = float(self.sld_x.value())
        sy = float(self.sld_y.value())
        b_val = float(self.sld_border.value())

        self.lbl_scale.setText(f"Naqsh masshtabi: {sc:.1f}x")
        self.lbl_x.setText(f"Cheksiz Surish X: {sx} mm")
        self.lbl_y.setText(f"Cheksiz Surish Y: {sy} mm")
        self.lbl_border.setText(f"Border qalinligi: {b_val} mm")

        self.engine.scale_val = sc
        self.engine.shift_x = sx
        self.engine.shift_y = sy
        self.engine.border_mm = b_val

        self.engine.yangila_geometriya()
        self.engine.update()

    def eksport_dxf(self):
        if not self.engine.tashqi_shakl: return
        doc = ezdxf.new(dxfversion='R2010')
        ms = doc.modelspace()
        
        # Tashqi Native CAD shakl
        ms.add_lwpolyline(list(self.engine.tashqi_shakl.exterior.coords), close=True, dxfattribs={'color': 3})
        # Border
        if self.engine.ichki_border and not self.engine.ichki_border.is_empty:
            borders = [self.engine.ichki_border] if self.engine.ichki_border.geom_type == 'Polygon' else self.engine.ichki_border.geoms
            for b_poly in borders:
                ms.add_lwpolyline(list(b_poly.exterior.coords), close=True, dxfattribs={'color': 2})
        # Naqsh chiziqlari
        for line in self.engine.cut_lines:
            if len(line) >= 2:
                ms.add_lwpolyline(line, dxfattribs={'color': 7})

        filename = "Lazer_Custom_CAD_Polyline.dxf"
        doc.saveas(filename)
        print(f"🎉 AutoCAD mos keluvchi shakl saqlandi: {filename}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AutoCADLauncher()
    window.show()
    sys.exit(app.exec())