"""Install, replace and uninstall the packaged plugin with the pinned native CLI."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    script = Path(os.environ.get('HARNESS_SOURCE', '/h')) / 'scripts/install-builtin-qwenpaw-plugins.py'
    spec = importlib.util.spec_from_file_location('builtin_installer', script)
    installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)
    package = Path('/opt/agentteams/plugins/teamharness-qwenpaw.zip')
    with tempfile.TemporaryDirectory(prefix='native-plugin-lifecycle-') as temporary:
        root = Path(temporary); home = root / 'home'; home.mkdir()
        working = root / 'profile'; working.mkdir()
        operator_file = working / 'operator-note.txt'; operator_file.write_text('retain operator content')
        os.environ.update(HOME=str(home), QWENPAW_WORKING_DIR=str(working))
        installer._install(package, working)
        plugin = working / 'plugins/teamharness'
        assert json.loads((plugin / 'plugin.json').read_text())['id'] == 'teamharness'
        assert (plugin / 'teamharness/mcp/common/task_native.py').is_file()
        stale = plugin / 'removed-by-new-version.txt'; stale.write_text('old bundled asset')
        installer._install(package, working)
        assert not stale.exists(), 'native force upgrade retained a removed builtin asset'
        assert operator_file.read_text() == 'retain operator content'
        cli = str(Path(sys.executable).with_name('qwenpaw'))
        subprocess.run([cli, 'plugin', 'uninstall', 'teamharness'], input='y\n', text=True, check=True, timeout=45)
        assert not plugin.exists()
        assert operator_file.read_text() == 'retain operator content'
        print('PASS: native plugin install/replace/uninstall; stale builtin removed; operator content retained')


if __name__ == '__main__':
    main()
