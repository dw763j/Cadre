#!/usr/bin/env python3
"""
Robust ZIP extractor for incomplete or corrupted downloads.
"""


import zipfile
import argparse
import subprocess
from pathlib import Path


class RobustZipExtractor:
    """Robust ZIP archive extractor."""
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.extracted_files = []
        self.failed_files = []
        
    def log(self, message: str):
        """Log a message."""
        if self.verbose:
            print(message)

    def _write_member(self, zip_file: zipfile.ZipFile, file_name: str, extract_dir: Path) -> bool:
        target_file = extract_dir / file_name
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with zip_file.open(file_name) as source, open(target_file, "wb") as target:
            while chunk := source.read(8192):
                target.write(chunk)
        self.extracted_files.append(str(target_file))
        return True
    
    def extract_zip_robust(self, zip_path: str | Path, extract_dir: str | Path) -> tuple[bool, int, int]:
        """
        Try multiple strategies to extract a ZIP file
        
        Args:
            zip_path: Path to the ZIP file
            extract_dir: Destination directory
            
        Returns:
            (success, extracted_count, total_files)
        """
        zip_path_obj = Path(zip_path)
        extract_dir_obj = Path(extract_dir)
        
        if not zip_path_obj.exists():
            self.log(f"Error: ZIP not found: {zip_path_obj}")
            return False, 0, 0
            
        # Create output directory
        extract_dir_obj.mkdir(parents=True, exist_ok=True)
        
        self.log(f"Processing ZIP: {zip_path_obj.name}")
        self.log(f"Destination: {extract_dir_obj}")
        
        for strategy in (
            self._try_standard_extraction,
            self._try_lenient_extraction,
            self._try_file_by_file_extraction,
            self._try_external_tools,
        ):
            result = strategy(zip_path_obj, extract_dir_obj)
            if result[0]:
                return result
            
        self.log("All strategies failed")
        return False, 0, 0
    
    def _try_standard_extraction(self, zip_path: Path, extract_dir: Path) -> tuple[bool, int, int]:
        """Strategy 1: standard ZIP extraction."""
        try:
            self.log("Strategy 1: standard extraction...")
            with zipfile.ZipFile(zip_path, "r") as zip_file:
                file_list = zip_file.namelist()
                total_files = len(file_list)
                extracted_count = 0
                
                self.log(f"Found {total_files} files")
                
                for file_name in file_list:
                    try:
                        if file_name.endswith("/"):
                            continue
                        extracted_count += int(self._write_member(zip_file, file_name, extract_dir))
                        
                        if extracted_count % 100 == 0:
                            self.log(f"  Extracted {extracted_count}/{total_files} files")
                            
                    except Exception as e:
                        self.log(f"  Failed to extract {file_name}: {e}")
                        self.failed_files.append(file_name)
                        continue
                
                success = extracted_count > 0
                self.log(f"Standard extraction: {extracted_count}/{total_files} succeeded")
                return success, extracted_count, total_files
                
        except Exception as e:
            self.log(f"Standard extraction failed: {e}")
            return False, 0, 0
    
    def _try_lenient_extraction(self, zip_path: Path, extract_dir: Path) -> tuple[bool, int, int]:
        """Strategy 2: lenient mode."""
        try:
            self.log("Strategy 2: lenient mode...")
            with zipfile.ZipFile(zip_path, "r", allowZip64=True) as zip_file:
                # Try reading the file list
                try:
                    file_list = zip_file.namelist()
                except Exception:
                    self.log("  Cannot read file list")
                    return False, 0, 0
                
                if not file_list:
                    return False, 0, 0
                
                # Retry with lenient mode
                return self._try_standard_extraction(zip_path, extract_dir)
                
        except Exception as e:
            self.log(f"Lenient mode failed: {e}")
            return False, 0, 0
    
    def _try_file_by_file_extraction(self, zip_path: Path, extract_dir: Path) -> tuple[bool, int, int]:
        """Strategy 3: probe members individually."""
        try:
            self.log("Strategy 3: file-by-file extraction...")
            
            with zipfile.ZipFile(zip_path, "r") as zip_file:
                extracted_count = 0
                extensions = ["", ".txt", ".log", ".json", ".xml", ".csv", ".dat"]
                for i in range(10000):  # probe more candidates
                    for ext in extensions:
                        file_name = f"file_{i}{ext}"
                        try:
                            extracted_count += int(self._write_member(zip_file, file_name, extract_dir))
                            if extracted_count % 100 == 0:
                                self.log(f"  Extracted {extracted_count} files")
                        except KeyError:
                            continue
                
                common_names = ["README", "config", "settings", "data", "output", "result", "log"]
                for base_name in common_names:
                    for ext in extensions:
                        file_name = f"{base_name}{ext}"
                        try:
                            extracted_count += int(self._write_member(zip_file, file_name, extract_dir))
                        except KeyError:
                            continue
                
                success = extracted_count > 0
                self.log(f"File-by-file extraction: {extracted_count} files")
                return success, extracted_count, 0
                
        except Exception as e:
            self.log(f"File-by-file extraction failed: {e}")
            return False, 0, 0
    
    def _try_external_tools(self, zip_path: Path, extract_dir: Path) -> tuple[bool, int, int]:
        """Strategy 4: external unzip tool."""
        self.log("Strategy 4: external tool...")
        
        # Try system unzip when available
        try:
            result = subprocess.run([
                'unzip', '-q', '-o', str(zip_path), '-d', str(extract_dir)
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                # Count extracted files
                extracted_files = list(extract_dir.rglob('*'))
                extracted_files = [f for f in extracted_files if f.is_file()]
                
                self.log(f"unzip succeeded: {len(extracted_files)} files")
                return True, len(extracted_files), 0
            else:
                self.log(f"unzip failed: {result.stderr}")
                
        except Exception as e:
            self.log(f"External tool failed: {e}")
        
        return False, 0, 0
    
    def get_summary(self) -> dict:
        """Return extraction summary."""
        return {
            'extracted_files': self.extracted_files,
            'failed_files': self.failed_files,
            'total_extracted': len(self.extracted_files),
            'total_failed': len(self.failed_files)
        }


def main():
    parser = argparse.ArgumentParser(description="Robust ZIP extraction tool")
    parser.add_argument("zip_file", help="Path to the ZIP file")
    parser.add_argument("-o", "--output", help="Output directory", default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    zip_path = args.zip_file
    if args.output:
        extract_dir = args.output
    else:
        # Default output dir from ZIP stem
        extract_dir = Path(zip_path).stem
    
    extractor = RobustZipExtractor(verbose=args.verbose)
    
    print(f"ZIP file: {zip_path}")
    print(f"Output directory: {extract_dir}")
    print("-" * 50)
    
    success, extracted_count, total_files = extractor.extract_zip_robust(zip_path, extract_dir)
    
    print("-" * 50)
    if success:
        print(f"✓ Success: extracted {extracted_count} files")
        if total_files > 0:
            print(f"Success rate: {extracted_count}/{total_files} ({extracted_count/total_files*100:.1f}%)")
    else:
        print("✗ Extraction failed")
    
    # Print summary
    summary = extractor.get_summary()
    if summary["extracted_files"]:
        print("\nExtracted files:")
        for file_path in summary["extracted_files"][:10]:  # show first 10 only
            print(f"  - {file_path}")
        if len(summary["extracted_files"]) > 10:
            print(f"  ... and {len(summary['extracted_files']) - 10} more files")
    
    if summary["failed_files"]:
        print("\nFailed members:")
        for file_name in summary["failed_files"][:10]:  # show first 10 only
            print(f"  - {file_name}")
        if len(summary["failed_files"]) > 10:
            print(f"  ... and {len(summary['failed_files']) - 10} more")


if __name__ == "__main__":
    main()
