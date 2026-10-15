import sys

try:
    import numpy, scipy, cv2
    import PIL
    assert tuple(map(int, PIL.__version__.split(".")[:3])) >= (12, 1, 1)
except (ImportError, AssertionError):
    sys.exit(1)
