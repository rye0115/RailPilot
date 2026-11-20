"""Build and verify the Windows GUI and update program files without replacing user configuration."""

import filecmp
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from railpilot import __version__


def build() -> None:
    dist = ROOT / "dist"
    work = ROOT / "build"
    for folder in (work, dist):
        if folder.resolve().parent != ROOT:
            raise ValueError(f"Unexpected release directory: {folder}")
        folder.mkdir(exist_ok=True)
    # Keep shortcuts from the November desktop bundle working on upgrade.
    old_install = dist / "RailPilot-20261120" / "RailPilotGUI"
    install = old_install if (old_install / "RailPilotGUI.exe").is_file() else dist / "RailPilotGUI"
    if not install.resolve().is_relative_to(dist.resolve()):
        raise ValueError("Unexpected install directory")
    archive = dist / f"RailPilotGUI-v{__version__}-win64.zip"
    subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-q"], cwd=ROOT, check=True)
    with TemporaryDirectory(dir=work, prefix="desktop-release-") as temp:
        stage = Path(temp).resolve()
        assert stage.parent == work
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--log-level", "WARN", "--onedir", "--windowed",
            "--collect-all", "playwright", "--name", "RailPilotGUI", "--specpath", str(stage),
            "--workpath", str(work / "desktop-cache"), "--distpath", str(stage / "bundle"),
            str(ROOT / "launcher_gui.py"),
        ], cwd=ROOT, check=True)
        bundle = stage / "bundle" / "RailPilotGUI"
        shutil.copy2(ROOT / "design" / "config.example.yaml", bundle / "config.yaml")
        guide = (ROOT / "design" / "README-desktop.txt").read_text(encoding="utf-8").replace("{version}", __version__)
        (bundle / "使用说明.txt").write_text(guide, encoding="utf-8-sig")
        env = dict(os.environ, RAILPILOT_DATA_DIR=str(stage / "smoke-data"))

        def smoke(directory):
            subprocess.run([str(directory / "RailPilotGUI.exe"), "--smoke"], cwd=directory,
                           env=env, timeout=40, check=True)

        smoke(bundle)
        files = sorted(p for p in bundle.rglob("*") if p.is_file())
        package = stage / archive.name
        with ZipFile(package, "x", ZIP_DEFLATED, compresslevel=6) as output:
            for p in files:
                output.write(p, p.relative_to(bundle.parent))
        with ZipFile(package) as output:
            if output.testzip() is not None:
                raise ValueError("便携包校验失败")
        applied = []
        try:
            for source in files:
                rel = source.relative_to(bundle)
                target = install / rel
                if not target.resolve().is_relative_to(install.resolve()):
                    raise ValueError("Unexpected file outside install directory")
                if target.exists() and (rel == Path("config.yaml") or filecmp.cmp(source, target, shallow=False)):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                backup = None
                if target.exists():
                    backup = stage / "rollback" / rel
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                os.replace(source, target)
                applied.append((target, backup))
            smoke(install)
            os.replace(package, archive)
        except BaseException:
            for target, backup in reversed(applied):
                if backup:
                    os.replace(backup, target)
                else:
                    target.unlink(missing_ok=True)
            raise
    print(install / "RailPilotGUI.exe")
    print(archive)


if __name__ == "__main__":
    build()
