"""Descarga e instala la versiÃ³n publicada del Anonimizador."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path


RELEASE_API = "https://api.github.com/repos/Telecampux/EnigmaLex/releases/latest"


def installed_version(destination: Path) -> str:
    version_file = destination / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "Desconocida"
    except OSError:
        return "Desconocida"


@dataclass(frozen=True)
class Release:
    version: str
    newer: bool | None
    installer_url: str | None = None
    installer_hash: str | None = None


def compare_release(installed: str) -> Release:
    request = urllib.request.Request(RELEASE_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "EnigmaLex",
    })
    with urllib.request.urlopen(request, timeout=8) as response:
        release = json.load(response)
    tag = release.get("tag_name", "") if isinstance(release, dict) else ""
    if not isinstance(tag, str) or not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){3}", tag):
        raise ValueError("GitHub no devolvió una versión válida.")
    available = tag.removeprefix("v")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", installed):
        return Release(available, None)
    if tuple(map(int, available.split("."))) <= tuple(map(int, installed.split("."))):
        return Release(available, False)
    name = f"EnigmaLex-Setup-{available}.exe"
    url = f"https://github.com/Telecampux/EnigmaLex/releases/download/{tag}/{name}"
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("name") != name or asset.get("browser_download_url") != url:
            continue
        digest = asset.get("digest")
        if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            return Release(available, True, url, digest.split(":", 1)[1].lower())
    return Release(available, True)


def download_installer(release: Release, folder: Path) -> Path:
    if not release.newer or not release.installer_url or not release.installer_hash:
        raise ValueError("La versión no tiene un instalador verificable.")
    request = urllib.request.Request(release.installer_url, headers={"User-Agent": "EnigmaLex"})
    installer = folder / f"EnigmaLex-Setup-{release.version}.exe"
    with urllib.request.urlopen(request, timeout=30) as response, installer.open("xb") as output:
        shutil.copyfileobj(response, output)
    with installer.open("rb") as package:
        checksum = hashlib.file_digest(package, "sha256").hexdigest()
    if checksum != release.installer_hash:
        raise ValueError("El archivo descargado no coincide con la suma SHA-256 publicada.")
    return installer
