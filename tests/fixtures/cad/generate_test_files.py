# -*- coding: utf-8 -*-
"""生成测试用 DXF 文件（运行一次即可）。"""
import ezdxf


def create_simple():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    doc.layers.add("Wall", color=1)
    doc.layers.add("Door", color=3)
    doc.layers.add("Window", color=5)
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "Wall"})
    msp.add_line((100, 0), (100, 80), dxfattribs={"layer": "Wall"})
    msp.add_line((100, 80), (0, 80), dxfattribs={"layer": "Wall"})
    msp.add_line((0, 80), (0, 0), dxfattribs={"layer": "Wall"})
    msp.add_arc((50, 0), radius=20, start_angle=0, end_angle=90,
                dxfattribs={"layer": "Door"})
    msp.add_lwpolyline([(20, 80), (40, 80)], dxfattribs={"layer": "Window"})
    msp.add_text("Room A", dxfattribs={"layer": "Wall", "height": 5,
                 "insert": (50, 40)})
    doc.saveas("tests/fixtures/cad/simple.dxf")


def create_complex():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for i, name in enumerate(["Architecture", "Structure", "MEP", "Annotation", "DIM"]):
        doc.layers.add(name, color=i + 1)
    msp.add_line((0, 0), (200, 0), dxfattribs={"layer": "Architecture"})
    msp.add_circle((100, 50), radius=30, dxfattribs={"layer": "Structure"})
    msp.add_lwpolyline([(0, 0), (200, 0), (200, 100), (0, 100)], close=True,
                       dxfattribs={"layer": "Architecture"})
    msp.add_mtext("Building A", dxfattribs={"layer": "Annotation",
                   "insert": (50, 50)})
    msp.add_text("Note", dxfattribs={"layer": "Annotation", "height": 3,
                 "insert": (10, 10)})
    for i in range(50):
        msp.add_line((i * 4, 0), (i * 4, 100), dxfattribs={"layer": "MEP"})
    doc.saveas("tests/fixtures/cad/complex.dxf")


if __name__ == "__main__":
    create_simple()
    create_complex()
    print("fixtures generated")