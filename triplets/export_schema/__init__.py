from pathlib import Path
import logging
import re

# Configure logging
logger = logging.getLogger(__name__)

# Get the directory path of the configuration files
config_directory = Path(__file__).resolve().parent

# Class to store schema paths
class Schemas:
    pass

# Function to sanitize names for valid Python attributes
def sanitize_name(name):
    # Replace invalid characters (e.g., dots, spaces, hyphens) with underscores
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    # Ensure it doesn't start with a number
    if sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized

# Initialize schemas object
schemas = Schemas()

# Recursively search for schema files, skipping the top-level parent
def load_schema_files(start_path):
    # Get relative path components, skipping the top-level directory
    for path in start_path.rglob("*"):
        if path.is_file():
            # Skip Python internal files (e.g., __pycache__) and non-schema files
            if "__" in path.stem or path.suffix == ".pyc":
                continue
            logger.debug(f"Found schema file: {path.resolve()}")

            # Get relative path components starting from the first subdirectory
            relative_path = path.relative_to(start_path)
            parts = relative_path.parts[:-1]  # Exclude the file name
            file_name = sanitize_name(relative_path.stem)

            # Navigate or create nested schema objects
            current = schemas
            for part in parts:
                part_sanitized = sanitize_name(part)
                if not hasattr(current, part_sanitized):
                    setattr(current, part_sanitized, Schemas())
                current = getattr(current, part_sanitized)

            # Set the file path as an attribute
            if hasattr(current, file_name):
                logger.warning(f"Schema name conflict: {file_name} in {path.parent}. Overwriting with {path.resolve()}")
            setattr(current, file_name, path.resolve())


# Load schema files starting from config_directory
load_schema_files(config_directory)

__all__ = ["schemas"]