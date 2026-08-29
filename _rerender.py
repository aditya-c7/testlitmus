import json
import sys

sys.path.insert(0, r"c:/Users/shash/OneDrive/Desktop/test/precedent")

import playbook as playbook_module

stored = json.loads(playbook_module.PLAYBOOK_DIR.joinpath("playbook.json").read_text(encoding="utf-8"))
playbook_module.PLAYBOOK_DIR.joinpath("PLAYBOOK.md").write_text(
    playbook_module.render_markdown(stored), encoding="utf-8"
)
print("re-rendered PLAYBOOK.md from stored playbook.json", flush=True)