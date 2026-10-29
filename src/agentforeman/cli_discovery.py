"""Find installed clients without changing PATH or executing shell shims."""
import os
from pathlib import Path
import shutil
import sys


def windows_path():
    paths = [os.environ.get('PATH', '')]
    if sys.platform == 'win32':
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, 'Environment'),
                              (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        value, _ = winreg.QueryValueEx(handle, 'Path')
                    if isinstance(value, str):
                        paths.append(os.path.expandvars(value))
                except OSError:
                    pass
        except ImportError:
            pass
    return os.pathsep.join(paths)


def find_client(binary):
    found = shutil.which(binary)
    if found or sys.platform != 'win32':
        return found
    found = shutil.which(binary, path=windows_path())
    if found:
        return found
    home = Path.home()
    appdata = Path(os.environ.get('APPDATA', str(home/'AppData/Roaming')))
    local = Path(os.environ.get('LOCALAPPDATA', str(home/'AppData/Local')))
    locations = [appdata/'npm', home/'.local/bin', home/'.bun/bin', home/'scoop/shims']
    if binary == 'opencode':
        locations += [home/'.opencode/bin', local/'opencode/bin', home/'scoop/apps/opencode/current']
    if binary == 'opencode':
        # The desktop's opencode.exe may be the GUI, not the CLI sidecar.
        for folder in (local/'Programs/OpenCode', Path(os.environ.get('ProgramFiles', str(home/'Programs')))/'OpenCode'):
            sidecar=folder/'opencode-cli.exe'
            if sidecar.is_file():return str(sidecar)
    for folder in locations:
        for suffix in ('.exe', '.cmd'):
            path = folder/(binary+suffix)
            if path.is_file():
                return str(path)
    return None
