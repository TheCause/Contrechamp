"""Paper scene: a declarative description of a paper-cut scene and its compiler.

See docs/fork/lot4-paper-scene.md (the format, why each element exists, the
three outputs) and skills/creative/paper-scene.md (how to write one).
"""

from lib.paper_scene.build import Build, compile_scene, load, scene_from, validate

__all__ = ["Build", "compile_scene", "load", "scene_from", "validate"]
