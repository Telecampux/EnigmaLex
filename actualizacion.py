"""Descarga e instala la versiÃ³n publicada del Anonimizador."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


RELEASE_API = "https://api.github.com/repos/Telecampux/EnigmaLex/releases/latest"
ARCHIVE_URL = "https://github.com/Telecampux/EnigmaLex/archive/refs/heads/main.zip"
MANIFEST = "Actualizador/archivos_programa.json"
PROTECTED = {".git", ".venv", "salida", "tessdata", "tmp", "capturas", "casos"}


def _safe_path(raw: str) -> Path:
    path = PurePosixPath(raw)
    if (not raw or path.is_absolute() or "\\" in raw or ":" in raw
            or any(part in ("", ".", "..") for part in path.parts)
            or path.parts[0].lower() in PROTECTED):
        raise ValueError(f"Ruta invÃ¡lida en el paquete: {raw}")
    return Path(*path.parts)


def _stage(archive: Path, staging: Path) -> list[Path]:
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        roots = {name.split("/", 1)[0] for name in names if "/" in name}
        if len(roots) != 1:
            raise ValueError("El paquete de GitHub no tiene una raÃ­z Ãºnica.")
        root = roots.pop()
        entries = json.loads(bundle.read(f"{root}/{MANIFEST}"))["archivos"]
        if not isinstance(entries, list) or not entries or len(entries) != len(set(entries)):
            raise ValueError("El listado de archivos del paquete es invÃ¡lido.")
        paths = [_safe_path(item) for item in entries]
        for relative in paths:
            info = bundle.getinfo(f"{root}/{relative.as_posix()}")
            if info.is_dir() or info.file_size > 100_000_000:
                raise ValueError(f"Archivo invÃ¡lido: {relative}")
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
        return paths


def installed_version(destination: Path) -> str:
    version_file = destination / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "Desconocida"
    except OSError:
        return "Anterior a 2026.11.18.1"


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


def update_direct(destination: Path, confirm=None) -> bool:
    destination = destination.resolve()
    if not (destination / "app.py").is_file() or not (destination / "Iniciar.bat").is_file():
        raise ValueError(f"No se encuentra el Anonimizador en {destination}")
    with tempfile.TemporaryDirectory(prefix="anonimizador_actualizacion_") as temporary:
        work = Path(temporary)
        archive = work / "version.zip"
        with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with zipfile.ZipFile(archive) as bundle:
            roots = {name.split("/", 1)[0] for name in bundle.namelist() if "/" in name}
            if len(roots) != 1:
                raise ValueError("El paquete de GitHub no tiene una raÃ­z Ãºnica.")
            try:
                available = bundle.read(f"{roots.pop()}/VERSION").decode("utf-8").strip()
            except KeyError as error:
                raise ValueError("La versiÃ³n de GitHub no estÃ¡ identificada.") from error
        if not available:
            raise ValueError("La versiÃ³n de GitHub estÃ¡ vacÃ­a.")
        if confirm is not None and not confirm(installed_version(destination), available):
            return False
        paths = _stage(archive, work / "nuevo")
        backup = work / "anterior"
        replaced: list[Path] = []
        created: list[Path] = []
        try:
            for relative in paths:
                target = destination / relative
                if target.exists():
                    if not target.is_file() or target.is_symlink():
                        raise ValueError(f"No se puede reemplazar: {target}")
                    old = backup / relative
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, old)
                    replaced.append(relative)
                else:
                    created.append(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(work / "nuevo" / relative, target)
        except Exception:
            for relative in replaced:
                shutil.copy2(backup / relative, destination / relative)
            for relative in created:
                (destination / relative).unlink(missing_ok=True)
            raise
    old_updater = destination / "Actualizador"
    if old_updater.is_dir() and not old_updater.is_symlink():
        shutil.rmtree(old_updater)
    return True
