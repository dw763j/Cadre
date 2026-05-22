#!/usr/bin/env python3
"""
Extract archives under workflow_detail_logs into unzipped_workflow_detail_logs.
"""

import tarfile
import gzip
import bz2
import lzma
import zipfile
import json
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from utils.utils import check_disk_space
# Run with: python -m get_info.unzip_workflow_detail_logs


def robust_zip_extract(zip_path: str, extract_dir: Path) -> tuple[bool, int, int]:
    """
    Robust ZIP extraction that recovers as much as possible from damaged archives.

    Args:
        zip_path: Path to the ZIP file
        extract_dir: Destination directory

    Returns:
        (success, extracted_count, total_files)
    """
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_file:
            # Read ZIP metadata
            file_list = zip_file.namelist()
            total_files = len(file_list)
            extracted_count = 0

            print(f"  - Found {total_files} files, starting extraction...")

            # Extract files one by one
            for file_name in file_list:
                try:
                    # Skip directories
                    if file_name.endswith('/'):
                        continue

                    # Build destination path
                    target_file = extract_dir / file_name
                    target_file.parent.mkdir(parents=True, exist_ok=True)

                    # Try extracting a single member
                    with zip_file.open(file_name) as source, open(target_file, 'wb') as target:
                        # Read in chunks to limit memory use
                        chunk_size = 8192
                        while True:
                            chunk = source.read(chunk_size)
                            if not chunk:
                                break
                            target.write(chunk)

                    extracted_count += 1
                    if extracted_count % 100 == 0:
                        print(f"    - Extracted {extracted_count}/{total_files} files")

                except Exception as e:
                    print(f"    - Failed to extract {file_name}: {e}")
                    # Continue with the next file
                    continue

            success = extracted_count > 0
            print(f"  - Done: {extracted_count}/{total_files} files extracted")
            return success, extracted_count, total_files

    except zipfile.BadZipFile as e:
        print(f"  - Corrupt ZIP: {e}")
        # Retry with a more lenient mode
        try:
            print("  - Retrying with lenient mode...")
            with zipfile.ZipFile(zip_path, 'r', allowZip64=True) as zip_file:
                # Try reading the file list
                try:
                    file_list = zip_file.namelist()
                except Exception as e:
                    print(f"  - Cannot read file list: {e}")
                    file_list = []

                if not file_list:
                    # If listing fails, probe common member names
                    print("  - Probing common file names...")
                    extracted_count = 0
                    total_files = 0

                    # Probe possible member names
                    for i in range(1000):  # probe up to 1000 candidate names
                        try:
                            file_name = f"file_{i}.txt"
                            with zip_file.open(file_name) as source:
                                target_file = extract_dir / file_name
                                target_file.parent.mkdir(parents=True, exist_ok=True)
                                with open(target_file, 'wb') as target:
                                    target.write(source.read())
                                extracted_count += 1
                        except Exception as e:
                            print(f"    - Cannot access {file_name}: {e}")
                            continue

                    return extracted_count > 0, extracted_count, total_files

                # Normal path
                return robust_zip_extract(zip_path, extract_dir)

        except Exception as e2:
            print(f"  - Lenient mode failed: {e2}")
            return False, 0, 0

    except Exception as e:
        print(f"  - Extraction error: {e}")
        return False, 0, 0


def detect_file_format(file_path: str) -> tuple[str, str]:
    """
    Detect the archive format.
    Returns: (format_type, description)
    """
    try:
        with open(file_path, 'rb') as f:
            # Read magic bytes
            header = f.read(8)

        # Match known compression signatures
        if header.startswith(b'\x1f\x8b'):
            return 'gzip', 'gzip archive'
        elif header.startswith(b'BZ'):
            return 'bzip2', 'bzip2 archive'
        elif header.startswith(b'\xfd7zXZ\x00'):
            return 'xz', 'xz archive'
        elif header.startswith(b'PK'):
            return 'zip', 'ZIP archive'
        elif header.startswith(b'ustar') or header.startswith(b'\x00\x00\x00'):
            return 'tar', 'tar archive'
        else:
            return 'unknown', 'unknown format'

    except Exception as e:
        return 'error', f'detection failed: {e}'


def get_archive_files(source_dir: str) -> list[str]:
    """List archive files in a directory."""
    archive_files = []
    source_path = Path(source_dir)

    if not source_path.exists():
        print(f"Source directory does not exist: {source_dir}")
        return archive_files

    # Supported extensions
    supported_extensions = {'.tar', '.gz', '.bz2', '.xz', '.zip', '.tgz', '.tbz2', '.txz'}

    for file in source_path.iterdir():
        if file.is_file() and file.suffix in supported_extensions:
            archive_files.append(str(file))

    return archive_files


def extract_archive_file(archive_path: str, target_dir: str) -> tuple:
    """Extract one archive into the target directory."""
    try:
        archive_file = Path(archive_path)
        file_name = archive_file.stem  # stem without extension

        # Detect format
        format_type, format_desc = detect_file_format(archive_path)
        # print(f"Detected format: {file_name} -> {format_desc}")

        # Check disk space
        if not check_disk_space(target_dir, min_bytes=1_000_000_000):
            print(f"⚠ Insufficient disk space, skipping: {file_name}")
            return False, file_name, "disk_space_insufficient", format_type

        # Create destination directory
        extract_dir = Path(target_dir) / file_name
        extract_dir.mkdir(parents=True, exist_ok=True)

        # print(f"Extracting: {file_name} ({format_type})")

        # Choose extractor by format
        if format_type == 'tar':
            with tarfile.open(archive_path, 'r') as tar:
                tar.extractall(path=extract_dir)
        elif format_type == 'gzip':
            with gzip.open(archive_path, 'rb') as gz:
                # .tar.gz: decompress gzip then untar
                if archive_path.endswith('.tar.gz') or archive_path.endswith('.tgz'):
                    with tarfile.open(fileobj=gz, mode='r:*') as tar:
                        tar.extractall(path=extract_dir)
                else:
                    # Plain gzip file
                    output_file = extract_dir / file_name
                    with open(output_file, 'wb') as out:
                        out.write(gz.read())
        elif format_type == 'bzip2':
            with bz2.open(archive_path, 'rb') as bz:
                if archive_path.endswith('.tar.bz2') or archive_path.endswith('.tbz2'):
                    with tarfile.open(fileobj=bz, mode='r:*') as tar:
                        tar.extractall(path=extract_dir)
                else:
                    output_file = extract_dir / file_name
                    with open(output_file, 'wb') as out:
                        out.write(bz.read())
        elif format_type == 'xz':
            with lzma.open(archive_path, 'rb') as xz:
                if archive_path.endswith('.tar.xz') or archive_path.endswith('.txz'):
                    with tarfile.open(fileobj=xz, mode='r:*') as tar:
                        tar.extractall(path=extract_dir)
                else:
                    output_file = extract_dir / file_name
                    with open(output_file, 'wb') as out:
                        out.write(xz.read())
        elif format_type == 'zip':
            print(f"Extracting ZIP: {file_name}")
            success, extracted_count, total_files = robust_zip_extract(archive_path, extract_dir)
            if not success:
                print("  - ZIP extraction failed or archive is corrupt")
                return False, file_name, "zip_extraction_failed", format_type
            print(f"  - ZIP done: {extracted_count}/{total_files} files")
        else:
            print(f"✗ Unsupported format: {file_name} ({format_type})")
            return False, file_name, f"unsupported_format_{format_type}", format_type

        # print(f"✓ Extracted: {file_name}")
        return True, file_name, "success", format_type

    except Exception as e:
        print(f"✗ Extraction failed {archive_file.name}: {e}")
        return False, file_name, str(e), "unknown"


def save_extraction_status(target_dir: str, extraction_results: list[dict], total_files: int):
    """Persist extraction status to JSON."""
    status_file = Path(target_dir) / "extraction_status.json"

    status_data = {
        "extraction_info": {
            "timestamp": datetime.now().isoformat(),
            "total_files": total_files,
            "successful_extractions": len([r for r in extraction_results if r["status"] == "success"]),
            "failed_extractions": len([r for r in extraction_results if r["status"] != "success"]),
            "disk_space_insufficient": len([r for r in extraction_results if r["reason"] == "disk_space_insufficient"]),
            "unsupported_format": len([r for r in extraction_results if "unsupported_format" in r["reason"]]),
            "other_errors": len([r for r in extraction_results if r["status"] != "success" \
                            and r["reason"] not in ["disk_space_insufficient"] \
                            and "unsupported_format" not in r["reason"]])
        },
        "file_results": extraction_results
    }

    try:
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(status_data, f, indent=2, ensure_ascii=False)
        print(f"✓ Extraction status saved to: {status_file}")
    except Exception as e:
        print(f"✗ Failed to save status file: {e}")


def unzip_workflow_detail_logs(input_dir: str="results/workflow_logs", output_dir: str="results/unzipped_workflow_logs", max_workers: int = 8):

    # Collect archives
    archive_files = get_archive_files(input_dir)

    if not archive_files:
        logger.warning("No archive files found")
        return

    logger.info(f"Found {len(archive_files)} archives in {input_dir}")

    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Extract concurrently
    success_count = 0
    failed_files = []
    disk_space_insufficient_count = 0
    extraction_results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit futures
        future_to_file = {
            executor.submit(extract_archive_file, archive_file, output_dir): archive_file
            for archive_file in archive_files
        }

        # Drain futures
        for future in as_completed(future_to_file):
            success, file_name, reason, format_type = future.result()

            # Record result
            result = {
                "file_name": file_name,
                "original_file": str(future_to_file[future]),
                "status": "success" if success else "failed",
                "reason": reason,
                "detected_format": format_type,
                "timestamp": datetime.now().isoformat()
            }
            extraction_results.append(result)

            if success:
                success_count += 1
            else:
                failed_files.append(file_name)
                if reason == "disk_space_insufficient":
                    disk_space_insufficient_count += 1

    # Save status
    save_extraction_status(output_dir, extraction_results, len(archive_files))

    logger.info(
        f"Extraction finished for {input_dir}: {success_count}/{len(archive_files)} succeeded, "
        f"{len(failed_files)} failed; output {output_dir}"
    )

    if disk_space_insufficient_count > 0 and logger:
        logger.warning(f"Skipped due to low disk space: {disk_space_insufficient_count}")

    if failed_files and logger:
        logger.warning(f"Failed archives: {len(failed_files)}")
        for failed_file in failed_files:
            logger.warning(f"  - {failed_file}")


if __name__ == "__main__":
    logger.add("logs/unzip_workflow_detail_logs.log")
    unzip_workflow_detail_logs("results/workflow_logs", "results/unzipped_workflow_logs", 8)
