"""patch_finder — find the Lustre patch that introduced a Maloo test regression.

The heavy lifting is data-mining Maloo's existing per-patch results, so most
runs need no rebuild at all.  See the README for the full method.
"""

__version__ = "0.1.0"
