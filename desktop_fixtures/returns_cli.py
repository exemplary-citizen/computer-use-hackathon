"""CLI for launching, resetting, inspecting, and installing Atlas Returns Desk."""

from __future__ import annotations

import json
import logging
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import tyro
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from desktop_fixtures.returns_store import (
    load_returns_state,
    reset_returns_state,
    returns_state_path,
)

APP_NAME = "Atlas Returns Desk"
BUNDLE_IDENTIFIER = "ai.automation-foundry.atlas-returns-desk"
LAUNCH_SERVICES = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
)

logger = logging.getLogger(__name__)


def launch() -> None:
    """Launch Atlas Returns Desk using the default local state file."""
    from desktop_fixtures.atlas_returns import main as run_app

    run_app()


def reset(data_root: Path | None = None) -> None:
    """Restore deterministic Atlas demo data.

    Args:
        data_root: Optional fixture-data directory override.
    """
    path = returns_state_path(data_root)
    state = reset_returns_state(path)
    logger.info("Restored %d Atlas cases at %s", len(state.cases), path)


def dump(data_root: Path | None = None) -> None:
    """Print validated Atlas state as stable JSON.

    Args:
        data_root: Optional fixture-data directory override.
    """
    state = load_returns_state(returns_state_path(data_root))
    print(json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True))


def install(destination: Path = Path.home() / "Applications") -> None:
    """Install and register a Spotlight-launchable macOS app bundle.

    Args:
        destination: Directory that will contain ``Atlas Returns Desk.app``.
    """
    app_path = install_app(destination)
    logger.info("Installed %s", app_path)


def install_app(destination: Path) -> Path:
    """Create and register the macOS application bundle.

    Args:
        destination: Parent directory for the application bundle.

    Returns:
        Installed ``.app`` bundle path.
    """
    repository_root = Path(__file__).resolve().parents[1]
    python_executable = repository_root / ".venv" / "bin" / "python"
    if not python_executable.is_file():
        python_executable = Path(sys.executable)
    app_path = destination.expanduser().resolve() / f"{APP_NAME}.app"
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    if app_path.exists():
        shutil.rmtree(app_path)
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    launcher = macos / "atlas-returns-desk"
    launcher.write_text(
        "#!/bin/bash\n"
        f"cd {shlex_quote(str(repository_root))}\n"
        f"exec {shlex_quote(str(python_executable))} -m desktop_fixtures.atlas_returns\n",
        encoding="utf-8",
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    plist = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": APP_NAME,
        "CFBundleExecutable": launcher.name,
        "CFBundleIconFile": "AtlasReturns.icns",
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "7.4.18",
        "CFBundleVersion": "20260712",
        "LSApplicationCategoryType": "public.app-category.business",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
    }
    with (contents / "Info.plist").open("wb") as output:
        plistlib.dump(plist, output)
    _write_icon(resources / "AtlasReturns.icns")
    if LAUNCH_SERVICES.is_file():
        subprocess.run((str(LAUNCH_SERVICES), "-f", str(app_path)), check=False, capture_output=True)
    subprocess.run(("touch", str(app_path)), check=False, capture_output=True)
    return app_path


def shlex_quote(value: str) -> str:
    """Quote one shell argument without invoking a shell.

    Args:
        value: Raw argument.

    Returns:
        POSIX single-quoted argument.
    """
    return "'" + value.replace("'", "'\"'\"'") + "'"


def main() -> None:
    """Dispatch the Atlas fixture CLI."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    tyro.extras.subcommand_cli_from_dict(
        {
            "launch": launch,
            "reset": reset,
            "dump": dump,
            "install": install,
        }
    )


def _write_icon(destination: Path) -> None:
    icon_app = QApplication.instance()
    owns_app = icon_app is None
    if owns_app:
        icon_app = QApplication([])
    iconset = destination.parent / "AtlasReturns.iconset"
    iconset.mkdir()
    sizes = (16, 32, 128, 256, 512)
    for size in sizes:
        _render_icon(iconset / f"icon_{size}x{size}.png", size)
        _render_icon(iconset / f"icon_{size}x{size}@2x.png", size * 2)
    iconutil = shutil.which("iconutil")
    if iconutil:
        result = subprocess.run(
            (iconutil, "-c", "icns", str(iconset), "-o", str(destination)),
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            logger.warning("Could not build app icon; Spotlight will use the default icon")
    shutil.rmtree(iconset)
    if owns_app and icon_app is not None:
        icon_app.quit()


def _render_icon(path: Path, pixels: int) -> None:
    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32)
    image.fill(QColor("#18364a"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = max(2, pixels // 9)
    painter.fillRect(margin, margin, pixels - margin * 2, pixels - margin * 2, QColor("#d19a36"))
    painter.setPen(QColor("#18364a"))
    font = QFont("Helvetica Neue", max(8, pixels // 3), QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "AR")
    painter.end()
    image.save(str(path), "PNG")


if __name__ == "__main__":
    main()
