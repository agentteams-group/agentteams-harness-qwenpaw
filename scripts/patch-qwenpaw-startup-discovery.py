"""Freeze startup discovery before readiness admits managed configuration."""
from importlib.metadata import distribution


def apply_patch(source):
    snapshot = "            startup_provider_ids = provider_manager.startup_sync_provider_ids()\n"
    marker = "            def _mark_core_agents_ready(_results: dict[str, bool]) -> None:\n"
    if snapshot + marker in source:
        return source
    if source.count(snapshot) != 1 or source.count(marker) != 1:
        raise RuntimeError("Unexpected QwenPaw startup discovery source")
    return source.replace(snapshot, "").replace(marker, snapshot + marker)


def main():
    package = distribution("qwenpaw")
    # Older pinned SDKs do not use the background startup discovery phase.
    if package.version in ("2.2.1", "2.2.2b4"):
        return
    if package.version != "2.2.2.post1":
        raise RuntimeError("Review startup discovery for QwenPaw " + package.version)
    path = package.locate_file("qwenpaw/app/_app.py")
    path.write_text(apply_patch(path.read_text(encoding="utf-8")), encoding="utf-8")


if __name__ == "__main__":
    main()
