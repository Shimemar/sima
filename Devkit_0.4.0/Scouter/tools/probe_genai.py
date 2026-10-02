#!/usr/bin/env python3
"""pyneat.genai.GenerationRequest の属性確認(モデルはロードしない)"""
import glob, sys
for p in glob.glob("/usr/lib/python3*/dist-packages"):
    sys.path.insert(0, p)
import pyneat
r = pyneat.genai.GenerationRequest()
print([a for a in dir(r) if not a.startswith("_")])
r.system_prompt = "x"; r.prompt = "y"; r.max_new_tokens = 10
print("ok", r.system_prompt, r.max_new_tokens)
