"""Apply the explicit native channel allowlist before optional SDK imports."""
from pathlib import Path
import sys
import sysconfig

path = (
    Path(sys.argv[1]) if len(sys.argv) > 1
    else Path(sysconfig.get_path("purelib")) / "qwenpaw/app/channels/registry.py"
)
source = path.read_text()
old = '''    out: dict[str, type[BaseChannel]] = {}
    for key, (module_name, class_name) in _BUILTIN_SPECS.items():'''
new = '''    from ...constant import EnvVarLoader

    specs = _BUILTIN_SPECS
    raw_enabled = EnvVarLoader.get_str("QWENPAW_ENABLED_CHANNELS", "").strip()
    if raw_enabled:
        enabled = {key.strip() for key in raw_enabled.split(",") if key.strip()}
        selected = {key: value for key, value in specs.items() if key in enabled}
        # Preserve the SDK's fallback for an unknown-only allowlist. AT's
        # explicit profile includes console and its supported DingTalk channel.
        if selected:
            specs = selected
    out: dict[str, type[BaseChannel]] = {}
    for key, (module_name, class_name) in specs.items():'''
if new in source:
    sys.exit(0)
if source.count(old) != 1:
    raise RuntimeError("Native registry changed; do not patch an unknown SDK")
path.write_text(source.replace(old, new))
