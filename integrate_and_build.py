# integrate_and_build.py
# This script combines your Python code and builds the binary in one step

import os
import sys
import subprocess
import shutil
import tempfile

# Create a temporary directory to work in
temp_dir = tempfile.mkdtemp()
print(f"Working in temporary directory: {temp_dir}")

# Check if Python and pip are available
def check_python():
    try:
        subprocess.run([sys.executable, "--version"], check=True, capture_output=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False

# Install a package if needed
def install_package(package):
    print(f"Installing {package}...")
    subprocess.run([sys.executable, "-m", "pip", "install", package], check=True)

# Create the combined script
def create_combined_script():
    combined_script = os.path.join(temp_dir, "combined.py")
    print(f"Creating combined script at {combined_script}")
    
    script_content = '''
import sys
import os
import subprocess
import importlib.util
import importlib.machinery

def import_module_from_file(module_name, file_path):
    """Import a module from a file path."""
    loader = importlib.machinery.SourceFileLoader(module_name, file_path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

def run_main_with_args(args):
    """Run the main.py module with command line arguments."""
    # Save original sys.argv
    original_argv = sys.argv.copy()
    
    try:
        # Replace sys.argv with our arguments
        sys.argv = ["main.py"] + args
        
        # Find main.py in the same directory as the executable
        if getattr(sys, 'frozen', False):
            # Running as executable
            main_path = os.path.join(os.path.dirname(sys.executable), "main.py")
        else:
            # Running as script
            main_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        
        # Import and run main.py
        main_module = import_module_from_file("main", main_path)
        
        # If main has a main() function, call it, otherwise assume it runs on import
        if hasattr(main_module, "main"):
            return main_module.main()
        return 0
        
    except Exception as e:
        print(f"Error executing command: {e}")
        return 1
    finally:
        # Restore original sys.argv
        sys.argv = original_argv

def main():
    print("Process started...")
    
    # First command
    print("Running first command: Writing image file...")
    result1 = run_main_with_args(["--write-image-file", "cat.png"])
    if result1 != 0:
        print("Error executing first command")
        return 1
    
    # Second command
    print("Running second command: Choosing image...")
    result2 = run_main_with_args(["--choose-image", "cat.raw"])
    if result2 != 0:
        print("Error executing second command")
        return 1
    
    print("All commands executed successfully!")
    return 0

if __name__ == "__main__":
    exitcode = main()
    if exitcode != 0:
        print("Process completed with errors.")
    else:
        print("Process completed successfully.")
    input("Press Enter to exit...")
    sys.exit(exitcode)
'''
    
    with open(combined_script, "w") as f:
        f.write(script_content)
    
    return combined_script

# Build with Nuitka
def build_with_nuitka(script_path):
    try:
        # Install Nuitka if not already installed
        install_package("nuitka")
        
        # Copy necessary files to temp directory
        for file in ["main.py", "cat.png", "cat.raw"]:
            if os.path.exists(file):
                shutil.copy(file, os.path.join(temp_dir, file))
            else:
                print(f"Warning: Could not find {file}, you may need to provide it manually.")
                with open(os.path.join(temp_dir, file), "w") as f:
                    f.write(f"# Placeholder for {file}")
        
        # Build command
        build_cmd = [
            sys.executable, "-m", "nuitka",
            "--standalone",
            "--follow-imports",
            "--include-plugin-directory=.",
            f"--include-data-files={os.path.join(temp_dir, 'main.py')}=main.py",
            f"--include-data-files={os.path.join(temp_dir, 'cat.png')}=cat.png",
            f"--include-data-files={os.path.join(temp_dir, 'cat.raw')}=cat.raw",
            script_path
        ]
        
        # Run the build
        print("Building with Nuitka (this may take a few minutes)...")
        subprocess.run(build_cmd, check=True)
        
        # Find the output directory
        output_dir = os.path.join(os.path.dirname(script_path), "combined.dist")
        if os.path.exists(output_dir):
            # Move the output to the current directory
            dest_dir = os.path.join(os.getcwd(), "CatProcessor")
            if os.path.exists(dest_dir):
                shutil.rmtree(dest_dir)
            shutil.move(output_dir, dest_dir)
            print(f"\nBuild successful! Your executable is in the {dest_dir} folder.")
        else:
            print("\nCould not find output directory. Check the Nuitka output for details.")
        
    except subprocess.SubprocessError as e:
        print(f"Error during build: {e}")
        return False
    
    return True

# Main process
if __name__ == "__main__":
    if not check_python():
        print("Error: Python is not available.")
        input("Press Enter to exit...")
        sys.exit(1)
    
    try:
        combined_script = create_combined_script()
        success = build_with_nuitka(combined_script)
        
        if success:
            print("\nYou can now distribute the executable and all files in the CatProcessor folder.")
        else:
            print("\nBuild failed. Please check the error messages above.")
    
    finally:
        # Clean up
        print(f"Cleaning up temporary files in {temp_dir}")
        try:
            shutil.rmtree(temp_dir)
        except:
            print(f"Warning: Could not remove temporary directory {temp_dir}")
        
    input("\nPress Enter to exit...")
