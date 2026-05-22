#!/usr/bin/env python3
"""
Extract damaged or incomplete ZIP archives.
"""

import zipfile
import subprocess
from pathlib import Path


def extract_damaged_zip(zip_path: str | Path, extract_dir: str | Path | None = None):
    zip_path = Path(zip_path)
    if not zip_path.exists():
        print(f"Error: file not found: {zip_path}")
        return
    extract_path = Path(extract_dir) if extract_dir is not None else Path(zip_path.stem)
    extract_path.mkdir(exist_ok=True)
    print(f"Processing: {zip_path.name}")
    print(f"Extract to: {extract_path}")
    print("-" * 40)

    for method_name, method_func in [
        ("standard mode", _try_standard),
        ("lenient mode", _try_lenient),
        ("file-by-file", _try_file_by_file),
        ("external tool", _try_external_tool),
    ]:
        print(f"\nTrying {method_name}...")
        try:
            success, count = method_func(zip_path, extract_path)
            if success:
                print(f"✓ {method_name} succeeded: extracted {count} files")
                return
            print(f"✗ {method_name} failed")
        except Exception as e:
            print(f"✗ {method_name} error: {e}")
    print("\nAll methods failed")


def _extract_members(zip_file: zipfile.ZipFile, extract_path: Path, members: list[str]) -> int:
    extracted_count = 0
    for file_name in members:
        if file_name.endswith("/"):
            continue
        try:
            target_file = extract_path / file_name
            target_file.parent.mkdir(parents=True, exist_ok=True)
            with zip_file.open(file_name) as source, open(target_file, "wb") as target:
                target.write(source.read())
            extracted_count += 1
        except Exception as e:
            print(f"  Skipped {file_name}: {e}")
    return extracted_count


def _extract_with_zip(zip_path: Path, extract_path: Path, *, allow_zip64: bool) -> tuple[bool, int]:
    with zipfile.ZipFile(zip_path, "r", allowZip64=allow_zip64) as zip_file:
        extracted_count = _extract_members(zip_file, extract_path, zip_file.namelist())
    return extracted_count > 0, extracted_count


def _try_standard(zip_path: Path, extract_path: Path):
    return _extract_with_zip(zip_path, extract_path, allow_zip64=False)


def _try_lenient(zip_path: Path, extract_path: Path):
    return _extract_with_zip(zip_path, extract_path, allow_zip64=True)


def _try_file_by_file(zip_path: Path, extract_path: Path):
    with zipfile.ZipFile(zip_path, "r") as zip_file:
        extracted_count = 0
        for i in range(1000):
            for ext in ["", ".txt", ".log", ".json", ".xml", ".csv"]:
                file_name = f"file_{i}{ext}"
                try:
                    extracted_count += _extract_members(zip_file, extract_path, [file_name])
                except KeyError:
                    continue
    return extracted_count > 0, extracted_count


def _try_external_tool(zip_path: Path, extract_path: Path):
    try:
        result = subprocess.run(
            ["unzip", "-q", "-o", str(zip_path), "-d", str(extract_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False, 0
        files = [f for f in extract_path.rglob("*") if f.is_file()]
        return True, len(files)
    except Exception:
        return False, 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2 or sys.argv[1] in ["-h", "--help", "help"]:
        print("Usage: python extract_damaged_zip.py <zip_path> [output_dir]")
        print("Example: python extract_damaged_zip.py damaged_file.zip")
        print("Example: python extract_damaged_zip.py damaged_file.zip output_dir")
        sys.exit(1)

    zip_path_arg = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    extract_damaged_zip(zip_path_arg, output_dir)
