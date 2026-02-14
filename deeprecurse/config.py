"""Configuration constants for DeepRecurse."""

# Modal Volume
VOLUME_NAME = "deeprecurse-transcripts"
MOUNT_PATH = "/transcripts"

# Models
ROOT_MODEL = "gpt-5"
RECURSIVE_MODEL = "gpt-5-nano"

# RLM
MAX_ITERATIONS = 10

# Modal
MODAL_APP_NAME = "deeprecurse"
MODAL_SECRET_NAME = "openai-secret"  # must contain OPENAI_API_KEY
MODAL_IMAGE_PYTHON = "3.12"
