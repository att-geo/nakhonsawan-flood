# -*- coding: utf-8 -*-
"""ArcGIS Pro Python Toolbox — Nakhon Sawan Flood Watch

1) Build Static Layers   : DEM/HAND/Sink/CN -> hex grid + ไฟล์ static ของเว็บ
1b) Build Upstream Basins: ลุ่มน้ำ 4 จังหวัดต้นน้ำที่ไหลเข้า นว. + จุดน้ำเข้า + เวลาเดินทาง + HAND แม่น้ำสายหลัก
2) Run Update (local)    : รัน pipeline near-realtime บนเครื่อง แล้วโหลดผลเป็น feature class ลงแผนที่
3) Publish to GitHub     : git add/commit/push โฟลเดอร์เว็บ (ต้องมี git + สิทธิ์ push)
"""
import os, sys, json, subprocess, importlib
import arcpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_DEFAULT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO_DEFAULT, "pipeline"))

CLASS_NAMES = ["ไม่ท่วม", "เฝ้าระวัง", "ท่วมขังเล็กน้อย", "ท่วมขังปานกลาง", "ท่วมสูง"]


def _p(name, disp, dtype, direction="Input", required=True, value=None):
    p = arcpy.Parameter(name=name, displayName=disp, datatype=dtype, direction=direction,
                        parameterType="Required" if required else "Optional")
    if value is not None:
        p.value = value
    return p


class Toolbox:
    def __init__(self):
        self.label = "Nakhon Sawan Flood Watch"
        self.alias = "nsflood"
        self.tools = [BuildStatic, BuildUpstream, RunUpdate, PublishGitHub]


class BuildStatic:
    def __init__(self):
        self.label = "1) Build Static Layers"
        self.description = "ดาวน์โหลด DEM/WorldCover/ขอบเขต แล้ววิเคราะห์ HAND, แอ่ง, CN ลง hex grid และ export ไฟล์ static ของเว็บ"
        self.canRunInBackground = False

    def getParameterInfo(self):
        try:
            proj = os.path.dirname(arcpy.mp.ArcGISProject("CURRENT").filePath)
        except Exception:  # noqa
            proj = None
        return [_p("project_dir", "Project folder (เก็บ source/ และ flood_static.gdb)", "DEFolder", value=proj),
                _p("repo_dir", "Web app folder (repo)", "DEFolder", value=REPO_DEFAULT),
                _p("hex_km2", "Hex size (km²)", "GPDouble", value=1.0),
                _p("stream_km2", "Stream threshold (km²) สำหรับ HAND", "GPDouble", value=5.0),
                _p("major_km2", "Major river threshold (km²) สำหรับน้ำล้นตลิ่ง", "GPDouble", value=1000.0)]

    def execute(self, params, messages):
        import build_static; importlib.reload(build_static)
        v = [p.valueAsText for p in params]
        build_static.build(v[0], v[1], float(v[2]), float(v[3]), float(v[4]), log=arcpy.AddMessage)


class BuildUpstream:
    def __init__(self):
        self.label = "1b) Build Upstream Basins (กำแพงเพชร พิจิตร พิษณุโลก เพชรบูรณ์)"
        self.description = ("DEM ภูมิภาค 90 ม. -> หาพื้นที่ใน 4 จังหวัดที่ไหลเข้านครสวรรค์, จุดน้ำเข้า, เวลาเดินทาง, zone สำหรับ "
                            "hydrograph และคำนวณ HAND แม่น้ำสายหลักใน นว. ใหม่โดยนับพื้นที่รับน้ำจากต้นน้ำ (ต้องรัน tool 1 ก่อน)")
        self.canRunInBackground = False

    def getParameterInfo(self):
        try:
            proj = os.path.dirname(arcpy.mp.ArcGISProject("CURRENT").filePath)
        except Exception:  # noqa
            proj = None
        return [_p("project_dir", "Project folder", "DEFolder", value=proj),
                _p("repo_dir", "Web app folder (repo)", "DEFolder", value=REPO_DEFAULT),
                _p("cell", "Regional cell size (m)", "GPDouble", value=90.0),
                _p("v_ms", "ความเร็วการไหลเฉลี่ย (ม./วิ) สำหรับเวลาเดินทาง", "GPDouble", value=0.7),
                _p("major_km2", "พื้นที่รับน้ำขั้นต่ำของแม่น้ำสายหลัก (km²)", "GPDouble", value=1000.0)]

    def execute(self, params, messages):
        import build_upstream; importlib.reload(build_upstream)
        v = [p.valueAsText for p in params]
        build_upstream.build_upstream(v[0], v[1], float(v[2]), float(v[3]), major_km2=float(v[4]), log=arcpy.AddMessage)


class RunUpdate:
    def __init__(self):
        self.label = "2) Run Update (local) + Load Results"
        self.description = "ดึงฝน/ระดับน้ำล่าสุด รันโมเดล เขียน data/live/*.json และสร้าง feature class hex_status"
        self.canRunInBackground = False

    def getParameterInfo(self):
        gdb = arcpy.env.workspace or ""
        return [_p("repo_dir", "Web app folder (repo)", "DEFolder", value=REPO_DEFAULT),
                _p("hex_fc", "Hex feature class (จาก Build Static)", "DEFeatureClass", required=False),
                _p("out_fc", "Output feature class", "DEFeatureClass", "Output", required=False,
                   value=os.path.join(gdb, "hex_status") if gdb.endswith(".gdb") else None),
                _p("add_map", "Add to current map", "GPBoolean", value=True)]

    def execute(self, params, messages):
        import run_update; importlib.reload(run_update)
        repo, hex_fc, out_fc, add = [p.valueAsText for p in params[:3]] + [params[3].value]
        meta = run_update.main(repo)
        arcpy.AddMessage(json.dumps(meta["summary"], ensure_ascii=False))
        if not (hex_fc and out_fc):
            return
        st = json.load(open(os.path.join(repo, "data", "live", "status.json")))
        arcpy.management.CopyFeatures(hex_fc, out_fc)
        fields = [("depth_cm", "SHORT"), ("cls", "SHORT"), ("cls_name", "TEXT"), ("source", "SHORT"),
                  ("hrs_flooded", "LONG"), ("hrs_remaining", "LONG"), ("rain24", "SHORT"), ("rain72", "SHORT"),
                  ("rain7d", "SHORT"), ("fc24", "SHORT"), ("fc72", "SHORT"), ("max_cls72", "SHORT"),
                  ("max_depth72", "SHORT")]
        for f, t in fields:
            arcpy.management.AddField(out_fc, f, t, field_length=32 if t == "TEXT" else None)
        keys = ["d", "c", None, "s", "h", "r", "p24", "p72", "p7d", "f24", "f72", "m72", "dm72"]
        with arcpy.da.UpdateCursor(out_fc, ["hid"] + [f for f, _ in fields]) as cur:
            for row in cur:
                i = row[0]
                vals = [CLASS_NAMES[st["c"][i]] if k is None else st[k][i] for k in keys]
                cur.updateRow([i] + vals)
        if add:
            try:
                m = arcpy.mp.ArcGISProject("CURRENT").activeMap
                lyr = m.addDataFromPath(out_fc)
                sym = lyr.symbology
                sym.updateRenderer("UniqueValueRenderer"); sym.renderer.fields = ["cls"]
                cols = [(255, 255, 255, 0), (191, 219, 254, 100), (96, 165, 250, 100), (37, 99, 235, 100), (30, 58, 138, 100)]
                for grp in sym.renderer.groups:
                    for it in grp.items:
                        k = int(it.values[0][0]); it.label = CLASS_NAMES[k]
                        it.symbol.color = {"RGB": list(cols[k])}; it.symbol.outlineColor = {"RGB": [0, 0, 0, 0]}
                lyr.symbology = sym
            except Exception as e:  # noqa
                arcpy.AddWarning(f"เพิ่มลงแผนที่ไม่ได้: {e}")


class PublishGitHub:
    def __init__(self):
        self.label = "3) Publish to GitHub"
        self.description = "commit + push โฟลเดอร์เว็บขึ้น GitHub (GitHub Actions จะ deploy Pages ให้อัตโนมัติ)"
        self.canRunInBackground = False

    def getParameterInfo(self):
        return [_p("repo_dir", "Web app folder (repo)", "DEFolder", value=REPO_DEFAULT),
                _p("msg", "Commit message", "GPString", value="update static layers from ArcGIS Pro")]

    def execute(self, params, messages):
        repo, msg = params[0].valueAsText, params[1].valueAsText
        for cmd in (["git", "add", "-A"], ["git", "commit", "-m", msg], ["git", "push"]):
            r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
            arcpy.AddMessage(" ".join(cmd) + "\n" + r.stdout + r.stderr)
