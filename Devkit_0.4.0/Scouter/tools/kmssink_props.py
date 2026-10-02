#!/usr/bin/env python3
"""kmssink のプロパティ一覧(モード変更系の確認)"""
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
Gst.init(None)
k = Gst.ElementFactory.make("kmssink")
for p in k.list_properties():
    print(p.name, "=", k.get_property(p.name) if p.flags & 1 else "(write-only)", "|", p.blurb[:90])
