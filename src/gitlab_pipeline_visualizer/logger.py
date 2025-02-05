import logging
import sys


def setup_logging(verbose):
    logger = logging.getLogger("GitLabPipelineVisualizer")
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if verbose >= 1:
        logger.setLevel(logging.INFO)
    if verbose >= 2:
        logger.setLevel(logging.DEBUG)
    return logger
