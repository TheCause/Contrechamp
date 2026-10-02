"""sfx_synth is discovered by the registry and declares a complete local contract."""
from tools.audio.sfx_synth import KIND_PARAMS, SfxSynth
from tools.tool_registry import registry

KINDS = {"click", "thud", "metal", "bell", "marimba", "motor", "swish", "sparkle", "pad"}


def test_registry_discovers_sfx_synth():
    registry.discover()
    assert isinstance(registry.get("sfx_synth"), SfxSynth)
    assert "sfx_synth" in [t.name for t in registry.get_by_capability("sound_effects")]


def test_contract_fields():
    info = SfxSynth().get_info()
    assert info["name"] == "sfx_synth"
    assert info["capability"] == "sound_effects"
    assert info["runtime"] == "local"
    assert info["determinism"] == "seeded"
    assert info["status"] == "available"
    assert info["resource_profile"]["network_required"] is False
    assert info["input_schema"]["required"] == ["events", "duration_seconds"]
    item = info["input_schema"]["properties"]["events"]["items"]
    assert set(item["properties"]["kind"]["enum"]) == KINDS
    assert set(KIND_PARAMS) == KINDS
    assert SfxSynth().estimate_cost({}) == 0.0
